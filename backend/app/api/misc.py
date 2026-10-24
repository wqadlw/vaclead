"""杂项路由：登录 / 设置 / Agent 日志。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import admin_only, create_token, current_user, verify_password
from ..db import get_session
from ..models import AgentRun, Enterprise, Lead, PoolSetting, SettingsKV, Signal, User

router = APIRouter(tags=["misc"])


@router.get("/health")
def health(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """平台就绪度聚合（学 OpenOutreach `status --json` 契约）：
    onboarding 缺什么、各实体计数、被什么阻塞、建议的下一步。"""
    from ..collectors import amap_poi, tender  # 探测能力位

    amap_key = _get(session, "amap_key")
    zhil_key = _get(session, "zhiliaoke_key")
    counts = {
        "enterprises": session.query(func.count()).select_from(Enterprise).scalar() or 0,
        "signals": session.query(func.count()).select_from(Signal).scalar() or 0,
        "leads": session.query(func.count()).select_from(Lead).scalar() or 0,
    }
    missing: list[str] = []
    if not amap_key:
        missing.append("amap_key")
    if not zhil_key:
        missing.append("zhiliaoke_key")

    # next_action 决策树：按"当前最该做的事"排序
    if counts["enterprises"] == 0:
        next_action = {"action": "import_enterprises", "hint": "在拓客任务页导入企业 CSV 或启动 POI 采集"}
    elif counts["signals"] == 0:
        next_action = {"action": "import_tenders", "hint": "导入招标公告 CSV，命中设备词自动生成意图信号"}
    elif not amap_key:
        next_action = {"action": "configure_amap", "hint": "配置高德 Key 解锁地图拓客"}
    else:
        next_action = {"action": "filter_enterprises", "hint": "去企业筛选页按信号和评分找客户"}

    return {
        "version": "0.3",
        "onboarding_complete": len(missing) == 0,
        "missing_config": missing,
        "capabilities": {
            "poi_quadtree": bool(amap_key),
            "tender_api": bool(zhil_key),
            "tender_csv": True,
            "llm_backends": False,  # P2：配置 GLM Key 后为 True
        },
        "counts": counts,
        "next_action": next_action,
    }


class PoolSettingIn(BaseModel):
    recycle_days: int | None = None
    receive_limit: int | None = None
    remind_days: int | None = None


def _get_pool(session: Session) -> PoolSetting:
    pool = session.query(PoolSetting).filter(PoolSetting.active == 1).first()
    if pool is None:
        pool = PoolSetting(name="默认公海")
        session.add(pool)
        session.commit()
        session.refresh(pool)
    return pool


@router.get("/pool-settings")
def get_pool_settings(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    pool = _get_pool(session)
    return {
        "name": pool.name,
        "recycle_days": pool.recycle_days,
        "receive_limit": pool.receive_limit,
        "remind_days": pool.remind_days,
    }


@router.put("/pool-settings")
def put_pool_settings(
    body: PoolSettingIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    pool = _get_pool(session)
    if body.recycle_days is not None:
        if not 1 <= body.recycle_days <= 365:
            raise HTTPException(422, "回收天数需在 1-365 之间")
        pool.recycle_days = body.recycle_days
    if body.receive_limit is not None:
        if body.receive_limit < 0 or body.receive_limit > 999:
            raise HTTPException(422, "领取上限需在 0-999 之间（0 为不限）")
        pool.receive_limit = body.receive_limit
    if body.remind_days is not None:
        if not 0 <= body.remind_days <= 30:
            raise HTTPException(422, "提醒天数需在 0-30 之间")
        pool.remind_days = body.remind_days
    session.commit()
    return {"ok": True}


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
