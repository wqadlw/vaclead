"""信息中心（SPEC-INFO）：数据资产总览——有什么/从哪来/多新/多全。

把散在 tasks（采集入口）/quality（质量运维）/competitors（雷达）的
"信息"维度收拢成一个数据资产视角：来源矩阵 + 批次台账 + 规模总览。
质量运维（漏斗/缺口/水位）仍在 quality 页，此处链接过去。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, Signal, Lead

router = APIRouter(tags=["data"])

SOURCE_CN = {
    "poi": "高德 POI 采集",
    "ggzy": "公共资源交易",
    "ccgp": "政采网公告",
    "ccgp_buyer": "政采买家",
    "ggzy_buyer": "公采买家",
    "proposal_hb": "湖北备案",
    "proposal_zj": "浙江备案",
    "proposal_js": "江苏备案",
    "proposal_gd": "广东备案",
    "csv": "手工导入",
    "tender": "招标导入",
    "riskbird": "风鸟搬运",
}

_DIALABLE = (
    Contact.phones_json.is_not(None),
    Contact.phones_json.notin_(("", "[]")),
)


@router.get("/data/overview")
def data_overview(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """SPEC-INFO：数据资产总览（规模 / 来源矩阵 / 批次台账）。"""
    alive = Enterprise.deleted_at.is_(None)
    total = (
        session.query(func.count())
        .select_from(Enterprise)
        .filter(alive)
        .scalar()
    ) or 0
    contacts_total = (
        session.query(func.count())
        .select_from(Contact)
        .filter(*_DIALABLE)
        .scalar()
    ) or 0
    signals_total = (
        session.query(func.count()).select_from(Signal).scalar()
    ) or 0
    leads_total = (
        session.query(func.count()).select_from(Lead).scalar()
    ) or 0

    # 来源矩阵：企业数 / 可拨企业数 / 最近入库 / 批次数
    src_rows = (
        session.query(
            Enterprise.source,
            func.count(),
            func.min(Enterprise.created_at),
            func.max(Enterprise.created_at),
            func.count(func.distinct(Enterprise.source_batch_id)),
        )
        .filter(alive)
        .group_by(Enterprise.source)
        .all()
    )
    # 可拨企业数按源（子查询半连接，口径与 contacts 一致）
    dial_by_src = dict(
        session.query(Enterprise.source, func.count(func.distinct(Contact.enterprise_id)))
        .join(Contact, Contact.enterprise_id == Enterprise.id)
        .filter(*_DIALABLE, alive)
        .group_by(Enterprise.source)
        .all()
    )
    sources = []
    for src, n, first_at, last_at, batch_n in src_rows:
        sources.append({
            "source": src,
            "label": SOURCE_CN.get(src, src),
            "count": n,
            "dialable": dial_by_src.get(src, 0),
            "first_at": (first_at or "")[:10],
            "last_at": (last_at or "")[:10],
            "batch_count": batch_n or 0,
        })
    sources.sort(key=lambda s: -s["count"])

    # 批次台账（有批次溯源的：导入/采集批次——POI 未打批次如实展示 NULL）
    batch_rows = (
        session.query(
            Enterprise.source,
            Enterprise.source_batch_id,
            func.count(),
            func.min(Enterprise.created_at),
            func.max(Enterprise.created_at),
        )
        .filter(alive, Enterprise.source_batch_id.is_not(None))
        .group_by(Enterprise.source, Enterprise.source_batch_id)
        .order_by(func.max(Enterprise.created_at).desc())
        .limit(12)
        .all()
    )
    batches = [
        {
            "batch_id": bid, "source": src, "label": SOURCE_CN.get(src, src),
            "count": n,
            "first_at": (first or "")[:19], "last_at": (last or "")[:19],
        }
        for src, bid, n, first, last in batch_rows
    ]

    # SPEC-INFO v2：行业分布（资产视角——哪些行业的客户储备最厚）
    ind_rows = (
        session.query(
            Enterprise.industry_key,
            Enterprise.industry_name,
            func.count(),
        )
        .filter(alive, Enterprise.industry_key.is_not(None), Enterprise.industry_key != "")
        .group_by(Enterprise.industry_key, Enterprise.industry_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    industries = [
        {"key": k, "name": name or k, "count": n}
        for k, name, n in ind_rows
    ]
    # SPEC-PROV 溯源概览：prov_json 全表聚合——各来源贡献的字段计数
    prov_src: dict[str, int] = {}
    for (pj,) in session.query(Enterprise.prov_json).filter(alive, Enterprise.prov_json != "{}").all():
        try:
            import json as _json

            prov = _json.loads(pj)
        except ValueError:
            continue
        for rec in prov.values():
            if isinstance(rec, dict) and rec.get("source"):
                prov_src[rec["source"]] = prov_src.get(rec["source"], 0) + 1

    return {
        "assets": {
            "total": total,
            "contacts_dialable": contacts_total,
            "signals": signals_total,
            "leads": leads_total,
        },
        "sources": sources,
        "batches": batches,
        "industries": industries,
        "prov_stats": sorted(prov_src.items(), key=lambda x: -x[1]),
    }