"""企业相关路由：筛选列表 / 详情 / 转线索 / 工作台。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import current_user
from ..api.leads import _claim_guard
from ..db import get_session
from ..models import Enterprise, Lead, Signal, User, utcnow

router = APIRouter(tags=["enterprises"])


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def fts_search_ids(session: Session, q: str, limit: int = 2000) -> list[int]:
    """检索企业：先 FTS5（参数化 MATCH），FTS 对中文分词不友好时退回 LIKE 子串匹配。"""
    tokens = [t.replace('"', "").replace("%", "").strip() for t in q.split()]
    tokens = [t for t in tokens if t][:5]
    if not tokens:
        return []
    match_expr = " ".join(f'"{t}"' for t in tokens)
    rows = session.execute(
        text("SELECT enterprise_id FROM enterprise_fts WHERE enterprise_fts MATCH :m LIMIT :lim"),
        {"m": match_expr, "lim": limit},
    ).fetchall()
    ids = [int(r[0]) for r in rows]
    if ids:
        return ids
    # LIKE 兜底（参数化，% 通配符已从词项剥离）
    like = f"%{tokens[0]}%"
    rows = session.execute(
        text(
            "SELECT id FROM enterprise WHERE name LIKE :p1 OR business_scope LIKE :p2 "
            "OR address LIKE :p3 OR industry_name LIKE :p4 LIMIT :lim"
        ),
        {"p1": like, "p2": like, "p3": like, "p4": like, "lim": limit},
    ).fetchall()
    return [int(r[0]) for r in rows]


@router.get("/enterprises")
def list_enterprises(
    q: str | None = None,
    industry_key: str | None = None,
    province: str | None = None,
    city: str | None = None,
    district: str | None = None,
    capital_min: float | None = None,
    capital_max: float | None = None,
    signal_days: int | None = None,
    score_min: float | None = None,
    is_competitor: int = 0,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    stmt = select(Enterprise)
    if industry_key:
        stmt = stmt.where(Enterprise.industry_key == industry_key)
    if province:
        stmt = stmt.where(Enterprise.province == province)
    if city:
        stmt = stmt.where(Enterprise.city == city)
    if district:
        stmt = stmt.where(Enterprise.district == district)
    if capital_min is not None:
        stmt = stmt.where(Enterprise.registered_capital_wan >= capital_min)
    if capital_max is not None:
        stmt = stmt.where(Enterprise.registered_capital_wan <= capital_max)
    if score_min is not None:
        stmt = stmt.where(Enterprise.icp_score >= score_min)
    if is_competitor in (0, 1):
        stmt = stmt.where(Enterprise.is_competitor == is_competitor)
    if signal_days:
        cutoff = _iso_days_ago(signal_days)
        stmt = stmt.where(
            Enterprise.id.in_(select(Signal.enterprise_id).where(Signal.created_at >= cutoff))
        )
    if q:
        ids = fts_search_ids(session, q)
        stmt = stmt.where(Enterprise.id.in_(ids if ids else [-1]))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Enterprise.icp_score.desc(), Enterprise.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    ent_ids = [e.id for e in rows]
    signal_counts: dict[int, int] = {}
    if ent_ids:
        for eid, cnt in session.execute(
            select(Signal.enterprise_id, func.count())
            .where(Signal.enterprise_id.in_(ent_ids))
            .group_by(Signal.enterprise_id)
        ).all():
            signal_counts[eid] = cnt

    items = []
    for e in rows:
        items.append(
            {
                "id": e.id,
                "name": e.name,
                "province": e.province,
                "city": e.city,
                "district": e.district,
                "address": e.address,
                "industry_key": e.industry_key,
                "industry_name": e.industry_name,
                "business_scope": e.business_scope,
                "registered_capital_wan": e.registered_capital_wan,
                "icp_score": e.icp_score,
                "icp_reasons": e.reasons_list(),
                "intent_score": e.intent_score,
                "is_competitor": e.is_competitor,
                "signal_count": signal_counts.get(e.id, 0),
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/enterprises/{eid}")
def enterprise_detail(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    signals = (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid)
        .order_by(Signal.pub_date.desc(), Signal.id.desc())
        .all()
    )
    return {
        "id": e.id,
        "name": e.name,
        "uscc": e.uscc,
        "province": e.province,
        "city": e.city,
        "district": e.district,
        "address": e.address,
        "industry_key": e.industry_key,
        "industry_name": e.industry_name,
        "business_scope": e.business_scope,
        "registered_capital_wan": e.registered_capital_wan,
        "status": e.status,
        "source": e.source,
        "icp_score": e.icp_score,
        "icp_reasons": e.reasons_list(),
        "intent_score": e.intent_score,
        "is_competitor": e.is_competitor,
        "signals": [
            {
                "id": s.id,
                "type": s.type,
                "title": s.title,
                "url": s.url,
                "pub_date": s.pub_date,
                "matched_terms": s.terms_list(),
                "hit_part": s.hit_part,
                "raw_excerpt": s.raw_excerpt,
                "evidence_claim": s.evidence_claim,
                "base_score": s.base_score,
            }
            for s in signals
        ],
    }


class LeadCreate(BaseModel):
    pass


@router.post("/enterprises/{eid}/lead", status_code=201)
def enterprise_to_lead(
    eid: int,
    body: LeadCreate | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    if e.is_competitor:
        raise HTTPException(422, "疑似同行企业，不允许转为线索")
    existing = session.query(Lead).filter(Lead.enterprise_id == eid).first()
    if existing and existing.status != "pool":
        raise HTTPException(422, "撞单：该企业已是别人的线索")
    # 新领取与公海重领都受公海规则约束
    _claim_guard(session, user)
    if existing:  # 公海重新领取
        existing.status = "following"
        existing.owner_id = user.id
        existing.updated_at = utcnow()
        lead = existing
    else:
        composite = e.icp_score * 0.4 + e.intent_score * 0.6
        grade = (
            "S" if composite >= 80 else "A" if composite >= 60 else "B" if composite >= 40 else None
        )
        lead = Lead(
            enterprise_id=eid,
            status="following",
            owner_id=user.id,
            grade=grade,
            claimed_at=utcnow(),
            tenant_id=e.tenant_id,
        )
        session.add(lead)
    session.commit()
    return {"id": lead.id, "enterprise_id": eid, "status": lead.status, "grade": lead.grade}


@router.get("/dashboard")
def dashboard(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    total_enterprises = session.scalar(select(func.count()).select_from(Enterprise)) or 0
    total_signals = session.scalar(select(func.count()).select_from(Signal)) or 0
    signals_7d = (
        session.scalar(
            select(func.count()).select_from(Signal).where(Signal.created_at >= _iso_days_ago(7))
        )
        or 0
    )
    my_leads = {
        st: cnt
        for st, cnt in session.execute(
            select(Lead.status, func.count())
            .where(Lead.owner_id == user.id)
            .group_by(Lead.status)
        ).all()
    }

    pending = (
        session.query(Lead, Enterprise)
        .join(Enterprise, Lead.enterprise_id == Enterprise.id)
        .filter(Lead.owner_id == user.id, Lead.status.in_(["following", "intent"]))
        .order_by(Lead.next_time.asc().nullslast(), Lead.last_followed_at.desc())
        .limit(50)
        .all()
    )
    pending_list = [
        {
            "lead_id": lead.id,
            "enterprise_name": ent.name,
            "next_action": lead.next_action,
            "next_time": lead.next_time,
        }
        for lead, ent in pending
    ]

    recent_rows = (
        session.query(Signal, Enterprise)
        .join(Enterprise, Signal.enterprise_id == Enterprise.id)
        .order_by(Signal.pub_date.desc(), Signal.id.desc())
        .limit(5)
        .all()
    )
    recent_signals = [
        {
            "signal_id": s.id,
            "title": s.title,
            "enterprise_id": e.id,
            "enterprise_name": e.name,
            "pub_date": s.pub_date,
            "base_score": s.base_score,
        }
        for s, e in recent_rows
    ]
    return {
        "total_enterprises": total_enterprises,
        "total_signals": total_signals,
        "signals_7d": signals_7d,
        "my_leads_by_status": my_leads,
        "pending_followups": pending_list,
        "recent_signals": recent_signals,
    }
