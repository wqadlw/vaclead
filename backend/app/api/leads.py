"""线索路由：列表 / 状态流转 / 跟进记录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user, visible_owner_ids
from ..db import get_session
from ..models import (
    Enterprise,
    Followup,
    Signal,
    LEAD_LOST_REASONS,
    LEAD_STAGE_PROBABILITY,
    LEAD_TRANSITIONS,
    Lead,
    User,
    utcnow,
)

router = APIRouter(tags=["leads"])


def _lead_dict(lead: Lead, ent: Enterprise | None) -> dict:
    return {
        "id": lead.id,
        "enterprise_id": lead.enterprise_id,
        "status": lead.status,
        "owner_id": lead.owner_id,
        "grade": lead.grade,
        "lost_reason": lead.lost_reason,
        "last_followed_at": lead.last_followed_at,
        "next_action": lead.next_action,
        "next_time": lead.next_time,
        "amount_wan": lead.amount_wan,
        "probability": lead.probability,
        "expected_close": lead.expected_close,
        "enterprise": (
            {
                "id": ent.id,
                "name": ent.name,
                "industry_name": ent.industry_name,
                "icp_score": ent.icp_score,
                "intent_score": ent.intent_score,
            }
            if ent
            else None
        ),
    }


@router.get("/leads")
def list_leads(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    stmt = select(Lead)
    if status:
        stmt = stmt.where(Lead.status == status)
    # 数据范围（SPEC-N N2）：admin 全库；销售=自己+公海；团队负责人追加成员线索
    owner_ids = visible_owner_ids(user, session)
    if owner_ids is not None:
        cond = Lead.owner_id.in_(owner_ids) | (Lead.status == "pool")
        stmt = stmt.where(cond)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Lead.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for lead in rows:
        ent = session.get(Enterprise, lead.enterprise_id)
        items.append(_lead_dict(lead, ent))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


class LeadPatch(BaseModel):
    status: str
    lost_reason: str | None = None


def _claim_guard(session: Session, user: User) -> None:
    """领取上限校验：用户持有(following/intent)线索数达到公海规则上限时拒绝领取。"""
    from ..models import PoolSetting

    pool = session.query(PoolSetting).filter(PoolSetting.active == 1).first()
    if pool is None or pool.receive_limit <= 0:
        return
    held = (
        session.query(func.count())
        .select_from(Lead)
        .where(Lead.owner_id == user.id, Lead.status.in_(["following", "intent"]))
        .scalar()
        or 0
    )
    if held >= pool.receive_limit:
        raise HTTPException(
            422, f"已达持有上限：公海规则限制每人 {pool.receive_limit} 条，当前持有 {held} 条"
        )


@router.patch("/leads/{lead_id}")
def patch_lead(
    lead_id: int,
    body: LeadPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "线索不存在")
    # 公海可领取；其余仅负责人或管理员可操作
    if lead.status == "pool" and body.status == "following":
        _claim_guard(session, user)
        lead.owner_id = user.id
        lead.claimed_at = utcnow()
    elif lead.owner_id != user.id and user.role != "admin":
        raise HTTPException(403, "只能操作自己的线索")
    allowed = LEAD_TRANSITIONS.get(lead.status, set())
    if body.status not in allowed:
        raise HTTPException(422, f"不允许从 {lead.status} 流转到 {body.status}")
    lead.status = body.status
    # twenty pipeline 模式：阶段推进即更新默认赢率
    lead.probability = LEAD_STAGE_PROBABILITY.get(body.status, lead.probability)
    if body.status == "lost":
        if body.lost_reason not in LEAD_LOST_REASONS:
            raise HTTPException(422, "请提供合法的放弃原因")
        lead.lost_reason = body.lost_reason
    if body.status == "pool":
        lead.owner_id = None
    if body.status == "following" and lead.owner_id is None:
        lead.owner_id = user.id
        lead.claimed_at = utcnow()
    lead.updated_at = utcnow()
    session.commit()
    return _lead_dict(lead, session.get(Enterprise, lead.enterprise_id))


class OpportunityPatch(BaseModel):
    """商机编辑（吸收 twenty opportunity：amount/closeDate/probability 可独立于阶段修改）。"""
    amount_wan: float | None = None
    probability: int | None = None
    expected_close: str | None = None
    next_action: str | None = None
    next_time: str | None = None


@router.patch("/leads/{lead_id}/opportunity")
def patch_lead_opportunity(
    lead_id: int,
    body: OpportunityPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "线索不存在")
    if lead.owner_id != user.id and user.role != "admin":
        raise HTTPException(403, "只能操作自己的线索")
    if body.amount_wan is not None:
        if body.amount_wan < 0 or body.amount_wan > 1_000_000:
            raise HTTPException(422, "商机金额需在 0-100 亿元之间")
        lead.amount_wan = float(body.amount_wan)
    if body.probability is not None:
        if not 0 <= body.probability <= 100:
            raise HTTPException(422, "赢率需在 0-100 之间")
        lead.probability = int(body.probability)
    if body.expected_close is not None:
        lead.expected_close = body.expected_close[:10] or None
    if body.next_action is not None:
        lead.next_action = body.next_action[:200] or None
    if body.next_time is not None:
        lead.next_time = body.next_time[:19] or None
    lead.updated_at = utcnow()
    session.commit()
    return _lead_dict(lead, session.get(Enterprise, lead.enterprise_id))


@router.get("/leads/{lead_id}/timeline")
def lead_timeline(
    lead_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """统一活动流（吸收 twenty timeline 模块）：跟进记录与命中信号合流为单一时间线。

    两源各取最近 50 条，按时间倒序归并，统一 {type, at, title, body} 结构。
    """
    ent = session.get(Enterprise, session.get(Lead, lead_id).enterprise_id) if session.get(Lead, lead_id) else None
    if ent is None:
        raise HTTPException(404, "线索不存在")
    lead = session.get(Lead, lead_id)
    followups = (
        session.query(Followup)
        .filter(Followup.lead_id == lead_id)
        .order_by(Followup.created_at.desc())
        .limit(50)
        .all()
    )
    signals = (
        session.query(Signal)
        .filter(Signal.enterprise_id == ent.id)
        .order_by(Signal.created_at.desc())
        .limit(50)
        .all()
    )
    items: list[dict] = []
    for f in followups:
        items.append({
            "type": "followup",
            "at": str(f.created_at),
            "title": "跟进",
            "body": f.content,
            "next_action": f.next_action,
            "next_time": f.next_time,
        })
    for sg in signals:
        items.append({
            "type": "signal_" + sg.type,
            "at": str(sg.pub_date or sg.created_at),
            "title": sg.title,
            "body": sg.evidence_claim or sg.raw_excerpt,
        })
    items.sort(key=lambda x: x["at"], reverse=True)
    return {"items": items[:80], "lead_id": lead_id, "enterprise_id": ent.id}


class FollowupCreate(BaseModel):
    content: str
    next_action: str | None = None
    next_time: str | None = None


@router.post("/leads/{lead_id}/followups", status_code=201)
def create_followup(
    lead_id: int,
    body: FollowupCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "线索不存在")
    if lead.owner_id != user.id and user.role != "admin":
        raise HTTPException(403, "只能跟进自己的线索")
    if not body.content.strip():
        raise HTTPException(422, "跟进内容不能为空")
    fu = Followup(
        lead_id=lead_id,
        content=body.content.strip(),
        next_action=body.next_action,
        next_time=body.next_time,
    )
    session.add(fu)
    lead.last_followed_at = utcnow()
    # 冗余到线索行（借鉴 WukongCRM CrmLeads.nextTime），列表/工作台免 join
    lead.next_action = body.next_action
    lead.next_time = body.next_time
    lead.updated_at = utcnow()
    if lead.status == "new":
        lead.status = "following"
    session.commit()
    return {
        "id": fu.id,
        "content": fu.content,
        "next_action": fu.next_action,
        "next_time": fu.next_time,
        "created_at": fu.created_at,
    }


@router.get("/leads/{lead_id}/followups")
def list_followups(
    lead_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "线索不存在")
    rows = (
        session.query(Followup)
        .filter(Followup.lead_id == lead_id)
        .order_by(Followup.created_at.desc())
        .all()
    )
    return [
        {
            "id": f.id,
            "content": f.content,
            "next_action": f.next_action,
            "next_time": f.next_time,
            "created_at": f.created_at,
        }
        for f in rows
    ]
