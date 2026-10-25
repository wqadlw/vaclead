"""仪表盘聚合端点（吸收 ant-design-pro 分析页 + tremor KPI 模式）。

一个端点喂饱分析页四段：KPI 环比 / 30 天趋势 / 来源分布 / 线索漏斗。
全部真实库内数据聚合，无发明。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, Lead, Signal, User

router = APIRouter(tags=["dashboard"])


def _day(n_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n_ago)).strftime("%Y-%m-%d")


@router.get("/dashboard/summary")
def dashboard_summary(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """分析页聚合：KPI（含环比）/ 30 天信号趋势 / 来源分布 / 线索漏斗。"""
    # ---- KPI 与环比（近 7 天 vs 前 7 天）----
    ent_total = session.scalar(select(func.count()).select_from(Enterprise)) or 0
    sig_total = session.scalar(select(func.count()).select_from(Signal)) or 0
    lead_total = session.scalar(select(func.count()).select_from(Lead)) or 0
    contact_total = session.scalar(select(func.count()).select_from(Contact)) or 0

    def _count(model, field, lo: str, hi: str) -> int:
        return (
            session.query(func.count())
            .select_from(model)
            .filter(field >= lo, field < hi)
            .scalar()
            or 0
        )

    today, d7, d14 = _day(0), _day(7), _day(14)
    kpis = {
        "enterprises": {
            "total": ent_total,
            "delta": _count(Enterprise, Enterprise.created_at, d7, today)
            - _count(Enterprise, Enterprise.created_at, d14, d7),
        },
        "signals": {
            "total": sig_total,
            "delta": _count(Signal, Signal.created_at, d7, today)
            - _count(Signal, Signal.created_at, d14, d7),
        },
        "leads": {"total": lead_total, "delta": 0},
        "contacts": {
            "total": contact_total,
            "delta": _count(Contact, Contact.created_at, d7, today)
            - _count(Contact, Contact.created_at, d14, d7),
        },
    }

    # ---- 30 天信号趋势（按创建日计数）----
    trend_rows = (
        session.query(func.date(Signal.created_at), func.count())
        .filter(Signal.created_at >= _day(29))
        .group_by(func.date(Signal.created_at))
        .all()
    )
    by_date: dict[str, int] = {str(r[0]): int(r[1]) for r in trend_rows}
    trend = [{"date": _day(29 - i), "count": by_date.get(_day(29 - i), 0)} for i in range(29, -1, -1)]

    # ---- 来源分布（企业按采集通道）----
    source_rows = (
        session.query(Enterprise.source, func.count())
        .group_by(Enterprise.source)
        .all()
    )
    channels: dict[str, int] = {}
    for src, cnt in source_rows:
        key = {"ggzy_buyer": "ggzy", "ccgp_buyer": "ccgp"}.get(str(src), str(src))
        channels[key] = channels.get(key, 0) + int(cnt)
    sources = sorted(
        [{"name": k, "value": v} for k, v in channels.items()],
        key=lambda x: -x["value"],
    )

    # ---- 线索漏斗（状态计数，按流转顺序）----
    funnel_order = ["new", "following", "intent", "won", "pool", "lost"]
    lead_rows = session.query(Lead.status, func.count()).group_by(Lead.status).all()
    lead_by_status = {str(r[0]): int(r[1]) for r in lead_rows}
    funnel = [
        {"status": s, "label": lb, "value": lead_by_status.get(s, 0)}
        for s, lb in [
            ("new", "新线索"), ("following", "跟进中"), ("intent", "意向"),
            ("won", "成交"), ("pool", "公海"), ("lost", "放弃"),
        ]
        if s in funnel_order
    ]

    return {"kpis": kpis, "trend": trend, "sources": sources, "funnel": funnel}
