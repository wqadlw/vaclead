"""拟建雷达 API（SPEC-BP）：备案公示信号的专属视图与培育流。

"早"的三级货币化阶梯最早一级（备案→招标→中标）；剑鱼把拟建当核心付费点，
我们粤/鄂通道数据已在库，本模块只做视图与培育语义。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Enterprise, Signal, Todo, User, utcnow

router = APIRouter(tags=["proposals"])

_PROJECT_RE = re.compile(r"「(.+?)」")


def _sig_date(sig: Signal) -> str:
    return (sig.pub_date or sig.created_at or "")[:10]


def _proposal_rows(session: Session, cap: int = 2000):
    """全部 proposal 信号 × 企业（id 降序 cap 条；Python 侧过滤，数百级量级）。"""
    return (
        session.query(Signal, Enterprise)
        .join(Enterprise, Enterprise.id == Signal.enterprise_id)
        .filter(Signal.type == "proposal", Enterprise.deleted_at.is_(None))
        .order_by(Signal.id.desc())
        .limit(cap)
        .all()
    )


@router.get("/proposals/center")
def proposals_center(
    province: str = "",
    q: str = "",
    days: int = 0,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """拟建雷达：KPI（30/90 天新增+招标窗口）+ 分布 + 过滤列表。

    招标窗口语义：备案公示后 1-3 个月进入招标（行业经验值，Todo 取 60 天
    中值）——pub_date 落在 [今日-90, 今日-30] 的备案=正处窗口期。
    """
    rows = _proposal_rows(session)
    today = date.today()
    d30 = (today - timedelta(days=30)).isoformat()
    d90 = (today - timedelta(days=90)).isoformat()

    provinces: Counter[str] = Counter()
    industries: Counter[str] = Counter()
    items: list[dict] = []
    total_30 = 0
    total_90 = 0
    window_now = 0
    qq = q.strip()
    for sig, e in rows:
        d = _sig_date(sig)
        provinces[e.province or "未知"] += 1
        if e.industry_name:
            industries[e.industry_name] += 1
        if d >= d30:
            total_30 += 1
        if d >= d90:
            total_90 += 1
        if d90 <= d <= d30:
            window_now += 1
        # 列表过滤（窗口/地区/关键词/天数）
        if days and d < (today - timedelta(days=days)).isoformat():
            continue
        if province and (e.province or "") != province:
            continue
        project = ""
        m = _PROJECT_RE.search(sig.evidence_claim or "")
        if m:
            project = m.group(1)
        # SPEC-BR：三通道徽章（粤=单位级/鄂=项目级/苏=项目级服务端过滤）
        channel = {"proposal_gd": "gd", "proposal_hb": "hb"}.get(e.source, "js")
        if qq and qq not in (e.name or "") and qq not in (sig.title or "") and qq not in project:
            continue
        items.append({
            "id": sig.id,
            "enterprise_id": e.id,
            "name": e.name,
            "province": e.province,
            "city": e.city,
            "industry_name": e.industry_name,
            "channel": channel,
            "pub_date": d,
            "title": sig.title,
            "project": project,
            "url": sig.url,
        })
    return {
        "overview": {
            "total_30d": total_30,
            "total_90d": total_90,
            "window_now": window_now,
            "province_total": len([k for k in provinces if k != "未知"]),
            "top_province": provinces.most_common(1)[0][0] if provinces else "",
        },
        "provinces": dict(provinces),
        "industries": dict(industries.most_common(8)),
        "items": items[offset:offset + limit],
        "total": len(items),
    }


@router.post("/proposals/{eid}/nurture")
def nurture_proposal(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """拟建培育回访（SPEC-BP BP2，幂等）：备案→60 天后回访 Todo。

    备案后 1-3 个月进入招标窗口——60 天回访踩在窗口开头，正是
    "比招标早一步"的动作语义。
    """
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "企业不存在")
    title = f"拟建回访：{e.name}（备案公示，预计进入招标窗口）"
    exists = (
        session.query(Todo)
        .filter(Todo.title == title, Todo.done == 0)
        .first()
    )
    due = (date.today() + timedelta(days=60)).isoformat()
    if exists is not None:
        return {"ok": True, "created": False, "todo_id": exists.id, "due_date": exists.due_date}
    todo = Todo(
        title=title,
        enterprise_id=e.id,
        due_date=due,
        owner_id=None,
        source="automation",
    )
    session.add(todo)
    session.commit()
    return {"ok": True, "created": True, "todo_id": todo.id, "due_date": due}
