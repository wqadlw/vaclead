"""标讯中心（SPEC-M M1）：全局信号流——头部拓客平台的"招投标查询"门面功能。

数据全部来自已入库的官方公示信号（中标/采购人/政采公告），带企业归属与证据溯源。
facet 直接在 Python 侧 Counter 统计（信号量数百级，无需 SQL 聚合技巧）；
查询全部参数化（contains autoescape，用户输入的 %/_ 不作通配符）。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Enterprise, Signal, User

router = APIRouter(tags=["signals"])

# 与 jobs.py 同源：从证据句提取成交金额（万元）——不 import jobs 防循环
_AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")

_TYPE_CN = {"tender_win": "中标", "tender_buy": "公开采购", "tender": "政采公告"}

#: facet 计数的行数上限（超出则不返回 facet——列表本身也到不了这个量级）
_FACET_SCAN_LIMIT = 5000


def _cutoff(days: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%S")


def _apply_filters(stmt, *, type_, industry_key, province, q, days, skip=None):
    """把标讯筛选条件套到语句上（skip 豁免自身维度，供 facet 计数用）。"""
    if type_ and skip != "type":
        stmt = stmt.where(Signal.type == type_)
    if industry_key and skip != "industry":
        stmt = stmt.where(Enterprise.industry_key == industry_key)
    if province and skip != "province":
        stmt = stmt.where(Enterprise.province == province)
    if q and skip != "q":
        stmt = stmt.where(Signal.title.contains(q.strip(), autoescape=True))
    if days and skip != "days":
        stmt = stmt.where(Signal.created_at >= _cutoff(days))
    return stmt


@router.get("/signals")
def list_signals(
    type: str | None = None,
    industry_key: str | None = None,
    province: str | None = None,
    q: str | None = None,
    days: int | None = None,
    page: int = 1,
    page_size: int = 20,
    facets: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """全局标讯流：筛选 + 分页 + 类型/省份 facet 计数。"""
    kw = dict(type_=type, industry_key=industry_key, province=province, q=q, days=days)

    stmt = _apply_filters(_joined(Signal, Enterprise), **kw)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(
        stmt.order_by(Signal.id.desc())
        .offset(max(0, page - 1) * page_size)
        .limit(min(page_size, 50))
    ).all()

    items = []
    for sig, ent in rows:
        m = _AMOUNT_RE.search(sig.raw_excerpt or "")
        items.append({
            "id": sig.id,
            "type": sig.type,
            "type_cn": _TYPE_CN.get(sig.type, sig.type),
            "title": sig.title,
            "ent_id": ent.id,
            "ent_name": ent.name,
            "ent_industry": ent.industry_key,
            "is_competitor": ent.is_competitor,
            "province": ent.province,
            "amount_wan": float(m.group(1)) if m else None,
            "pub_date": sig.pub_date,
            "created_at": sig.created_at,
            "evidence_claim": sig.evidence_claim,
            "base_score": sig.base_score,
        })

    out: dict = {"items": items, "total": total, "page": page, "page_size": page_size}
    if facets and total <= _FACET_SCAN_LIMIT:
        # Python 侧 Counter 统计（量级匹配：数百级信号）
        scan = session.execute(
            _apply_filters(_joined(Signal.type, Enterprise.province), skip="all", **kw)
        ).all()
        out["facets"] = {
            "type": dict(Counter(str(r[0] or "未知") for r in scan)),
            "province": dict(Counter(str(r[1] or "未知") for r in scan)),
        }
    return out


def _joined(*cols):
    return select(*cols).select_from(Signal).join(
        Enterprise, Signal.enterprise_id == Enterprise.id
    )
