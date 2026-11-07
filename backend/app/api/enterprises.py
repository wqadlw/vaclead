"""企业相关路由：筛选列表 / 详情 / 转线索 / 工作台。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..api.leads import _claim_guard
from ..db import get_session
from ..models import Contact, Enterprise, Lead, Signal, User, utcnow
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
    established_within_years: int | None = None,
    bid_min: int | None = None,
    bid_amount_min: float | None = None,
    complete: int = 0,
    tag: str | None = None,
    customer_role: str | None = None,
    call_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    facets: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    # SPEC-M M2：新维度（成立年限/中标力）只支持 SQLite 路径——带参时跳过 Meili，
    # 保证 total/分页语义正确（Meili 投影不含这两列）
    sqlite_only = bool(established_within_years or bid_min or bid_amount_min or complete or tag or customer_role or call_status)
    # Quick picks（gmaps-scraper 模式）：has_contact=1 → 只看有可拨打联系人的企业。
    # SPEC-I I5：不再物化全量 id 列表（limit(5000) 会静默漏企业）——
    # SQLite 路径走 IN 子查询半连接；Meili 路径只对当前页 id 做存活过滤。
    if has_contact:
        from ..models import Contact

        if session.query(Contact.id).limit(1).scalar() is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
    # Meili 优先（毫秒级组合筛选+分面）；不可用回退 SQLite LIKE 路径
    if meili.available() and not sqlite_only:
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
                if has_contact:
                    from ..models import Contact

                    allow = {
                        int(r[0])
                        for r in session.query(Contact.enterprise_id)
                        .filter(Contact.enterprise_id.in_(ids))
                        .distinct()
                    }
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
    if customer_role:
        # SPEC-AU：OEM/终端/分销/集成商 角色筛选（AS2）
        stmt = stmt.where(Enterprise.customer_role == customer_role)
    if call_status:
        # SPEC-AZ AZ1 触达覆盖：untouched=有电话且全部联系人未拨；touched=任一已拨
        from ..models import Contact as _C
        from sqlalchemy import or_ as _or

        if call_status == "untouched":
            stmt = stmt.where(
                Enterprise.id.in_(select(_C.enterprise_id).where(_C.phones_json != "[]"))
                & ~Enterprise.id.in_(
                    select(_C.enterprise_id).where(_C.call_result != "pending")
                )
            )
        elif call_status == "touched":
            stmt = stmt.where(
                Enterprise.id.in_(
                    select(_C.enterprise_id).where(_C.call_result != "pending")
                )
            )
    if has_contact:
        from ..models import Contact

        stmt = stmt.where(Enterprise.id.in_(select(Contact.enterprise_id)))
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
    # SPEC-M M2a：成立年限（新锐企业筛选）
    if established_within_years:
        cutoff_year = datetime.now(timezone.utc).year - established_within_years
        stmt = stmt.where(
            Enterprise.established_at.is_not(None),
            Enterprise.established_at >= f"{cutoff_year}-01-01",
        )
    # SPEC-M M2b：中标力（EnterpriseStat 半连接）
    if bid_min is not None or bid_amount_min is not None:
        from ..models import EnterpriseStat

        stat_cond = []
        if bid_min is not None:
            stat_cond.append(EnterpriseStat.bid_count >= bid_min)
        if bid_amount_min is not None:
            stat_cond.append(EnterpriseStat.bid_amount_total_wan >= bid_amount_min)
        stmt = stmt.where(
            Enterprise.id.in_(
                select(EnterpriseStat.enterprise_id).where(*stat_cond)
            )
        )
    # SPEC-U U3：资料齐全（四要素全非空）
    if complete:
        stmt = stmt.where(
            Enterprise.legal_rep.is_not(None),
            Enterprise.established_at.is_not(None),
            Enterprise.registered_capital_wan.is_not(None),
            Enterprise.business_scope.is_not(None),
        )
    # SPEC-V V2：标签筛选（JSON 列参数化 LIKE，引号包裹防子串误命中）
    if tag:
        from sqlalchemy import bindparam

        stmt = stmt.where(
            Enterprise.tags.like(
                bindparam("tagpat", f'%"{tag.strip()}"%')
            )
        )

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
                "legal_rep": e.legal_rep,
                "established_at": e.established_at,
                "completeness": e.completeness(),
                "tags": _tags_of(e),
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


@router.get("/enterprises/geo")
def enterprises_geo(
    south: float | None = None,
    west: float | None = None,
    north: float | None = None,
    east: float | None = None,
    with_phone: int = 0,
    industry_key: str | None = None,
    limit: int = 2000,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """客户地图打点（SPEC-AJ AJ2）：GCJ-02 坐标（与高德瓦片同源零纠偏）。

    bbox 可选；with_phone=1 只看打得通的；limit ≤2000 防炸（前端分城聚合加载）。
    """
    callable_ids = (
        session.query(Contact.enterprise_id)
        .filter(
            or_(
                Contact.phones_json != "[]",
                Contact.title.op("GLOB")("*[0-9]*"),
            )
        )
        .subquery()
    )
    stmt = (
        select(Enterprise)
        .where(
            Enterprise.lat.is_not(None),
            Enterprise.lng.is_not(None),
            Enterprise.lat != 0,
            Enterprise.lng != 0,
        )
    )
    if south is not None and north is not None and west is not None and east is not None:
        stmt = stmt.where(
            Enterprise.lat >= south, Enterprise.lat <= north,
            Enterprise.lng >= west, Enterprise.lng <= east,
        )
    if with_phone:
        stmt = stmt.where(Enterprise.id.in_(callable_ids.select()))
    if industry_key:
        stmt = stmt.where(Enterprise.industry_key == industry_key)
    rows = session.scalars(stmt.limit(min(limit, 2000))).all()
    phone_set = {
        r[0] for r in session.execute(select(Contact.enterprise_id).where(
            or_(
                Contact.phones_json != "[]",
                Contact.title.op("GLOB")("*[0-9]*"),
            )
        )).all()
    }
    # SPEC-AK AK2：超 1500 点自动网格聚合（geoutil 纯函数，24k 点前端防炸）
    from ..geoutil import grid_cluster

    flat = [(e.lat, e.lng, e.id in phone_set) for e in rows]
    clustered = grid_cluster(flat, threshold=1500)
    if clustered["mode"] == "clusters":
        return {"mode": "clusters", "total": clustered["total"], "items": clustered["items"]}
    return {
        "mode": "points",
        "total": clustered["total"],
        "items": [
            {
                "id": e.id, "name": e.name, "lat": e.lat, "lng": e.lng,
                "city": e.city, "district": e.district,
                "industry_name": e.industry_name,
                "intent_score": e.intent_score,
                "has_phone": e.id in phone_set,
            }
            for e in rows
        ],
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
    """导出当前筛选的企业 CSV（流式响应无超时风险；上限 20000 行，SPEC-AM AM2 扩容）。"""
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
        stmt.order_by(Enterprise.icp_score.desc()).limit(20000)
    ).all()

    from ..models import Contact

    # 联系人列（销售刚需：导出即名单）——每企业全部联系人"姓名(角色):电话"拼接
    # SPEC-I I5：只按导出行 enterprise ids 查询，不再全表加载 Contact
    ent_ids = [e.id for e in rows]
    export_contacts = (
        session.query(Contact).filter(Contact.enterprise_id.in_(ent_ids)).all()
        if ent_ids
        else []
    )
    by_ent: dict[int, list[str]] = {}
    import re as _re

    for c in export_contacts:
        phone = c.title or ""
        # 电话槽位含数字才展示（legal_rep 回填的槽位是角色名，非号码）
        phone_part = f":{phone}" if phone and _re.search(r"\d", phone) else ""
        by_ent.setdefault(c.enterprise_id, []).append(
            f"{c.name}({ROLE_CN_EXPORT.get(c.role, c.role)}){phone_part}"
        )

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["id", "名称", "统一信用代码", "法定代表人", "成立日期", "省", "市",
                     "区县", "行业", "注册资本(万)", "ICP分", "意图分", "疑似同行", "来源",
                     "资料完整度", "标签", "联系人"])
    import json as _json

    for e in rows:
        try:
            tags = "；".join(_json.loads(e.tags or "[]"))
        except ValueError:
            tags = ""
        writer.writerow([
            e.id, e.name, e.uscc or "", e.legal_rep or "", e.established_at or "",
            e.province or "", e.city or "", e.district or "",
            e.industry_name or "", e.registered_capital_wan or "", e.icp_score,
            e.intent_score, "是" if e.is_competitor == 1 else "否", e.source,
            f"{e.completeness()}/4", tags,
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


def _tags_of(e) -> list[str]:
    """Enterprise.tags JSON 列 → 数组（损坏容错）。"""
    import json as _json

    try:
        arr = _json.loads(e.tags or "[]")
        return arr if isinstance(arr, list) else []
    except ValueError:
        return []


# ---------------- 回收站（SPEC-AM AM1） ----------------
# 注意：/enterprises/trash 静态段必须在 /enterprises/{eid} 之前注册（路由顺序坑）
@router.get("/enterprises/trash")
def trash_list(
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """回收站（admin）：deleted_at 倒序。"""
    stmt = (
        select(Enterprise)
        .where(Enterprise.deleted_at.is_not(None))
        .order_by(Enterprise.deleted_at.desc())
        .execution_options(include_deleted=True)
    )
    total = session.scalar(
        select(func.count())
        .select_from(Enterprise)
        .where(Enterprise.deleted_at.is_not(None))
        .execution_options(include_deleted=True)
    ) or 0
    rows = session.scalars(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {"id": e.id, "name": e.name, "city": e.city, "source": e.source,
             "deleted_at": e.deleted_at}
            for e in rows
        ],
        "total": total,
    }


@router.get("/enterprises/{eid}")
def enterprise_detail(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at:
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
        "legal_rep": e.legal_rep,
        "established_at": e.established_at,
        "completeness": e.completeness(),
        "tags": _tags_of(e),
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


# ---------------- SPEC-V V1 企业备注 ----------------

@router.get("/enterprises/{eid}/notes")
def list_enterprise_notes(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """企业备注（主观工作笔记，区别于信号时间线），倒序。"""
    from ..models import EnterpriseNote

    rows = (
        session.query(EnterpriseNote)
        .filter(EnterpriseNote.enterprise_id == eid)
        .order_by(EnterpriseNote.id.desc())
        .limit(100)
        .all()
    )
    users = {u.id: (u.display_name or u.username) for u in session.query(User).all()}
    return {
        "items": [
            {"id": n.id, "content": n.content, "author": users.get(n.user_id, str(n.user_id)),
             "user_id": n.user_id, "created_at": n.created_at}
            for n in rows
        ]
    }


class NoteIn(BaseModel):
    content: str


@router.post("/enterprises/{eid}/notes", status_code=201)
def create_enterprise_note(
    eid: int,
    body: NoteIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    content = (body.content or "").strip()
    if not 1 <= len(content) <= 2000:
        raise HTTPException(422, "备注 1-2000 字")
    from ..models import EnterpriseNote

    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    n = EnterpriseNote(enterprise_id=eid, user_id=user.id, content=content)
    session.add(n)
    session.commit()
    return {"id": n.id}


@router.delete("/enterprises/notes/{nid}")
def delete_enterprise_note(
    nid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from ..models import EnterpriseNote

    n = session.get(EnterpriseNote, nid)
    if n is None:
        raise HTTPException(404, "备注不存在")
    if n.user_id != user.id and user.role != "admin":
        raise HTTPException(403, "仅本人或管理员可删除")
    session.delete(n)
    session.commit()
    return {"ok": True}


# ---------------- SPEC-V V2 企业标签 ----------------

class TagsIn(BaseModel):
    tags: list[str]


@router.put("/enterprises/{eid}/tags")
def set_enterprise_tags(
    eid: int,
    body: TagsIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """全量替换标签：单标签 ≤20 字、去重 trim、≤8 个（防标签爆炸）。"""
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    cleaned: list[str] = []
    for t in body.tags:
        t = t.strip()
        if t and t not in cleaned:
            cleaned.append(t)
    if len(cleaned) > 8:
        raise HTTPException(422, "标签最多 8 个")
    if any(len(t) > 20 for t in cleaned):
        raise HTTPException(422, "单个标签 ≤20 字")
    import json as _json

    e.tags = _json.dumps(cleaned, ensure_ascii=False)
    session.commit()
    return {"tags": cleaned}


@router.get("/tags")
def all_tags(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """全库标签聚合（标签云/自动补全建议）。"""
    import json as _json
    from collections import Counter

    counter: Counter = Counter()
    for (raw,) in session.query(Enterprise.tags).all():
        try:
            counter.update(_json.loads(raw or "[]"))
        except ValueError:
            continue
    return {"items": [{"tag": t, "count": c} for t, c in counter.most_common(50)]}


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
        session.flush()
        from ..audit import log_insert

        log_insert(session, lead)
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
    created_leads: list[Lead] = []
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
        lead = Lead(enterprise_id=eid, status="new", tenant_id=e.tenant_id)
        session.add(lead)
        created_leads.append(lead)
        converted += 1
    if created_leads:
        session.flush()
        from ..audit import log_insert

        for lead in created_leads:
            log_insert(session, lead)
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
    """相似企业推荐 v2（SPEC-CD）：look-alike 可解释排序。

    同行业 > 同省 > 同市 > 可拨 > 近 90 天有信号 > 意图近邻；
    排除同行/自身/**在管**（已有线索=已拿下）；BX 轮省份 87% 覆盖
    让"同省"维度从形同虚设变可用。每条带 reasons 理由链。
    """
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    stmt = (
        select(Enterprise)
        .where(Enterprise.id != eid, Enterprise.is_competitor == 0)
        .order_by(Enterprise.intent_score.desc())
        .limit(800)
    )
    cands = session.scalars(stmt).all()
    # 排除在管（已有线索）
    managed_ids = {
        r[0] for r in session.query(Lead.enterprise_id).all()
    }
    cands = [c for c in cands if c.id not in managed_ids]
    cand_ids = [c.id for c in cands]

    dial_ids: set[int] = set()
    recent_ids: set[int] = set()
    if cand_ids:
        cond = [Enterprise.id == i for i in cand_ids]
        dial_rows = (
            session.query(Contact.enterprise_id, Contact.phones_json)
            .join(Enterprise, Enterprise.id == Contact.enterprise_id)
            .filter(or_(*cond))
            .all()
        )
        from ..phones import load_phones

        dial_ids = {eid for eid, pj in dial_rows if load_phones(pj)}
        d90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        sig_rows = (
            session.query(Signal.enterprise_id)
            .filter(
                or_(*[Signal.enterprise_id == i for i in cand_ids]),
                Signal.created_at >= d90,
            )
            .all()
        )
        recent_ids = {r[0] for r in sig_rows}

    def rank(c: Enterprise) -> tuple:
        return (
            1 if (c.industry_key and c.industry_key == e.industry_key) else 0,
            1 if (c.province and c.province == e.province) else 0,
            1 if (c.city and c.city == e.city) else 0,
            1 if c.id in dial_ids else 0,
            1 if c.id in recent_ids else 0,
            -abs((c.intent_score or 0) - (e.intent_score or 0)),
        )

    def reasons(c: Enterprise) -> list[str]:
        out = []
        if c.industry_key and c.industry_key == e.industry_key:
            out.append("同行业")
        if c.province and c.province == e.province:
            out.append("同省")
        if c.city and c.city == e.city:
            out.append("同市")
        if c.id in dial_ids:
            out.append("可拨")
        if c.id in recent_ids:
            out.append("近期有信号")
        return out

    top = sorted(cands, key=rank, reverse=True)[:8]
    return {
        "items": [
            {"id": c.id, "name": c.name, "industry_name": c.industry_name,
             "province": c.province, "city": c.city,
             "intent_score": c.intent_score, "reasons": reasons(c)}
            for c in top
        ]
    }


# ---------------- SPEC-Y Y1：批量打标签（append 合并语义） ----------------

class BatchTagsIn(BaseModel):
    ids: list[int]
    tags: list[str]
    mode: str = "append"  # append=合并已有 | replace=全量替换


@router.post("/enterprises/batch-tags")
def batch_set_tags(
    body: BatchTagsIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """批量打标签：append 合并去重（不覆盖销售已有标签）/ replace 全量替换。

    部分成功明细：不存在/超上限的企业进 skipped。单批 ≤200 家。
    """
    import json as _json

    if not body.ids or len(body.ids) > 200:
        raise HTTPException(422, "ids 1-200")
    if body.mode not in ("append", "replace"):
        raise HTTPException(422, "mode 取值 append|replace")
    cleaned: list[str] = []
    for t in body.tags:
        t = t.strip()
        if t and t not in cleaned:
            cleaned.append(t)
    if not cleaned:
        raise HTTPException(422, "标签不能为空")
    if any(len(t) > 20 for t in cleaned):
        raise HTTPException(422, "单个标签 ≤20 字")

    updated = 0
    skipped: list[dict] = []
    rows = session.query(Enterprise).filter(Enterprise.id.in_(body.ids)).all()
    for e in rows:
        current: list[str] = []
        try:
            parsed = _json.loads(e.tags or "[]")
            current = parsed if isinstance(parsed, list) else []
        except ValueError:
            current = []
        merged = cleaned if body.mode == "replace" else list(dict.fromkeys(current + cleaned))
        if len(merged) > 8:
            skipped.append({"id": e.id, "name": e.name, "reason": "合并后超 8 个标签"})
            continue
        e.tags = _json.dumps(merged, ensure_ascii=False)
        updated += 1
    session.commit()
    return {"updated": updated, "skipped": skipped}


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


# ---------------- 回收站操作（SPEC-AM AM1） ----------------
@router.delete("/enterprises/{eid}")
def soft_delete_enterprise(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """软删（admin）：进回收站，30 天后自动清空。信号保留但全链隐藏。"""
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at:
        raise HTTPException(404, "企业不存在")
    from ..models import utcnow as _now

    e.deleted_at = _now()
    session.commit()
    return {"ok": True, "id": eid}


@router.post("/enterprises/{eid}/restore")
def restore_enterprise(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """从回收站恢复。"""
    stmt = (
        select(Enterprise)
        .where(Enterprise.id == eid, Enterprise.deleted_at.is_not(None))
        .execution_options(include_deleted=True)
    )
    e = session.scalar(stmt)
    if e is None:
        raise HTTPException(404, "回收站中不存在该企业")
    e.deleted_at = None
    session.commit()
    return {"ok": True, "id": eid}


@router.delete("/enterprises/{eid}/purge")
def purge_enterprise(
    eid: int,
    confirm: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """彻底删（admin，confirm=1 二次确认）：级联信号/联系人/线索/备注。"""
    if confirm != 1:
        raise HTTPException(422, "彻底删除需要 confirm=1 二次确认")
    stmt = (
        select(Enterprise)
        .where(Enterprise.id == eid, Enterprise.deleted_at.is_not(None))
        .execution_options(include_deleted=True)
    )
    e = session.scalar(stmt)
    if e is None:
        raise HTTPException(404, "回收站中不存在该企业")
    # 级联（显式删，避免依赖各表 FK 是否带 ondelete）
    from ..models import (
        Contact,
        EnterpriseNote,
        Lead,
        Signal,
        Subscription,
    )

    for model in (Signal, Contact, Lead, EnterpriseNote, Subscription):
        session.query(model).filter(model.enterprise_id == eid).delete(
            synchronize_session=False
        )
    session.delete(e)
    session.commit()
    return {"ok": True, "id": eid, "purged": True}


@router.post("/enterprises/bulk-cleanup")
def bulk_cleanup(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """POI 噪音批量软删（SPEC-AM 回收站能力 + SPEC-AN AN4）。

    patterns：名称子串列表（苑/家园/小区/便利店…）；dry_run=1 只预览计数。
    """
    patterns = [str(p).strip() for p in (body.get("patterns") or []) if str(p).strip()]
    dry_run = int(body.get("dry_run") or 0)
    if not patterns:
        raise HTTPException(422, "patterns 不能为空")
    matched = []
    rows = session.scalars(select(Enterprise)).all()
    for e in rows:
        if any(p in e.name for p in patterns):
            matched.append(e)
    if dry_run:
        return {"matched": len(matched), "dry_run": True,
                "samples": [e.name for e in matched[:10]]}
    from ..models import utcnow as _now

    for e in matched:
        e.deleted_at = _now()
    session.commit()
    return {"deleted": len(matched), "dry_run": False}
