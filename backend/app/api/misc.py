"""杂项路由：登录 / 设置 / Agent 日志。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import admin_only, create_token, current_user, verify_password
from ..db import get_session
from ..models import AgentRun, SettingsKV, User

router = APIRouter(tags=["misc"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, session: Session = Depends(get_session)):
    user = session.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {
        "token": create_token(user),
        "role": user.role,
        "display_name": user.display_name or user.username,
    }


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:4]}****{value[-4:]}" if len(value) > 8 else "****"


@router.get("/settings")
def get_settings(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    amap = _get(session, "amap_key")
    zhil = _get(session, "zhiliaoke_key")
    try:
        backends = __import__("json").loads(_get(session, "agent_backends") or "{}")
    except Exception:
        backends = {}
    return {
        "amap_key_masked": _mask(amap),
        "zhiliaoke_key_masked": _mask(zhil),
        "agent_backends": backends or {"signal_parser": "rule", "icp_judge": "rule"},
    }


def _get(session: Session, key: str) -> str | None:
    row = session.get(SettingsKV, key)
    return row.value if row else None


class SettingsPut(BaseModel):
    amap_key: str | None = None
    zhiliaoke_key: str | None = None


@router.put("/settings")
def put_settings(
    body: SettingsPut,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    for key, value in (("amap_key", body.amap_key), ("zhiliaoke_key", body.zhiliaoke_key)):
        if value:
            row = session.get(SettingsKV, key)
            if row is None:
                session.add(SettingsKV(key=key, value=value))
            else:
                row.value = value
    session.commit()
    return {"ok": True}


@router.get("/agent-runs")
def list_agent_runs(
    agent_name: str | None = None,
    task_id: str | None = None,
    page: int = 1,
    page_size: int = 15,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    stmt = select(AgentRun)
    if agent_name:
        stmt = stmt.where(AgentRun.agent_name == agent_name)
    if task_id:
        stmt = stmt.where(AgentRun.task_id == task_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(AgentRun.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "agent_name": r.agent_name,
                "backend": r.backend,
                "input_json": r.input_json,
                "output_json": r.output_json,
                "decision_summary": r.decision_summary,
                "duration_ms": r.duration_ms,
                "task_id": r.task_id,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
