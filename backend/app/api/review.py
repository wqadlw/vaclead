"""归一复核队列路由（SPEC-B §4）：疑似重复企业的人工裁决。

merge 语义：candidate 的别名与信号全部迁到 target，删除 candidate；
迁移后重算双方 intent 并触发 Meili 同步（SQLite 为真源）。
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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


# ---------------------------------------------------------------------------
# SPEC-CA 查重治理：确定性自动拒绝 + 对照表导出 + 批量回写
# ---------------------------------------------------------------------------
_STORE_RE = re.compile(r"[（(]([^()（）]*店)[)）]\s*$")


@router.post("/tasks/review-auto-dismiss")
def review_auto_dismiss(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """确定性自动拒绝（SPEC-CA CA1）：唯一安全规则 poi_store。

    两侧括号内容都以"店"结尾 = POI 按门店建档的设计行为——不同店是
    不同到访点，合并即错。其余子群一律留人工（去括号同名≠同一主体：
    安世(中国)/友嘉(绍兴)是独立法人——用数据否决过更激进的规则）。
    """
    rows = session.query(ReviewQueue).filter(ReviewQueue.status == "pending").all()
    dismissed = 0
    for rq in rows:
        c = session.get(Enterprise, rq.candidate_id) if rq.candidate_id else None
        t = session.get(Enterprise, rq.target_id)
        if c is None or t is None:
            continue
        mc, mt = _STORE_RE.search(c.name or ""), _STORE_RE.search(t.name or "")
        if mc and mt:
            rq.status = "rejected"
            rq.reason = "auto_poi_store"
            rq.updated_at = utcnow()
            dismissed += 1
    session.commit()
    return {"scanned": len(rows), "dismissed": dismissed}


@router.get("/review-queue/export")
def export_review_queue(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """复核对照表 CSV（SPEC-CA CA2）：线下 Excel 快过，标好贴回 bulk。"""
    import csv as _csv
    import io as _io

    from fastapi.responses import StreamingResponse

    from sqlalchemy.orm import aliased

    cand_e = aliased(Enterprise)
    tgt_e = aliased(Enterprise)
    rows = (
        session.query(ReviewQueue, cand_e, tgt_e)
        .join(cand_e, cand_e.id == ReviewQueue.candidate_id)
        .join(tgt_e, tgt_e.id == ReviewQueue.target_id)
        .filter(ReviewQueue.status == "pending")
        .limit(20000)
        .all()
    )
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["rid", "score", "candidate", "candidate_source", "target", "target_source", "decision"])
    for rq, c, t in rows:
        w.writerow([rq.id, rq.score, c.name, c.source, t.name, t.source, ""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=review_queue.csv"},
    )


class BulkReviewIn(BaseModel):
    items: list[dict]


@router.post("/review-queue/bulk")
def bulk_review(
    body: BulkReviewIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """批量回写（SPEC-CA CA2）：逐条复用既有 merge/reject 语义。

    Excel 对照表标记后粘贴回写；返回逐条成败（一条失败不中断整批）。
    """
    ok = 0
    errors: list[dict] = []
    for item in body.items[:1000]:
        rid = int(item.get("id") or 0)
        action = str(item.get("action") or "")
        if action not in ("merge", "reject"):
            errors.append({"id": rid, "error": "非法 action"})
            continue
        try:
            if action == "merge":
                merge_review(rid=rid, session=session, user=user)
            else:
                reject_review(rid=rid, session=session, user=user)
            ok += 1
        except HTTPException as exc:
            session.rollback()
            errors.append({"id": rid, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            errors.append({"id": rid, "error": str(exc)})
    return {"ok": ok, "failed": len(errors), "errors": errors[:20]}
