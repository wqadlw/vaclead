"""vaclead FastAPI 入口：路由装配 / CORS / 定时任务 / 工具注入。"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .api import enterprises, imports, leads, misc, tasks
from .db import SessionLocal, init_db
from .models import Enterprise, Lead, TaskCheckpoint, utcnow


def _recycle_leads() -> None:
    """公海回收：超期未跟进的线索退回公海（每晚定时，规则读 pool_setting）。"""
    from datetime import timedelta

    from .models import PoolSetting

    with SessionLocal() as s:
        pool = s.query(PoolSetting).filter(PoolSetting.active == 1).first()
        days = pool.recycle_days if pool else 30
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        stale = (
            s.query(Lead)
            .filter(
                Lead.status.in_(["following", "intent"]),
                Lead.last_followed_at.is_not(None),
                Lead.last_followed_at < cutoff,
            )
            .all()
        )
        # 领取后从未跟进且超期
        never_followed = (
            s.query(Lead)
            .filter(
                Lead.status.in_(["following", "intent"]),
                Lead.last_followed_at.is_(None),
                Lead.claimed_at.is_not(None),
                Lead.claimed_at < cutoff,
            )
            .all()
        )
        for lead in set(stale) | set(never_followed):
            lead.status = "pool"
            lead.owner_id = None
            lead.updated_at = utcnow()
        s.commit()


def _inject_tools() -> None:
    """把平台能力注入 Agent 框架的工具注册表（框架未就绪时静默跳过）。"""
    try:
        from .agents.tools import TOOL_REGISTRY
    except Exception:
        return

    def search_enterprises(payload: dict) -> dict:
        with SessionLocal() as s:
            q = s.query(Enterprise)
            if payload.get("industry_key"):
                q = q.filter(Enterprise.industry_key == payload["industry_key"])
            rows = q.order_by(Enterprise.icp_score.desc()).limit(payload.get("limit", 20)).all()
            return {
                "items": [
                    {"id": e.id, "name": e.name, "icp_score": e.icp_score} for e in rows
                ]
            }

    def create_lead(payload: dict) -> dict:
        with SessionLocal() as s:
            ent = s.get(Enterprise, payload.get("enterprise_id", 0))
            if ent is None:
                return {"error": "enterprise_not_found"}
            existing = s.query(Lead).filter(Lead.enterprise_id == ent.id).first()
            if existing:
                return {"lead_id": existing.id, "existed": True}
            lead = Lead(enterprise_id=ent.id, status="new", tenant_id=ent.tenant_id)
            s.add(lead)
            s.commit()
            return {"lead_id": lead.id, "existed": False}

    for name, fn in (
        ("search_enterprises", search_enterprises),
        ("create_lead", create_lead),
    ):
        if TOOL_REGISTRY.has(name):
            TOOL_REGISTRY.register_function(name, fn)


def _recover_interrupted_tasks() -> None:
    """启动恢复（学自 AI_Find_Customer recover_interrupted_running_jobs）：
    把上次进程退出时残留的 running 任务标记为 error，避免永远卡住。"""
    with SessionLocal() as s:
        stale = s.query(TaskCheckpoint).filter(TaskCheckpoint.status == "running").all()
        for cp in stale:
            cp.status = "error"
            cp.state_json = json.dumps(
                {"error": "进程重启导致任务中断，请重新发起"},
                ensure_ascii=False,
            )
            cp.updated_at = utcnow()
        s.commit()


def _run_job_with_watermark(job_name: str, fn, job_kwargs: dict | None = None) -> None:
    """定时任务统一包装：执行 + 写 sync_watermark（失败落 failed 不静默，SPEC-B §6）。"""
    from .jobs import mark_watermark

    try:
        stats = fn(**(job_kwargs or {}))
        with SessionLocal() as s:
            mark_watermark(s, job_name, "ok", stats if stats is not None else {})
    except Exception as exc:  # noqa: BLE001 — 定时任务兜底：失败必须落水位
        import logging

        logging.getLogger("vaclead.jobs").error(
            "定时任务 %s 失败", job_name, exc_info=True
        )
        with SessionLocal() as s:
            mark_watermark(s, job_name, "failed", {"error": str(exc)})


def _cron_signature_ok(fn, job_kwargs: dict | None) -> bool:
    """调度注册防呆（SPEC-I I0）：fn 必须能用注册的 kwargs 调起。

    曾有 refresh_all_stats(session) 漏传 job_kwargs 被无参调用，任务静默 failed
    三天才被发现——注册前 bind 校验，不匹配的直接不注册并响亮告警。
    """
    import inspect

    try:
        inspect.signature(fn).bind(**(job_kwargs or {}))
        return True
    except TypeError:
        return False


def _stale_incremental_hours() -> float | None:
    """上次增量距今小时数；从未跑过返回 None（SPEC-G G4：>26h 启动补跑）。"""
    from datetime import datetime, timezone

    from .models import SyncWatermark

    with SessionLocal() as s:
        row = s.get(SyncWatermark, "daily_incremental")
    if row is None or not row.last_run_at:
        return None
    try:
        last = datetime.fromisoformat(row.last_run_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except ValueError:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _inject_tools()
    _recover_interrupted_tasks()
    # 日志按天滚动（I6 P2）：data/logs/vaclead.log，保留 14 天
    from .logrotate import setup_logging

    setup_logging()
    # 审计钩子（SPEC-J J1）：update/delete 落 audit_log
    from .audit import install as _install_audit

    _install_audit()
    # 记录主事件循环：采集线程用它把 SSE 事件推回订阅者
    import asyncio

    from .api.tasks import set_main_loop

    set_main_loop(asyncio.get_running_loop())

    # 测试/E2E 环境禁用全部后台任务（G4 补跑会对着真网络跑增量并持写事务，
    # 与用例并发写库 → database is locked，SPEC-I I0c）
    _jobs_disabled = os.environ.get("VACLEAD_DISABLE_JOBS") == "1"

    # SPEC-G G4：进程不常开——启动时若上次增量>26h（或从未跑过且有数据）自动后台补一次
    if not _jobs_disabled:
        try:
            from .db import SessionLocal as _SL0
            from .models import Enterprise as _Ent

            with _SL0() as _s0:
                _has_data = _s0.query(_Ent).count() > 0
            _hours = _stale_incremental_hours()
            if _has_data and (_hours is None or _hours > 26):
                import threading as _th

                from .jobs import run_daily_incremental

                def _catchup() -> None:
                    _run_job_with_watermark("daily_incremental", run_daily_incremental)

                _th.Thread(target=_catchup, daemon=True).start()
                import logging as _lg

                _lg.getLogger("vaclead.jobs").info(
                    "启动补跑增量（距上次 %s 小时）", "从未" if _hours is None else round(_hours)
                )
        except Exception:  # noqa: BLE001 — 补跑失败不影响启动
            import logging

            logging.getLogger("vaclead.jobs").warning("启动补增量检查失败", exc_info=True)

    try:
        import logging

        from apscheduler.schedulers.background import BackgroundScheduler

        from .db import DATA_DIR as _DATA_DIR
        from .jobs import backup_sqlite, refresh_all_stats, run_daily_incremental

        scheduler = BackgroundScheduler()

        def _register_cron(job_name: str, fn, hour: int, minute: int,
                           job_kwargs: dict | None = None) -> None:
            """统一 cron 注册（SPEC-I I0）：签名防呆 + 失败落水位 + 防重入。

            注册前 bind 校验拦下参数笔误（曾有 refresh_all_stats 漏传 session
            静默 failed 三天）；misfire 宽限/合并/单实例为每日任务标准加固。
            """
            if _jobs_disabled:
                logging.getLogger("vaclead.jobs").info(
                    "VACLEAD_DISABLE_JOBS=1：跳过注册定时任务 %s（测试环境）", job_name
                )
                return
            if not _cron_signature_ok(fn, job_kwargs):
                logging.getLogger("vaclead.jobs").error(
                    "定时任务 %s 注册签名不匹配（fn=%r kwargs=%r），已跳过——请修复注册参数",
                    job_name, fn, job_kwargs,
                )
                return
            scheduler.add_job(
                _run_job_with_watermark, "cron", hour=hour, minute=minute,
                args=[job_name, fn, job_kwargs],
                misfire_grace_time=3600, coalesce=True, max_instances=1,
            )

        # SPEC-B §6：全部定时任务统一走水位包装（公海回收 → 增量采集 → 画像 → 备份串行错峰）
        _register_cron("recycle_leads", _recycle_leads, 2, 0)
        _register_cron("daily_incremental", run_daily_incremental, 3, 30)

        def _refresh_stats_sweep() -> dict:
            from .db import SessionLocal as _SL

            with _SL() as s:
                return refresh_all_stats(s)

        _register_cron("refresh_enterprise_stats", _refresh_stats_sweep, 4, 0)

        def _notify_sweep() -> dict:
            from .jobs import generate_notifications
            from .db import SessionLocal as _SL

            with _SL() as s:
                return generate_notifications(s)

        _register_cron("notify_sweep", _notify_sweep, 4, 5)

        def _digest_flush() -> dict:
            from .notify.channels import digest_flush
            from .db import SessionLocal as _SL

            with _SL() as s:
                return digest_flush(s)

        _register_cron("digest_flush", _digest_flush, 8, 5)  # SPEC-G：静默期聚合出舱

        def _automation_sweep() -> dict:
            from .jobs import run_automation_rules
            from .db import SessionLocal as _SL

            with _SL() as s:
                return run_automation_rules(s)

        _register_cron("automation_sweep", _automation_sweep, 4, 7)

        def _notice_contacts_daily() -> dict:
            # 直接复用端点逻辑（内部限速/幂等/限流即停），免 HTTP 依赖。
            # 曾写成 "..api.tasks" 越顶层导入（SPEC-I I0）：每日 04:08 必 ImportError
            # 且不走水位静默失败。
            from .api.tasks import backfill_notice_contacts as _fn

            from .db import SessionLocal as _SL

            with _SL() as s:
                result = _fn(type("B", (), {"limit": 30})(), s, None)
            return result if isinstance(result, dict) else {}

        _register_cron("notice_contacts", _notice_contacts_daily, 4, 8)
        _register_cron("backup_sqlite", backup_sqlite, 4, 10,
                       {"backup_dir": str(_DATA_DIR / "backups")})

        def _subscription_rules_sweep() -> dict:
            from .db import SessionLocal as _SL
            from .jobs import run_subscription_rules

            with _SL() as s:
                return run_subscription_rules(s)

        _register_cron("subscription_rules", _subscription_rules_sweep, 4, 6)

        def _audit_cleanup_sweep() -> dict:
            from .db import SessionLocal as _SL
            from .jobs import audit_cleanup

            with _SL() as s:
                return audit_cleanup(s)

        _register_cron("audit_cleanup", _audit_cleanup_sweep, 4, 12)

        # SPEC-J J4：触达调度——工作时段两次（收件同步 + 预算内发送）
        def _outreach_dispatch_sweep() -> dict:
            from .db import SessionLocal as _SL
            from .outreach.dispatcher import run_outreach_dispatch

            with _SL() as s:
                return run_outreach_dispatch(s)

        _register_cron("outreach_dispatch", _outreach_dispatch_sweep, 9, 30)
        _register_cron("outreach_dispatch", _outreach_dispatch_sweep, 14, 30)
        scheduler.start()
    except Exception:  # noqa: BLE001 — APScheduler 不可用时跳过定时任务（不影响主功能）
        import logging

        logging.getLogger("vaclead.jobs").warning("调度器启动失败，定时任务停摆", exc_info=True)
    yield


app = FastAPI(title="vaclead", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- 可观测（企业级）：请求访问日志 + 慢请求标记 + 全局异常兜底 ----
@app.middleware("http")
async def access_log_middleware(request, call_next):
    import logging
    import time as _time

    start = _time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger("vaclead.http").exception(
            "未处理异常 %s %s", request.method, request.url.path
        )
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "服务器内部错误，详见服务端日志"}, status_code=500)
    cost_ms = (_time.monotonic() - start) * 1000
    if request.url.path.startswith("/api/") and cost_ms > 1500:
        logging.getLogger("vaclead.http").warning(
            "慢请求 %s %s %.0fms", request.method, request.url.path, cost_ms
        )
    response.headers["X-Process-Time-Ms"] = f"{cost_ms:.0f}"
    return response

app.include_router(misc.router, prefix="/api")
app.include_router(enterprises.router, prefix="/api")
app.include_router(leads.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
from .api import review  # noqa: E402

app.include_router(review.router, prefix="/api")
from .api import intel  # noqa: E402

app.include_router(intel.router, prefix="/api")
from .api import automation  # noqa: E402

app.include_router(automation.router, prefix="/api")
from .api import todos  # noqa: E402

app.include_router(todos.router, prefix="/api")
from .api import analytics  # noqa: E402

app.include_router(analytics.router, prefix="/api")
from .api import governance  # noqa: E402

app.include_router(governance.router, prefix="/api")
from .api import quotes  # noqa: E402

app.include_router(quotes.router, prefix="/api")
from .api import leadforms  # noqa: E402

app.include_router(leadforms.router, prefix="/api")
from .api import outreach  # noqa: E402

app.include_router(outreach.router, prefix="/api")
from .api import signals  # noqa: E402

app.include_router(signals.router, prefix="/api")


# ---- 单进程部署：托管前端静态导出（frontend/out），SPA 回退 ----
from pathlib import Path as _Path

_STATIC_DIR = _Path(__file__).resolve().parents[2] / "frontend" / "out"
if _STATIC_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app.mount("/_next", StaticFiles(directory=_STATIC_DIR / "_next"), name="next-static")

    @app.get("/", include_in_schema=False)
    async def _spa_index():
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        if full_path.startswith(("api/", "docs", "openapi.json")):
            raise HTTPException(404, "Not Found")
        candidate = _STATIC_DIR / f"{full_path}.html"
        if candidate.is_file():
            return FileResponse(candidate)
        candidate = _STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")  # SPA 客户端路由回退
else:

    @app.get("/")
    def root():
        return {"app": "vaclead", "docs": "/docs"}
