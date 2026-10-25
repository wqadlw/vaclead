"""企业相关路由：筛选列表 / 详情 / 转线索 / 工作台。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..api.leads import _claim_guard
from ..db import get_session
from ..models import Enterprise, Lead, Signal, User, utcnow
from .. import search as meili

router = APIRouter(tags=["enterprises"])

# 导出用角色中文名（与 intel.py 的 ROLE_CN 同源；独立常量避免跨 API 模块导入）
ROLE_CN_EXPORT: dict[str, str] = {
    "legal_rep": "法定代表人",
    "buyer_unit": "采购单位",
    "tender_agency": "代理机构",
    "采购联系人": "采购联系人",
    "项目联系人": "项目联系人",
    "代理机构": "代理机构",
}


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
    is_competitor: int = -1,
    source: str | None = None,
    has_contact: int = 0,
    page: int = 1,
    page_size: int = 20,
    facets: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    # Quick picks（gmaps-scraper 模式）：has_contact=1 → 只看有可拨打联系人的企业。
    # 联系人是低频写高基数维度，SQLite 侧集合过滤即可（不走 Meili filter）。
    contact_ids: list[int] | None = None
    if has_contact:
        from ..models import Contact

        contact_ids = [
            int(r[0])
            for r in session.query(Contact.enterprise_id).distinct().limit(5000).all()
        ]
        if not contact_ids:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
    # Meili 优先（毫秒级组合筛选+分面）；不可用回退 SQLite LIKE 路径
    if meili.available():
        try:
            r = meili.search(
                q or "",
                industry_key=industry_key,
                province=province,
                city=city,
                capital_min=capital_min,
                capital_max=capital_max,
                score_min=score_min,
                signal_days=signal_days,
                is_competitor=is_competitor,
                source=source,
                page=page,
                page_size=page_size,
                facets=bool(facets),
            )
            if r["items"] and "id" in r["items"][0]:
                ids = [it["id"] for it in r["items"]]
                rows = {e.id: e for e in session.query(Enterprise).filter(Enterprise.id.in_(ids)).all()}
                if contact_ids is not None:
                    allow = set(contact_ids)
                    rows = {eid: e for eid, e in rows.items() if eid in allow}
                items = []
                for it in r["items"]:
                    e = rows.get(it["id"])
                    if e is None:
                        continue
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
                            "source": e.source,
                            "signal_count": it.get("signal_count", 0),
                        }
                    )
                out = {"items": items, "total": r["total"], "page": page, "page_size": page_size}
                if r.get("facets") is not None:
                    out["facets"] = r["facets"]
                return out
        except Exception:  # noqa: BLE001 — Meili 异常一律回退 SQLite
            pass

    stmt = select(Enterprise)
    if contact_ids is not None:
        stmt = stmt.where(Enterprise.id.in_(contact_ids))
    if industry_key:
        stmt = stmt.where(Enterprise.industry_key == industry_key)
    if province:
        stmt = stmt.where(Enterprise.province == province)
    if city:
        stmt = stmt.where(Enterprise.city == city)
    if district:
        stmt = stmt.where(Enterprise.district == district)
    if source:
        # 与 Meili 路径同语义：通道名归并到多个来源值
        channel = {"ggzy": ("ggzy", "ggzy_buyer"), "ccgp": ("ccgp", "ccgp_buyer")}.get(
            source, (source,)
        )
        stmt = stmt.where(Enterprise.source.in_(channel))
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
                "source": e.source,
                "signal_count": signal_counts.get(e.id, 0),
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}



class QuickUpdateIn(BaseModel):
    # registered_capital_wan 数值 | industry_key 行业键 | address 文本
    field: str
    value: float | str | None = None


@router.patch("/enterprises/{eid}/quick-update")
def quick_update_enterprise(
    eid: int,
    body: QuickUpdateIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """行内编辑单字段（吸收 twenty InlineCell：display/edit 双态的保存端点）。

    白名单字段防误改；金额合法区间由前端+后端双校验。
    """
    allowed = {"registered_capital_wan", "industry_key", "address"}
    if body.field not in allowed:
        raise HTTPException(422, f"不支持的字段：{body.field}")
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    if body.field == "registered_capital_wan":
        if body.value is not None and (body.value < 0 or body.value > 10_000_000):
            raise HTTPException(422, "注册资本需在 0-1000 亿元")
        e.registered_capital_wan = body.value
    elif body.field == "industry_key":
        key = (body.value or "").strip()
        if key:
            from ..vertical import industry_by_key

            ind = industry_by_key(key)
            if ind is None:
                raise HTTPException(422, f"未知行业键：{key}")
            e.industry_key = ind["key"]
            e.industry_name = ind["name"]
        else:
            e.industry_key = None
            e.industry_name = None
    elif body.field == "address":
        e.address = (str(body.value).strip() or None) if body.value else None
    from .imports import apply_icp

    apply_icp(e)
    session.commit()
    try:
        from .. import search as meili

        meili.upsert_enterprise(session, e.id)
    except Exception:  # noqa: BLE001 — 检索投影失败不影响主数据
        import logging

        logging.getLogger("vaclead.meili").warning("行内编辑后 Meili 同步失败", exc_info=True)
    return {"ok": True, "icp_score": e.icp_score}


class AdvancedFilterCondition(BaseModel):
    """单条件：field/operator/value（吸收 twenty advanced-filter 三元组）。"""
    field: str
    operator: str  # eq | neq | gt | gte | lt | lte | contains | is_empty | is_not_empty
    value: str | float | int | None = None


class AdvancedFilterIn(BaseModel):
    logic: str = "and"  # and | or
    conditions: list[AdvancedFilterCondition]


_ADV_ALLOWED_FIELDS: dict[str, type] = {
    "icp_score": float,
    "intent_score": float,
    "registered_capital_wan": float,
    "province": str,
    "city": str,
    "industry_key": str,
    "source": str,
    "name": str,
}
_TEXT_OPS = {"eq", "neq", "contains", "is_empty", "is_not_empty"}
_NUM_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"}


@router.post("/enterprises/advanced-filter")
def advanced_filter_enterprises(
    body: AdvancedFilterIn,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """高级筛选（吸收 twenty advanced-filter）：动态条件 AND/OR 组合。

    字段白名单 + 操作符按字段类型收敛，条件数 1-8，全部参数化查询。
    """
    from ..models import Contact

    if not 1 <= len(body.conditions) <= 8:
        raise HTTPException(422, "条件数量需为 1-8")
    if body.logic not in ("and", "or"):
        raise HTTPException(422, "logic 取值 and/or")

    clauses: list = []
    for c in body.conditions:
        ftype = _ADV_ALLOWED_FIELDS.get(c.field)
        if ftype is None:
            raise HTTPException(422, f"不支持的字段：{c.field}")
        col = getattr(Enterprise, c.field)
        is_num = ftype is float
        if is_num and c.operator not in _NUM_OPS:
            raise HTTPException(422, f"{c.field} 不支持 {c.operator}")
        if not is_num and c.operator not in _TEXT_OPS:
            raise HTTPException(422, f"{c.field} 不支持 {c.operator}")
        if c.operator == "is_empty":
            clauses.append(col.is_(None) if not is_num else (col == 0))
        elif c.operator == "is_not_empty":
            clauses.append(col.isnot(None) if not is_num else (col != 0))
        elif c.operator == "eq":
            clauses.append(col == c.value)
        elif c.operator == "neq":
            clauses.append(col != c.value)
        elif c.operator == "contains":
            clauses.append(col.like(f"%{c.value}%"))
        elif c.operator in ("gt", "gte", "lt", "lte"):
            try:
                num = float(c.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise HTTPException(422, f"{c.operator} 需要数值")
            clauses.append({
                "gt": col > num, "gte": col >= num,
                "lt": col < num, "lte": col <= num,
            }[c.operator])

    from functools import reduce
    import operator as _op

    combined = reduce(
        _op.and_ if body.logic == "and" else _op.or_, clauses
    )
    stmt = select(Enterprise).where(combined)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Enterprise.icp_score.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": e.id, "name": e.name, "province": e.province, "city": e.city,
                "industry_name": e.industry_name, "icp_score": e.icp_score,
                "intent_score": e.intent_score, "source": e.source,
                "is_competitor": e.is_competitor,
            }
            for e in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/enterprises/export")
def export_enterprises(
    q: str | None = None,
    industry_key: str | None = None,
    province: str | None = None,
    is_competitor: int = -1,
    source: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """导出当前筛选的企业 CSV（SQLite 路径同款筛选，上限 5000 行，SPEC-C F2）。"""
    import csv as _csv
    import io

    from fastapi.responses import StreamingResponse

    stmt = select(Enterprise)
    if industry_key:
        stmt = stmt.where(Enterprise.industry_key == industry_key)
    if province:
        stmt = stmt.where(Enterprise.province == province)
    if is_competitor in (0, 1):
        stmt = stmt.where(Enterprise.is_competitor == is_competitor)
    if source:
        channel = {"ggzy": ("ggzy", "ggzy_buyer"), "ccgp": ("ccgp", "ccgp_buyer")}.get(
            source, (source,)
        )
        stmt = stmt.where(Enterprise.source.in_(channel))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Enterprise.name.like(like))
    rows = session.scalars(
        stmt.order_by(Enterprise.icp_score.desc()).limit(5000)
    ).all()

    from ..models import Contact

    # 联系人列（销售刚需：导出即名单）——每企业全部联系人"姓名(角色):电话"拼接
    all_contacts = session.query(Contact).all()
    by_ent: dict[int, list[str]] = {}
    import re as _re

    for c in all_contacts:
        phone = c.title or ""
        # 电话槽位含数字才展示（legal_rep 回填的槽位是角色名，非号码）
        phone_part = f":{phone}" if phone and _re.search(r"\d", phone) else ""
        by_ent.setdefault(c.enterprise_id, []).append(
            f"{c.name}({ROLE_CN_EXPORT.get(c.role, c.role)}){phone_part}"
        )

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["id", "名称", "统一信用代码", "省", "市", "区县", "行业",
                     "注册资本(万)", "ICP分", "意图分", "疑似同行", "来源", "联系人"])
    for e in rows:
        writer.writerow([
            e.id, e.name, e.uscc or "", e.province or "", e.city or "", e.district or "",
            e.industry_name or "", e.registered_capital_wan or "", e.icp_score,
            e.intent_score, "是" if e.is_competitor == 1 else "否", e.source,
            "；".join(by_ent.get(e.id, [])),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=vaclead_export.csv"},
    )


@router.post("/enterprises/advanced-filter")
def advanced_filter_enterprises(
    body: AdvancedFilterIn,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """高级筛选（吸收 twenty advanced-filter）：动态条件 AND/OR 组合。

    字段白名单 + 操作符按字段类型收敛（数值字段禁 contains 等），
    条件数 1-8，全部参数化查询。
    """
    from ..models import Contact

    if not 1 <= len(body.conditions) <= 8:
        raise HTTPException(422, "条件数量需为 1-8")
    if body.logic not in ("and", "or"):
        raise HTTPException(422, "logic 取值 and/or")

    clauses: list = []
    for c in body.conditions:
        ftype = _ADV_ALLOWED_FIELDS.get(c.field)
        if ftype is None:
            raise HTTPException(422, f"不支持的字段：{c.field}")
        col = getattr(Enterprise, c.field)
        is_num = ftype is float
        if is_num and c.operator not in _NUM_OPS:
            raise HTTPException(422, f"{c.field} 不支持 {c.operator}")
        if not is_num and c.operator not in _TEXT_OPS:
            raise HTTPException(422, f"{c.field} 不支持 {c.operator}")
        if c.operator == "is_empty":
            clauses.append(col.is_(None) if not is_num else (col == 0))
        elif c.operator == "is_not_empty":
            clauses.append(col.isnot(None) if not is_num else (col != 0))
        elif c.operator == "eq":
            clauses.append(col == c.value)
        elif c.operator == "neq":
            clauses.append(col != c.value)
        elif c.operator == "contains":
            clauses.append(col.like(f"%{c.value}%"))
        elif c.operator in ("gt", "gte", "lt", "lte"):
            try:
                num = float(c.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise HTTPException(422, f"{c.operator} 需要数值")
            clauses.append({
                "gt": col > num, "gte": col >= num,
                "lt": col < num, "lte": col <= num,
            }[c.operator])

    from functools import reduce
    import operator as _op

    combined = reduce(
        _op.and_ if body.logic == "and" else _op.or_, clauses
    )

def _segments_of(e, session) -> dict | None:
    """ICP 分段评分（SPEC-F F2）：情报页分组展示。"""
    from datetime import datetime, timedelta

    from ..models import Signal

    cutoff = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    recent = (
        session.query(Signal)
        .filter(Signal.enterprise_id == e.id, Signal.pub_date >= cutoff)
        .first()
    )
    any_tender = (
        session.query(Signal)
        .filter(Signal.enterprise_id == e.id)
        .first()
    )
    try:
        from ..agents.impl.icp_judge_rule import judge_segments

        r = judge_segments(
            name=e.name,
            business_scope=e.business_scope or "",
            registered_capital_wan=e.registered_capital_wan,
            industry_key=e.industry_key,
            signal_active=recent is not None,
            has_tender=any_tender is not None,
        )
        return r["segments"]
    except Exception:  # noqa: BLE001 — 分段失败不影响详情
        return None


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
    from ..models import EnterpriseStat

    stat = session.get(EnterpriseStat, eid)
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
        "icp_segments": _segments_of(e, session),
        "intent_score": e.intent_score,
        "is_competitor": e.is_competitor,
        "stat": {
            "bid_count": stat.bid_count,
            "bid_amount_total_wan": stat.bid_amount_total_wan,
            "bid_amount_max_wan": stat.bid_amount_max_wan,
            "last_win_date": stat.last_win_date,
            "top_buyer": stat.top_buyer,
        }
        if stat is not None
        else None,
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


@router.post("/enterprises/{eid}/refresh-stat")
def refresh_enterprise_stat(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """手动刷新单企业中标画像（全量刷新走每日 04:00 定时任务，SPEC-B §5）。"""
    from ..jobs import enrich_stats

    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    stat = enrich_stats(session, eid)
    if not stat:
        raise HTTPException(404, "企业不存在")
    return {"ok": True, "stat": stat}


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
        from ..models import LEAD_STAGE_PROBABILITY

        existing.status = "following"
        existing.probability = LEAD_STAGE_PROBABILITY["following"]
        existing.owner_id = user.id
        existing.updated_at = utcnow()
        lead = existing
    else:
        composite = e.icp_score * 0.4 + e.intent_score * 0.6
        grade = (
            "S" if composite >= 80 else "A" if composite >= 60 else "B" if composite >= 40 else None
        )
        from ..models import LEAD_STAGE_PROBABILITY as _SP

        lead = Lead(
            enterprise_id=eid,
            status="following",
            probability=_SP["following"],
            owner_id=user.id,
            grade=grade,
            claimed_at=utcnow(),
            tenant_id=e.tenant_id,
        )
        session.add(lead)
    session.commit()
    return {
        "id": lead.id,
        "enterprise_id": eid,
        "status": lead.status,
        "grade": lead.grade,
        "probability": lead.probability,
    }


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


class BatchLeadIn(BaseModel):
    ids: list[int]


@router.post("/enterprises/batch-lead")
def batch_create_leads(
    body: BatchLeadIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """批量转线索（SPEC-C F2）：逐条套用单条校验，返回汇总不中断。"""
    from ..models import Lead

    converted = existed = skipped_competitor = missing = 0
    for eid in body.ids[:200]:  # 单批上限
        e = session.get(Enterprise, eid)
        if e is None:
            missing += 1
            continue
        if e.is_competitor:
            skipped_competitor += 1
            continue
        if session.query(Lead).filter(Lead.enterprise_id == eid).first():
            existed += 1
            continue
        session.add(Lead(enterprise_id=eid, status="new", tenant_id=e.tenant_id))
        converted += 1
    session.commit()
    return {
        "converted": converted, "existed": existed,
        "skipped_competitor": skipped_competitor, "missing": missing,
    }



@router.get("/enterprises/{eid}/similar")
def similar_enterprises(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """相似企业推荐（SPEC-C F3）：同行业优先 > 同省 > 意图近邻，排除同行与自身。"""
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    stmt = (
        select(Enterprise)
        .where(Enterprise.id != eid, Enterprise.is_competitor == 0)
        .order_by(Enterprise.intent_score.desc())
        .limit(500)
    )
    cands = session.scalars(stmt).all()

    def rank(c: Enterprise) -> tuple:
        return (
            1 if (c.industry_key and c.industry_key == e.industry_key) else 0,
            1 if (c.province and c.province == e.province) else 0,
            -abs((c.intent_score or 0) - (e.intent_score or 0)),
        )

    top = sorted(cands, key=rank, reverse=True)[:5]
    return {
        "items": [
            {"id": c.id, "name": c.name, "industry_name": c.industry_name,
             "province": c.province, "intent_score": c.intent_score}
            for c in top
        ]
    }


@router.post("/enterprises/{eid}/subscribe", status_code=201)
def subscribe_enterprise(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """关注企业（SPEC-C F1）：新中标/新公告生成站内通知。"""
    from ..models import Subscription

    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    exists = (
        session.query(Subscription)
        .filter(Subscription.enterprise_id == eid, Subscription.user_id == user.id)
        .first()
    )
    if exists is None:
        session.add(Subscription(enterprise_id=eid, user_id=user.id))
        session.commit()
    return {"ok": True, "subscribed": True}


@router.delete("/enterprises/{eid}/subscribe")
def unsubscribe_enterprise(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from ..models import Subscription

    session.query(Subscription).filter(
        Subscription.enterprise_id == eid, Subscription.user_id == user.id
    ).delete()
    session.commit()
    return {"ok": True, "subscribed": False}
