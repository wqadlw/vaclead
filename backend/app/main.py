"""vaclead FastAPI 入口：路由装配 / CORS / 定时任务 / 工具注入。"""
from __future__ import annotations

import json
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
            mark_watermark(s, job_name, "ok", stats)
    except Exception as exc:  # noqa: BLE001 — 定时任务兜底：失败必须落水位
        import logging

        logging.getLogger("vaclead.jobs").error(
            "定时任务 %s 失败", job_name, exc_info=True
        )
        with SessionLocal() as s:
            mark_watermark(s, job_name, "failed", {"error": str(exc)})


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _inject_tools()
    _recover_interrupted_tasks()
    # 记录主事件循环：采集线程用它把 SSE 事件推回订阅者
    import asyncio

    from .api.tasks import set_main_loop

    set_main_loop(asyncio.get_running_loop())
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        from .jobs import backup_sqlite, refresh_all_stats, run_daily_incremental
        from .db import DATA_DIR as _DATA_DIR

        scheduler = BackgroundScheduler()
        scheduler.add_job(_recycle_leads, "cron", hour=2, minute=0)
        # SPEC-B §6：每日增量采集 → 画像刷新 → 备份（串行错峰）
        scheduler.add_job(
            _run_job_with_watermark, "cron", hour=3, minute=30,
            args=["daily_incremental", run_daily_incremental],
        )
        scheduler.add_job(
            _run_job_with_watermark, "cron", hour=4, minute=0,
            args=["refresh_enterprise_stats", refresh_all_stats],
        )

        def _notify_sweep() -> dict:
            from .jobs import generate_notifications
            from .db import SessionLocal as _SL

            with _SL() as s:
                return generate_notifications(s)

        scheduler.add_job(_notify_sweep, "cron", hour=4, minute=5)

        scheduler.add_job(_notify_sweep, "cron", hour=4, minute=5)

        def _automation_sweep() -> dict:
            from .jobs import run_automation_rules
            from .db import SessionLocal as _SL

            with _SL() as s:
                return run_automation_rules(s)

        scheduler.add_job(_automation_sweep, "cron", hour=4, minute=7)

        def _notice_contacts_daily() -> dict:
            import time as _t
            from ..api.tasks import backfill_notice_contacts as _fn

            # 直接复用端点逻辑（内部限速/幂等/限流即停），免 HTTP 依赖
            from .db import SessionLocal as _SL

            with _SL() as s:
                result = _fn(type("B", (), {"limit": 30})(), s, None)
            _t.sleep(0)
            return result if isinstance(result, dict) else {}

        scheduler.add_job(_notice_contacts_daily, "cron", hour=4, minute=8)
        scheduler.add_job(
            _run_job_with_watermark, "cron", hour=4, minute=10,
            args=["backup_sqlite", backup_sqlite,
                  {"backup_dir": str(_DATA_DIR / "backups")}],
        )
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
