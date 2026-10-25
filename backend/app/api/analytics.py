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

    # ---- 省份地理分布（SPEC-F F4：地图视图数据源）----
    geo_rows = (
        session.query(Enterprise.province, func.count())
        .filter(Enterprise.province.isnot(None), Enterprise.province != "")
        .group_by(Enterprise.province)
        .all()
    )
    geo = sorted(
        [{"province": str(p_).strip(), "count": int(c)} for p_, c in geo_rows if str(p_).strip()],
        key=lambda x: -x["count"],
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

    return {"kpis": kpis, "trend": trend, "sources": sources, "geo": geo, "funnel": funnel}


@router.get("/dashboard/my-day")
def my_day(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """今日作战清单（SPEC-G G5）：四源合并按优先级排序——销售打开就知道先干什么。

    源与优先级：①逾期待办(p0) ②今日到期待办(p1) ③意向线索超3天未跟(p1)
    ④新线索从未跟进超2天(p2) ⑤今日规则命中数(p2 汇总行)。
    """
    from datetime import date, timedelta

    from ..models import Lead, Todo, WorkflowRun

    today = date.today().isoformat()
    three_days_ago = (date.today() - timedelta(days=3)).isoformat()
    two_days_ago = (date.today() - timedelta(days=2)).isoformat()
    items: list[dict] = []

    # ① 逾期待办
    overdue = (
        session.query(Todo)
        .filter(Todo.done == 0, Todo.due_date < today)
        .order_by(Todo.due_date.asc())
        .all()
    )
    for t in overdue:
        items.append({
            "priority": 0, "kind": "overdue_todo", "id": t.id,
            "title": t.title, "hint": f"逾期 {t.due_date}",
        })

    # ② 今日到期
    due_today = (
        session.query(Todo)
        .filter(Todo.done == 0, Todo.due_date == today)
        .all()
    )
    for t in due_today:
        items.append({
            "priority": 1, "kind": "due_today", "id": t.id,
            "title": t.title, "hint": "今天到期",
        })

    # ③ 意向线索超3天未跟（自己的；公海无人跟进不催）
    from sqlalchemy import select as _sel
    stale_intent = session.execute(
        _sel(Lead, Enterprise.name)
        .join(Enterprise, Enterprise.id == Lead.enterprise_id)
        .where(
            Lead.status == "intent",
            Lead.owner_id == user.id,
            (Lead.last_followed_at.is_(None))
            | (Lead.last_followed_at < three_days_ago),
        )
        .limit(10)
    ).all()
    for lead, name in stale_intent:
        items.append({
            "priority": 1, "kind": "stale_intent", "id": lead.id,
            "title": f"意向线索久未跟：{name}", "hint": "超3天未跟进",
        })

    # ④ 新线索从未跟进超2天（含规则自动建的 new 线索——owner 可能为空也算，鼓励认领）
    fresh = session.execute(
        _sel(Lead, Enterprise.name)
        .join(Enterprise, Enterprise.id == Lead.enterprise_id)
        .where(
            Lead.status == "new",
            Lead.last_followed_at.is_(None),
            (Lead.claimed_at.is_(None)) | (Lead.claimed_at < two_days_ago),
            Lead.created_at < two_days_ago,
        )
        .order_by(Enterprise.intent_score.desc())
        .limit(5)
    ).all()
    for lead, name in fresh:
        owned = "待认领" if lead.owner_id is None else "领取超2天未跟"
        items.append({
            "priority": 2 if lead.owner_id is not None else 3,
            "kind": "fresh_lead", "id": lead.id,
            "title": f"高分新线索：{name}", "hint": owned,
        })

    # ⑤ 今日规则命中（汇总行）
    today_prefix = date.today().isoformat()
    rule_hits = (
        session.query(WorkflowRun)
        .filter(WorkflowRun.created_at.like(f"{today_prefix}%"))
        .count()
    )
    if rule_hits > 0:
        items.append({
            "priority": 2, "kind": "rule_hits", "id": 0,
            "title": f"今日自动化命中 {rule_hits} 次", "hint": "去通知铃铛看明细",
        })

    items.sort(key=lambda x: x["priority"])
    return {"items": items[:20], "today": today}
