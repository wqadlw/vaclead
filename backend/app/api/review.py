"""归一复核队列路由（SPEC-B §4）：疑似重复企业的人工裁决。

merge 语义：candidate 的别名与信号全部迁到 target，删除 candidate；
迁移后重算双方 intent 并触发 Meili 同步（SQLite 为真源）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import admin_only
from ..db import get_session
from ..models import Enterprise, EnterpriseAlias, ReviewQueue, Signal, User, utcnow

logger = logging.getLogger("vaclead.api.review")

router = APIRouter(tags=["review"])


def _ent_brief(e: Enterprise) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "uscc": e.uscc,
        "source": e.source,
        "province": e.province,
        "city": e.city,
        "signal_count": 0,
    }


@router.get("/review-queue")
def list_review_queue(
    status: str = "pending",
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    if status not in ("pending", "merged", "rejected", "all"):
        raise HTTPException(422, "status 取值：pending/merged/rejected/all")
    stmt = select(ReviewQueue).order_by(ReviewQueue.score.desc(), ReviewQueue.id)
    if status != "all":
        stmt = stmt.where(ReviewQueue.status == status)
    rows = session.scalars(stmt).all()
    ent_ids = {r.candidate_id for r in rows} | {r.target_id for r in rows}
    ents: dict[int, Enterprise] = {}
    if ent_ids:
        ents = {
            e.id: e
            for e in session.query(Enterprise).filter(Enterprise.id.in_(ent_ids)).all()
        }
    items = []
    for r in rows:
        cand = ents.get(r.candidate_id) if r.candidate_id is not None else None
        target = ents.get(r.target_id)
        if r.candidate_id is not None and (cand is None or target is None):
            continue  # 企业已被删除（如手工合并），队列项视为失效
        if r.candidate_id is None and target is None:
            continue
        items.append(
            {
                "id": r.id,
                "score": round(r.score, 1),
                "reason": r.reason,
                "status": r.status,
                "candidate": _ent_brief(cand) if cand is not None else None,
                "target": _ent_brief(target) if target is not None else None,
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/review-queue/{rid}/merge")
def merge_review(
    rid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    r = session.get(ReviewQueue, rid)
    if r is None:
        raise HTTPException(404, "复核项不存在")
    if r.status != "pending":
        raise HTTPException(422, f"复核项状态为 {r.status}，不可合并")
    cand = session.get(Enterprise, r.candidate_id)
    target = session.get(Enterprise, r.target_id)
    if cand is None or target is None:
        r.status = "rejected"  # 一方已不存在，直接作废
        r.updated_at = utcnow()
        session.commit()
        raise HTTPException(422, "候选或目标企业已不存在，复核项已作废")
    if cand.id == target.id:
        r.status = "merged"
        r.updated_at = utcnow()
        session.commit()
        return {"ok": True, "moved_signals": 0, "moved_aliases": 0}

    # 1) 信号迁移
    moved_signals = (
        session.query(Signal)
        .filter(Signal.enterprise_id == cand.id)
        .update({"enterprise_id": target.id}, synchronize_session=False)
    )
    # 2) 别名迁移（跳过与 target 主名重复的）
    moved_aliases = 0
    for alias in session.query(EnterpriseAlias).filter(
        EnterpriseAlias.enterprise_id == cand.id
    ):
        dup = (
            session.query(EnterpriseAlias)
            .filter(
                EnterpriseAlias.enterprise_id == target.id,
                EnterpriseAlias.alias == alias.alias,
            )
            .first()
        )
        if dup is None:
            alias.enterprise_id = target.id
            moved_aliases += 1
        else:
            session.delete(alias)
    # 3) 档案字段补齐（target 缺的从 cand 拿；uscc 冲突时保留 target 的）
    for field in ("uscc", "address", "business_scope", "registered_capital_wan",
                  "province", "city", "district", "industry_key", "industry_name"):
        if getattr(target, field) in (None, "") and getattr(cand, field) not in (None, ""):
            setattr(target, field, getattr(cand, field))
    target.is_competitor = max(target.is_competitor, cand.is_competitor)
    target.updated_at = utcnow()

    # 4) 删除候选（复核记录 candidate_id 被 SET NULL，保留作审计）+ 落账
    session.delete(cand)
    r.status = "merged"
    r.updated_at = utcnow()
    session.commit()

    # 5) 意图重算 + 检索投影（失败不影响主流程）
    from .imports import recompute_intent

    recompute_intent(session, target.id)
    session.commit()
    try:
        from .. import search as meili

        meili.sync_all(session)
    except Exception:  # noqa: BLE001 — 检索层失败不影响合并（SQLite 为真源）
        logger.warning("merge 后 Meili 同步失败，检索投影滞后", exc_info=True)
    return {"ok": True, "moved_signals": moved_signals, "moved_aliases": moved_aliases}


@router.post("/review-queue/{rid}/reject")
def reject_review(
    rid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    r = session.get(ReviewQueue, rid)
    if r is None:
        raise HTTPException(404, "复核项不存在")
    if r.status != "pending":
        raise HTTPException(422, f"复核项状态为 {r.status}，不可驳回")
    r.status = "rejected"
    r.updated_at = utcnow()
    session.commit()
    return {"ok": True}
