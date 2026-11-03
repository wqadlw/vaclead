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
from ..models import Contact, Enterprise, Lead, Signal, Todo, User

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
    # SPEC-AE：流失预警数（生命周期已降级 或 健康分<40 的活跃线索）
    at_risk_total = (
        session.query(func.count())
        .select_from(Lead)
        .filter(
            Lead.status.in_(["following", "intent", "won"]),
            (Lead.lifecycle_stage == "at_risk") | (Lead.health_score < 40),
        )
        .scalar()
        or 0
    )
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
        "at_risk": {"total": at_risk_total, "delta": 0},
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
    # SPEC-J J3：报价数是 intent→won 之间的转化环节（发出+赢单的报价单数）
    from ..models import Quote

    quoted = (
        session.query(func.count())
        .select_from(Quote)
        .filter(Quote.status.in_(["sent", "won"]))
        .scalar()
        or 0
    )
    funnel = [
        {"status": s, "label": lb, "value": lead_by_status.get(s, 0)}
        for s, lb in [
            ("new", "新线索"), ("following", "跟进中"), ("intent", "意向"),
            ("quoted", "已报价"), ("won", "成交"), ("pool", "公海"), ("lost", "放弃"),
        ]
        if s in funnel_order or s == "quoted"
    ]
    funnel[3]["value"] = int(quoted)  # quoted 非 lead 状态，单独注入

    # ---- SPEC-J J6 加权管道预测（espocrm Forecasts 思想）----
    # Σ(amount×probability) 按预计成交月分组；对照个人月度目标算达成率
    from ..models import UserTarget

    forecast: dict = {"months": [], "current_month_weighted": 0.0}
    open_rows = (
        session.query(Lead)
        .filter(
            Lead.status.in_(["following", "intent"]),
            Lead.amount_wan.is_not(None),
            Lead.expected_close.is_not(None),
        )
        .all()
    )
    by_month: dict[str, float] = {}
    for lead in open_rows:
        month = str(lead.expected_close)[:7]
        if not month:
            continue
        by_month[month] = by_month.get(month, 0.0) + float(lead.amount_wan) * (
            int(lead.probability or 0) / 100.0
        )
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    forecast["current_month_weighted"] = round(by_month.get(this_month, 0.0), 1)
    forecast["months"] = [
        {"month": m, "weighted": round(v, 1)} for m, v in sorted(by_month.items())
    ][:6]
    # SPEC-AF AF4：在手合同余款（active 合同额 − 已收回款分期）——
    # 赢单后金额从管线消失是盲区，backlog 让它留在预测视野
    from ..models import Contract, PaymentMilestone

    active_ids = [
        cid
        for (cid,) in session.query(Contract.id)
        .filter(Contract.status == "active")
        .all()
    ]
    amount = (
        session.query(func.coalesce(func.sum(Contract.amount_wan), 0.0))
        .filter(Contract.status == "active")
        .scalar()
        or 0.0
    )
    paid = (
        session.query(func.coalesce(func.sum(PaymentMilestone.amount_wan), 0.0))
        .filter(
            PaymentMilestone.contract_id.in_(active_ids or [0]),
            PaymentMilestone.status == "paid",
        )
        .scalar()
        or 0.0
    )
    forecast["contract_backlog"] = round(float(amount) - float(paid), 1)
    target = (
        session.query(UserTarget)
        .filter(UserTarget.user_id == user.id, UserTarget.month == this_month)
        .first()
    )
    forecast["target"] = float(target.amount_wan) if target else None
    forecast["attain"] = (
        round(forecast["current_month_weighted"] / target.amount_wan, 3)
        if target and target.amount_wan > 0
        else None
    )

    return {
        "kpis": kpis, "trend": trend, "sources": sources,
        "geo": geo, "funnel": funnel, "forecast": forecast,
        "health": _followup_health(session),
    }


def _followup_health(session: Session) -> dict:
    """跟进健康度（SPEC-X X3）：活跃线索中超期未跟进的计数与占比。

    口径（调研校准）：status ∈ {following, intent} 且 last_followed_at 距今 > 阈值
    且**无未来到期待办**（有 Todo 在身=有人在管，不算停滞）；
    两级阈值（7 天警示 / 14 天严重，monday CRM 默认 14 天口径的分级版）；
    从未跟进的按 created_at 起算。
    """
    now = datetime.now(timezone.utc)
    from ..models import Todo

    todo_leads = {
        row[0]
        for row in session.query(Todo.lead_id).filter(
            Todo.done == 0, Todo.due_date.is_not(None)
        )
    }

    def _stale(days: int) -> int:
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        rows = (
            session.query(Lead.id)
            .filter(
                Lead.status.in_(["following", "intent"]),
                func.coalesce(Lead.last_followed_at, Lead.created_at) < cutoff,
            )
            .all()
        )
        return sum(1 for (lid,) in rows if lid not in todo_leads)

    active_rows = (
        session.query(Lead.id)
        .filter(Lead.status.in_(["following", "intent"]))
        .all()
    )
    active = len(active_rows)
    stale7 = _stale(7)
    stale14 = _stale(14)
    return {
        "active_leads": active,
        "stale_7d": stale7,
        "stale_14d": stale14,
        "ratio": round(stale7 / active, 3) if active else 0.0,
    }


@router.get("/analytics/conversion")
def conversion_analytics(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """分析环深化（SPEC-X）：阶段转化率（audit_log 重放）+ 来源 ROI（first-touch 快照）。

    - 阶段转化率：重放 lead update 的 status [before→after] 边计数，
      X1 转化率 = 该边发生数 / 前 from 态的 update 事件数；audit 自 W38 起才有数据，
      边数过少时返回 accumulating 状态（前端显示"数据积累中"）
    - 来源 ROI：按企业来源分组的线索数/赢单数/赢单金额/赢单率（won/(won+lost)）
    """
    import json as _json

    from ..models import AuditLog, Quote

    # ---- X1 阶段转化率（重放 status 边）----
    edges: dict[tuple[str, str], int] = {}
    enter_counts: dict[str, int] = {}
    rows = session.execute(
        select(AuditLog.changes_json)
        .where(AuditLog.entity == "lead", AuditLog.action == "update")
        .order_by(AuditLog.id.asc())
        .limit(5000)
    ).all()
    for (changes_json,) in rows:
        try:
            changes = _json_loads(changes_json)
        except ValueError:
            continue
        if "status" in changes:
            before, after = changes["status"][0], changes["status"][1]
            if before and after and before != after:
                edges[(before, after)] = edges.get((before, after), 0) + 1
                enter_counts[after] = enter_counts.get(after, 0) + 1

    stages = [("new", "following"), ("following", "intent"), ("intent", "won")]
    transitions = []
    total_edges = sum(edges.values())
    # 分母口径：曾经到达 frm ≈ 当前停留在 frm（快照补底）+ 曾从 frm 转出（enter_counts）
    snapshot = {
        str(r[0]): int(r[1])
        for r in session.query(Lead.status, func.count()).group_by(Lead.status).all()
    }
    for frm, to in stages:
        edge_n = edges.get((frm, to), 0)
        from_n = enter_counts.get(frm, 0) + snapshot.get(frm, 0)
        rate = round(edge_n / from_n, 3) if from_n else None
        transitions.append({"from": frm, "to": to, "edges": edge_n, "rate": rate})

    conversion = {
        "status": "ok" if total_edges >= 10 else "accumulating",
        "total_edges": total_edges,
        "transitions": transitions,
    }

    # ---- X2 来源 ROI（first-touch：线索按企业来源归属）----
    roi_rows = (
        session.query(Enterprise.source, func.count())
        .join(Lead, Lead.enterprise_id == Enterprise.id)
        .group_by(Enterprise.source)
        .all()
    )
    won_rows = (
        session.query(Enterprise.source, func.count(), func.sum(Lead.amount_wan))
        .join(Lead, Lead.enterprise_id == Enterprise.id)
        .filter(Lead.status == "won")
        .group_by(Enterprise.source)
        .all()
    )
    lost_rows = (
        session.query(Enterprise.source, func.count())
        .join(Lead, Lead.enterprise_id == Enterprise.id)
        .filter(Lead.status == "lost")
        .group_by(Enterprise.source)
        .all()
    )
    lost_map = {str(r[0]): int(r[1]) for r in lost_rows}
    won_map = {str(r[0]): (int(r[1]), float(r[2] or 0)) for r in won_rows}
    roi = []
    for src, leads in roi_rows:
        won_n, won_amount = won_map.get(str(src), (0, 0.0))
        closed = won_n + lost_map.get(str(src), 0)
        roi.append({
            "source": str(src),
            "leads": int(leads),
            "won": won_n,
            "won_amount_wan": round(won_amount, 1),
            "win_rate": round(won_n / closed, 3) if closed else None,
        })
    roi.sort(key=lambda x: (-x["won_amount_wan"], -x["leads"]))

    return {"conversion": conversion, "source_roi": roi}


def _json_loads(raw: str) -> dict:
    import json as _json

    return _json.loads(raw or "{}")


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
