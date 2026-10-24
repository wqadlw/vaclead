"""vaclead FastAPI 入口：路由装配 / CORS / 定时任务 / 工具注入。"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
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

        scheduler = BackgroundScheduler()
        scheduler.add_job(_recycle_leads, "cron", hour=2, minute=0)
        scheduler.start()
    except Exception:
        pass  # APScheduler 不可用时跳过定时任务（不影响主功能）
    yield


app = FastAPI(title="vaclead", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(misc.router, prefix="/api")
app.include_router(enterprises.router, prefix="/api")
app.include_router(leads.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")


@app.get("/")
def root():
    return {"app": "vaclead", "docs": "/docs"}
