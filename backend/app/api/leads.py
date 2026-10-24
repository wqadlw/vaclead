"""线索路由：列表 / 状态流转 / 跟进记录。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import (
    Enterprise,
    Followup,
    LEAD_LOST_REASONS,
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
    # 销售只看自己的线索与公海；管理员看全部
    if user.role != "admin":
        stmt = stmt.where((Lead.owner_id == user.id) | (Lead.status == "pool"))
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
        lead.owner_id = user.id
        lead.claimed_at = utcnow()
    elif lead.owner_id != user.id and user.role != "admin":
        raise HTTPException(403, "只能操作自己的线索")
    allowed = LEAD_TRANSITIONS.get(lead.status, set())
    if body.status not in allowed:
        raise HTTPException(422, f"不允许从 {lead.status} 流转到 {body.status}")
    lead.status = body.status
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
