"""标讯中心（SPEC-M M1）：全局信号流——头部拓客平台的"招投标查询"门面功能。

数据全部来自已入库的官方公示信号（中标/采购人/政采公告），带企业归属与证据溯源。
facet 直接在 Python 侧 Counter 统计（信号量数百级，无需 SQL 聚合技巧）；
查询全部参数化（contains autoescape，用户输入的 %/_ 不作通配符）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Enterprise, Signal, User, extract_amount_wan

router = APIRouter(tags=["signals"])

_TYPE_CN = {
    "tender_win": "中标",
    "tender_buy": "公开采购",
    "tender": "政采公告",
    "proposal": "项目备案",
}

#: facet 计数的行数上限（超出则不返回 facet——列表本身也到不了这个量级）
_FACET_SCAN_LIMIT = 5000


def _cutoff(days: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%S")


def _apply_filters(stmt, *, type_, industry_key, province, q, days, competitor=None, skip=None):
    """把标讯筛选条件套到语句上（skip 豁免自身维度，供 facet 计数用）。"""
    if type_ and skip != "type":
        stmt = stmt.where(Signal.type == type_)
    if industry_key and skip != "industry":
        stmt = stmt.where(Enterprise.industry_key == industry_key)
    if province and skip != "province":
        stmt = stmt.where(Enterprise.province == province)
    if competitor is not None and skip != "competitor":
        stmt = stmt.where(Enterprise.is_competitor == competitor)
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
    competitor: int | None = None,
    since_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    facets: int = 0,
    order: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """全局标讯流：筛选 + 分页 + 类型/省份 facet 计数。

    competitor=1 → 竞对中标动态视图（SPEC-AA AA2：对标剑鱼竞对监测）。
    since_id=游标增量（SPEC-AI：前端 5s 轮询实时感知，items 倒序、max_id 带回）。
    order="amount" → 金额最大优先（SPEC-AMT：持久列 SQL ORDER BY，NULL 排尾；
    同额按 id desc 稳定分页。默认序不变 id desc）。
    """
    kw = dict(type_=type, industry_key=industry_key, province=province, q=q, days=days,
              competitor=competitor)

    stmt = _apply_filters(_joined(Signal, Enterprise), **kw)
    if since_id is not None:
        stmt = stmt.where(Signal.id > since_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # SPEC-AMT：金额排序直接 SQL ORDER BY 持久列（ORM 链式，与既有形态一致）——
    # 排序下推数据库，不再窗口内 Python 排序；NULL 排尾不挡前
    # SPEC-CUX4：评分排序（base_score desc——意图信号置信度）
    if order.strip().lower() == "amount" and since_id is None:
        stmt = stmt.order_by(Signal.amount_wan.desc().nulls_last(), Signal.id.desc())
    elif order.strip().lower() == "score" and since_id is None:
        stmt = stmt.order_by(Signal.base_score.desc().nulls_last(), Signal.id.desc())
    else:
        stmt = stmt.order_by(Signal.id.desc())
    rows = session.execute(
        stmt.offset(max(0, page - 1) * page_size)
        .limit(min(page_size, 50))
    ).all()

    items = [_signal_item(sig, ent) for sig, ent in rows]
    if session.dirty:
        # SPEC-AMT：惰性回填的旧行统一落库（幂等——列已有值即不再 dirty）
        session.commit()

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


def _signal_item(sig: Signal, ent: Enterprise) -> dict:
    """单行构造（SPEC-AMT：金额优先读持久列——列表渲染不再逐行正则）。

    旧行（迁移前入库、列空）回退正则抽一次并惰性回填 ORM 属性，
    由调用方在请求末尾统一 commit——读路径自愈：存量行被访问一次即补列，
    下次 order=amount 直接走 SQL 排序。
    """
    amount = sig.amount_wan
    if amount is None:
        amount = extract_amount_wan(sig.raw_excerpt)
        if amount is not None:
            sig.amount_wan = amount
    return {
        "id": sig.id,
        "type": sig.type,
        "type_cn": _TYPE_CN.get(sig.type, sig.type),
        "title": sig.title,
        "url": sig.url,
        "ent_id": ent.id,
        "ent_name": ent.name,
        "ent_industry": ent.industry_key,
        "is_competitor": ent.is_competitor,
        "province": ent.province,
        "amount_wan": amount,
        "pub_date": sig.pub_date,
        "created_at": sig.created_at,
        "evidence_claim": sig.evidence_claim,
        "base_score": sig.base_score,
    }
