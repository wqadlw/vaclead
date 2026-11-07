"""报表中心（SPEC-O O1）：经营周报的按需生成 + CSV 导出 + 定时邮件摘要。

设计（采纳 espocrm "报表=dashlet+邮件+导出" 单一数据源模式）：
固定 schema payload {period, kpis, funnel, top_signals, top_customers, highlights}，
页面渲染 / CSV 导出 / 邮件正文三处共用同一 build_weekly() 产物。
全部只读聚合；owner 维度排行归 SPEC-N（不在此处）。
"""
from __future__ import annotations

import csv as _csv
import io
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import Contact, Enterprise, Followup, Lead, Signal, User

router = APIRouter(tags=["reports"])

_AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")


def _week_bounds(offset: int) -> tuple[date, date]:
    """offset=0 上周（周一~周日）、1 上上周……返回 [start, end) 日期界。"""
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=offset + 1)
    end = start + timedelta(weeks=1)
    return start, end


def _dt(day: date) -> str:
    """日期 → 当日零点的 ISO 秒级字符串（周窗口用 [start, next_start) 左闭右开）。"""
    return f"{day.isoformat()}T00:00:00"


def _count_in(session: Session, model, lo: str, hi: str) -> int:
    return (
        session.query(func.count())
        .select_from(model)
        .filter(model.created_at >= lo, model.created_at < hi)
        .scalar()
        or 0
    )


def _kpis(session: Session, lo: str, hi: str, prev_lo: str, prev_hi: str) -> list[dict]:
    from ..models import Lead as _Lead

    def _won(lo_: str, hi_: str) -> tuple[int, float]:
        rows = (
            session.query(_Lead.amount_wan)
            .filter(
                _Lead.status == "won",
                _Lead.updated_at >= lo_,
                _Lead.updated_at < hi_,
            )
            .all()
        )
        return len(rows), float(sum(a for (a,) in rows if a) or 0.0)

    specs = [
        ("new_enterprises", "新增企业", Enterprise, None),
        ("new_signals", "新增标讯", Signal, None),
        ("new_leads", "新增线索", Lead, None),
        ("followups", "跟进次数", Followup, None),
    ]
    kpis: list[dict] = []
    for key, label, model, _ in specs:
        value = _count_in(session, model, lo, hi)
        prev = _count_in(session, model, prev_lo, prev_hi)
        delta = round((value - prev) / prev, 3) if prev else (1.0 if value else None)
        kpis.append({"key": key, "label": label, "value": value, "prev": prev, "delta": delta})
    won, won_amount = _won(lo, hi)
    won_prev, won_amount_prev = _won(prev_lo, prev_hi)
    kpis.append({
        "key": "won_count", "label": "赢单数", "value": won, "prev": won_prev,
        "delta": round((won - won_prev) / won_prev, 3) if won_prev else (1.0 if won else None),
    })
    kpis.append({
        "key": "won_amount_wan", "label": "赢单金额(万)", "value": round(won_amount, 1),
        "prev": round(won_amount_prev, 1),
        "delta": round((won_amount - won_amount_prev) / won_amount_prev, 3)
        if won_amount_prev else (1.0 if won_amount else None),
    })
    return kpis


def _funnel(session: Session, lo: str, hi: str) -> list[dict]:
    """本周内"有动静"的线索状态分布（created 或 updated 落在窗口）。"""
    rows = (
        session.query(Lead.status, func.count())
        .filter(
            (Lead.created_at >= lo) & (Lead.created_at < hi)
            | (Lead.updated_at >= lo) & (Lead.updated_at < hi)
        )
        .group_by(Lead.status)
        .all()
    )
    label = {"new": "新线索", "following": "跟进中", "intent": "意向",
             "won": "成交", "pool": "公海", "lost": "放弃"}
    order = ["new", "following", "intent", "won", "pool", "lost"]
    by = {str(r[0]): int(r[1]) for r in rows}
    return [{"status": s, "label": label.get(s, s), "value": by.get(s, 0)} for s in order]


def _top_signals(session: Session, lo: str, hi: str, limit: int = 10) -> list[dict]:
    rows = session.execute(
        select(Signal, Enterprise)
        .join(Enterprise, Signal.enterprise_id == Enterprise.id)
        .filter(Signal.created_at >= lo, Signal.created_at < hi)
        .order_by(Signal.base_score.desc(), Signal.id.desc())
        .limit(60)
    ).all()
    scored = []
    for sig, ent in rows:
        m = _AMOUNT_RE.search(sig.raw_excerpt or "")
        scored.append({
            "title": sig.title, "ent_name": ent.name, "type": sig.type,
            "amount_wan": float(m.group(1)) if m else None,
            "pub_date": sig.pub_date, "base_score": sig.base_score,
        })
    scored.sort(key=lambda x: (x["amount_wan"] is None, -(x["amount_wan"] or 0), -x["base_score"]))
    return scored[:limit]


def _top_customers(session: Session, lo: str, hi: str, limit: int = 10) -> list[dict]:
    rows = (
        session.query(Lead, Enterprise)
        .join(Enterprise, Lead.enterprise_id == Enterprise.id)
        .filter(Lead.created_at >= lo, Lead.created_at < hi)
        .limit(200)
        .all()
    )
    items = []
    for lead, ent in rows:
        composite = round(ent.icp_score * 0.4 + ent.intent_score * 0.6, 1)
        items.append({
            "ent_name": ent.name, "industry": ent.industry_name or ent.industry_key,
            "grade": lead.grade, "composite": composite, "owner_id": lead.owner_id,
        })
    items.sort(key=lambda x: -x["composite"])
    return items[:limit]


def _highlights(kpis: list[dict], won_amount: float) -> list[str]:
    """亮点/风险提示：环比最大变化 + 赢单金额（一句话摘要）。"""
    out: list[str] = []
    notable = [k for k in kpis if k["delta"] is not None and k["key"] != "won_amount_wan"]
    notable.sort(key=lambda k: -abs(k["delta"]))
    for k in notable[:2]:
        arrow = "↑" if k["delta"] > 0 else "↓"
        out.append(f"{k['label']}环比 {arrow} {abs(round(k['delta'] * 100))}%（{k['prev']}→{k['value']}）")
    if won_amount:
        out.append(f"本周赢单金额 {won_amount:.1f} 万元")
    if not out:
        out.append("本周各项指标平稳")
    return out


def _radar_section(session: Session) -> dict:
    """雷达动态节（SPEC-BU BU2）：三雷达+触达漏斗的周报快照（全局口径）。"""
    from datetime import timedelta as _td

    from ..phones import load_phones

    today = date.today()
    d30 = (today - _td(days=30)).isoformat()
    dwin_lo = (today - _td(days=90)).isoformat()
    dwin_hi = (today - _td(days=30)).isoformat()

    win_rows = (
        session.query(Signal)
        .join(Enterprise, Enterprise.id == Signal.enterprise_id)
        .filter(
            Signal.type == "tender_win",
            Enterprise.is_competitor == 1,
            Enterprise.deleted_at.is_(None),
            Signal.created_at >= d30,
        )
        .limit(500)
        .all()
    )
    amount = 0.0
    for sig in win_rows:
        m = _AMOUNT_RE.search(f"{sig.title} {sig.raw_excerpt or ''}")
        if m:
            try:
                amount += float(m.group(1))
            except ValueError:
                pass

    pub_rows = (
        session.query(Signal.pub_date)
        .join(Enterprise, Enterprise.id == Signal.enterprise_id)
        .filter(Signal.type == "proposal", Enterprise.deleted_at.is_(None))
        .limit(5000)
        .all()
    )
    pub_dates = [(r[0] or "")[:10] for r in pub_rows]
    prop_total = len(pub_dates)
    window_now = len([d for d in pub_dates if dwin_lo <= d <= dwin_hi])

    pairs = (
        session.query(Contact.phones_json, Contact.call_result)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(Enterprise.deleted_at.is_(None))
        .all()
    )
    with_phone = dialed = interested = 0
    for pj, cr in pairs:
        if not load_phones(pj):
            continue
        with_phone += 1
        if cr not in (None, "pending"):
            dialed += 1
            if cr == "interested":
                interested += 1

    return {
        "competitor_wins_30d": len(win_rows),
        "competitor_wins_amount_wan": round(amount, 1),
        "proposals_total": prop_total,
        "proposals_window_now": window_now,
        "contact_with_phone": with_phone,
        "contact_dialed": dialed,
        "contact_interested": interested,
    }


def build_weekly(session: Session, offset: int = 0) -> dict:
    """周报单一数据源（页面/CSV/邮件共用）。

    窗口语义：offset=0 → 上周 [start, end)；环比对照上上周 [start-7d, start)。
    """
    start, end = _week_bounds(max(0, offset))
    p_lo, p_hi = _dt(start), _dt(end)
    prev_lo, prev_hi = _dt(start - timedelta(days=7)), _dt(start)
    kpis = _kpis(session, p_lo, p_hi, prev_lo, prev_hi)
    won_amount = next((k["value"] for k in kpis if k["key"] == "won_amount_wan"), 0.0)
    return {
        "period": {
            "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
            "label": f"经营周报 {start.month}.{start.day:02d}-{(end - timedelta(days=1)).day:02d}"
            + ("" if offset == 0 else f"（前推 {offset} 周）"),
        },
        "kpis": kpis,
        "funnel": _funnel(session, p_lo, p_hi),
        "top_signals": _top_signals(session, p_lo, p_hi),
        "top_customers": _top_customers(session, p_lo, p_hi),
        "highlights": _highlights(kpis, float(won_amount)),
        "radar": _radar_section(session),
    }


def _email_text(report: dict) -> str:
    """邮件正文：结论摘要 + KPI 窄表 + 文字漏斗 + 引导（不放全量表）。"""
    kpi_lines = "\n".join(
        f"· {k['label']}：{k['value']}"
        + (f"（上周 {k['prev']}，环比 {round(k['delta'] * 100)}%）" if k["delta"] is not None else "")
        for k in report["kpis"]
    )
    funnel_line = " → ".join(f"{f['label']} {f['value']}" for f in report["funnel"] if f["value"])
    radar = report.get("radar") or {}
    radar_line = (
        f"竞对中标（30天）{radar.get('competitor_wins_30d', 0)} 笔/"
        f"{radar.get('competitor_wins_amount_wan', 0)} 万；拟建窗口 "
        f"{radar.get('proposals_window_now', 0)} 个；触达：已拨 "
        f"{radar.get('contact_dialed', 0)}/有意向 {radar.get('contact_interested', 0)}"
    )
    hl = "\n".join(f"· {h}" for h in report["highlights"])
    return (
        f"【{report['period']['label']}】\n\n"
        f"{report['highlights'][0] if report['highlights'] else ''}\n\n"
        f"核心指标：\n{kpi_lines}\n\n"
        f"线索漏斗：{funnel_line or '本周无动静'}\n\n"
        f"雷达动态：{radar_line}\n\n"
        f"亮点与风险：\n{hl}\n\n"
        f"完整报表（含 Top 标讯/Top 新客户）：登录平台 → 报表中心；支持 CSV 导出。\n"
    )


@router.get("/reports/weekly")
def weekly_report(
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    return build_weekly(session, max(0, offset))


@router.get("/reports/weekly/export")
def weekly_export(
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """周报 CSV（分节：KPI/漏斗/Top标讯/Top客户）。"""
    report = build_weekly(session, max(0, offset))
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow([report["period"]["label"]])
    w.writerow([])
    w.writerow(["== 核心指标 =="])
    w.writerow(["指标", "本周", "上周", "环比%"])
    for k in report["kpis"]:
        w.writerow([k["label"], k["value"], k["prev"],
                    "" if k["delta"] is None else round(k["delta"] * 100)])
    w.writerow([])
    w.writerow(["== 线索漏斗 =="])
    w.writerow(["阶段", "数量"])
    for f in report["funnel"]:
        w.writerow([f["label"], f["value"]])
    w.writerow([])
    w.writerow(["== Top 标讯 =="])
    w.writerow(["标题", "企业", "类型", "金额(万)", "日期"])
    for s in report["top_signals"]:
        w.writerow([s["title"], s["ent_name"], s["type"], s["amount_wan"] or "", s["pub_date"] or ""])
    w.writerow([])
    w.writerow(["== Top 新客户 =="])
    w.writerow(["企业", "行业", "等级", "综合分"])
    for c in report["top_customers"]:
        w.writerow([c["ent_name"], c["industry"] or "", c["grade"] or "", c["composite"]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=weekly_{report['period']['start']}.csv"},
    )


def reports_weekly_sweep() -> dict:
    """每周一 08:30：生成上周周报摘要 → 通知 admin（email 渠道走 J4 emailengine）。"""
    from .db import SessionLocal
    from .notify.channels import notify_dispatch

    with SessionLocal() as s:
        report = build_weekly(s, offset=0)
        admins = s.query(User).filter(User.role == "admin").all()
        notified = 0
        for u in admins:
            notify_dispatch(
                s, u.id, kind="report",
                title=f"经营周报已生成（{report['period']['label']}）",
                body=_email_text(report),
                ref_type="report", ref_id=0,
            )
            notified += 1
        return {"notified": notified, "period": report["period"]["label"]}


# ---------------------------------------------------------------------------
# SPEC-AC AC2：我的周报（per-user）+ 周一个人邮件
# ---------------------------------------------------------------------------
def build_my_week(session: Session, user: User, offset: int = 0) -> dict:
    """个人周报（owner 维度）：我的新线索/跟进/赢单/金额/待办完成/停滞客户。

    结构与全局周报同款（页面/CSV/邮件三处复用）；口径全部 owner_id=本人。
    """
    from ..models import Todo

    start, end = _week_bounds(max(0, offset))
    p_lo, p_hi = _dt(start), _dt(end)
    n_lo, n_hi = _dt(end), _dt(end + timedelta(days=7))
    uid = user.id

    def _mine(model, lo: str, hi: str, owner_col: str = "owner_id") -> int:
        col = getattr(model, owner_col)
        return (
            session.query(func.count())
            .select_from(model)
            .filter(model.created_at >= lo, model.created_at < hi, col == uid)
            .scalar()
            or 0
        )

    def _my_followups(lo: str, hi: str) -> int:
        """Followup 无 user 列：经 Lead.owner_id 归属（join 线索→我的跟进）。"""
        return (
            session.query(func.count())
            .select_from(Followup)
            .join(Lead, Followup.lead_id == Lead.id)
            .filter(
                Lead.owner_id == uid,
                Followup.created_at >= lo,
                Followup.created_at < hi,
            )
            .scalar()
            or 0
        )

    def _won(lo_: str, hi_: str) -> tuple[int, float]:
        rows = (
            session.query(Lead.amount_wan)
            .filter(
                Lead.owner_id == uid,
                Lead.status == "won",
                Lead.updated_at >= lo_,
                Lead.updated_at < hi_,
            )
            .all()
        )
        return len(rows), float(sum(a for (a,) in rows if a) or 0.0)

    kpis = []
    for key, label, fn in [
        ("new_leads", "我的新线索", lambda l, h: _mine(Lead, l, h)),
        ("followups", "我的跟进", _my_followups),
    ]:
        value = fn(p_lo, p_hi)
        prev = fn(n_lo, n_hi)
        kpis.append({
            "key": key, "label": label, "value": value, "prev": prev,
            "delta": round((value - prev) / prev, 3) if prev else (1.0 if value else None),
        })
    won, won_amount = _won(p_lo, p_hi)
    won_prev, won_amount_prev = _won(n_lo, n_hi)
    kpis.append({
        "key": "won_count", "label": "我的赢单", "value": won, "prev": won_prev,
        "delta": round((won - won_prev) / won_prev, 3) if won_prev else (1.0 if won else None),
    })
    kpis.append({
        "key": "won_amount_wan", "label": "我的赢单(万)",
        "value": round(won_amount, 1), "prev": round(won_amount_prev, 1),
        "delta": round((won_amount - won_amount_prev) / won_amount_prev, 3)
        if won_amount_prev else (1.0 if won_amount else None),
    })
    todos_done = (
        session.query(func.count())
        .select_from(Todo)
        .filter(
            Todo.owner_id == uid,
            Todo.done == 1,
            Todo.created_at >= p_lo,
            Todo.created_at < p_hi,
        )
        .scalar()
        or 0
    )
    kpis.append({
        "key": "todos_done", "label": "待办完成", "value": todos_done,
        "prev": todos_done, "delta": None,
    })

    # 停滞客户（我的活跃线索超 7 天未跟进）
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    stale = (
        session.query(func.count())
        .select_from(Lead)
        .filter(
            Lead.owner_id == uid,
            Lead.status.in_(["following", "intent"]),
            func.coalesce(Lead.last_followed_at, Lead.created_at) < cutoff,
        )
        .scalar()
        or 0
    )

    top_leads = (
        session.query(Lead, Enterprise)
        .join(Enterprise, Lead.enterprise_id == Enterprise.id)
        .filter(
            Lead.owner_id == uid,
            Lead.created_at >= p_lo,
            Lead.created_at < p_hi,
        )
        .order_by(Lead.icp_score.desc() if hasattr(Lead, "icp_score") else Lead.id.desc())
        .limit(5)
        .all()
    )
    my_top = [
        {"ent_name": e.name, "status": l.status, "grade": l.grade}
        for l, e in top_leads
    ]

    highlights = []
    if stale:
        highlights.append(f"有 {stale} 条活跃线索超 7 天未跟进")
    if won:
        highlights.append(f"本周赢单 {won} 笔 / {won_amount:.1f} 万元")
    if not highlights:
        highlights.append("本周个人数据平稳")

    return {
        "period": {
            "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
            "label": f"我的周报 {start.month}.{start.day:02d}-{(end - timedelta(days=1)).day:02d}",
        },
        "owner": user.display_name or user.username,
        "kpis": kpis,
        "stale_clients": stale,
        "top_leads": my_top,
        "highlights": highlights,
    }


def _my_email_text(report: dict) -> str:
    kpi_lines = "\n".join(
        f"· {k['label']}：{k['value']}"
        + (f"（上周 {k['prev']}，环比 {round(k['delta'] * 100)}%）" if k["delta"] is not None else "")
        for k in report["kpis"]
    )
    hl = "\n".join(f"· {h}" for h in report["highlights"])
    return (
        f"【{report['period']['label']} · {report['owner']}】\n\n"
        f"{report['highlights'][0] if report['highlights'] else ''}\n\n"
        f"我的本周：\n{kpi_lines}\n\n{hl}\n\n"
        f"完整明细：登录平台 → 报表中心 → 我的周报。\n"
    )


def my_weekly_sweep() -> dict:
    """每周一 08:40：为每个 active 用户生成个人周报摘要 → 邮件推送。"""
    from .db import SessionLocal
    from .notify.channels import notify_dispatch

    notified = skipped = 0
    with SessionLocal() as s:
        users = s.query(User).filter(User.active == 1).all()
        period_label = ""
        for u in users:
            report = build_my_week(s, u, offset=0)
            period_label = report["period"]["label"]
            notify_dispatch(
                s, u.id, kind="report",
                title=f"我的周报（{period_label}）",
                body=_my_email_text(report),
                ref_type="my_report", ref_id=u.id,
            )
            notified += 1
    return {"notified": notified, "period": period_label}


@router.get("/reports/my-week")
def my_week_report(
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from .reports import build_my_week as _build

    return _build(session, user, max(0, offset))
