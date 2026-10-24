"""vaclead FastAPI 入口：路由装配 / CORS / 定时任务 / 工具注入。"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import enterprises, imports, leads, misc, tasks
from .db import SessionLocal, init_db
from .models import Enterprise, Lead, SettingsKV, utcnow
from .vertical import recycle_days


def _recycle_leads() -> None:
    """公海回收：超期未跟进的线索退回公海（每晚定时）。"""
    from datetime import timedelta

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=recycle_days())
    ).isoformat(timespec="seconds")
    with SessionLocal() as s:
        stale = (
            s.query(Lead)
            .filter(
                Lead.status.in_(["following", "intent"]),
                Lead.last_followed_at.is_not(None),
                Lead.last_followed_at < cutoff,
                (utcnow() > cutoff),
            )
            .all()
        )
        # last_followed_at 早于回收期，或领取后从未跟进且超期
        claimed_cutoff = cutoff
        never_followed = (
            s.query(Lead)
            .filter(
                Lead.status.in_(["following", "intent"]),
                Lead.last_followed_at.is_(None),
                Lead.claimed_at.is_not(None),
                Lead.claimed_at < claimed_cutoff,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _inject_tools()
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
