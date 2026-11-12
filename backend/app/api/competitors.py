"""竞对雷达 API（SPEC-BN）：组合级竞对监测——谁家的蛋糕被竞对抢了。

对标剑鱼标讯"竞对监测"付费功能（PRODUCT_GAP §5.3 #3）：is_competitor
的信号早已在库，只缺一个专门视图。金额/买家从公告文本正则回抽，
与 intel/reports 同源口径，全部可溯源不发明数据。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import Contract, Enterprise, Lead, Signal, User, Watch

router = APIRouter(tags=["competitors"])

# 与 intel.py / reports.py 同源：公告文本回抽（可溯源，不发明数据）
_AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")
_BUYER_RE = re.compile(r"采购人[：:]\s*([^\s，,。；;]+)")


def _amount(text: str) -> float:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _sig_date(sig: Signal) -> str:
    """信号归属日：pub_date 优先，created_at 兜底（截到日）。"""
    return (sig.pub_date or sig.created_at or "")[:10]


def _competitor_ids(session: Session) -> list[int]:
    rows = (
        session.query(Enterprise.id)
        .filter(Enterprise.is_competitor == 1, Enterprise.deleted_at.is_(None))
        .all()
    )
    return [r[0] for r in rows]


def _comp_signals(session: Session, ids: list[int], cap: int = 1500) -> list[Signal]:
    """竞对信号（id 降序 cap 条防全表扫）；ids 来自库内查询非用户输入。

    SPEC-BV 体检：feed 页 ≤200 条，窗口过滤在 Python 侧——1500 条上限
    覆盖足够深的历史，首屏从 734ms 降到 ~100ms。
    """
    if not ids:
        return []
    cond = [Signal.enterprise_id == i for i in ids]
    return (
        session.query(Signal)
        .filter(or_(*cond))
        .order_by(Signal.id.desc())
        .limit(cap)
        .all()
    )


@router.get("/competitors/overview")
def competitors_overview(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """KPI：竞对数 / 30 天信号与中标（金额）/ 被抢走的生意（流失线索+金额）。"""
    ids = _competitor_ids(session)
    signals = _comp_signals(session, ids)
    cutoff30 = (date.today() - timedelta(days=30)).isoformat()
    signals_30 = [s for s in signals if _sig_date(s) >= cutoff30]
    wins_30 = [s for s in signals_30 if s.type == "tender_win"]
    win_amount_30 = sum(_amount(s.title + " " + (s.raw_excerpt or "")) for s in wins_30)

    # 被竞对抢走的生意：lost_reason=competitor 的线索 + 关联合同金额
    lost_q = session.query(Lead).filter(Lead.lost_reason == "competitor")
    lost_leads = lost_q.all()
    lost_amount = 0.0
    for ld in lost_leads:
        c = (
            session.query(Contract)
            .filter(Contract.lead_id == ld.id)
            .first()
        )
        if c is not None:
            lost_amount += c.amount_wan or 0.0

    # 30 天最活跃竞对（按中标数）
    win_counts = Counter(s.enterprise_id for s in wins_30)
    top_name = ""
    if win_counts:
        top_id = win_counts.most_common(1)[0][0]
        top_e = session.get(Enterprise, top_id)
        top_name = top_e.name if top_e else ""

    return {
        "competitor_total": len(ids),
        "signals_30d": len(signals_30),
        "wins_30d": len(wins_30),
        "win_amount_30d_wan": round(win_amount_30, 1),
        "lost_to_competitor": len(lost_leads),
        "lost_amount_wan": round(lost_amount, 1),
        "top_competitor": top_name,
    }


@router.get("/competitors/feed")
def competitor_feed(
    type: str = "",
    days: int = 90,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """竞对动态流：时间/竞对/类型/标题/抢单对象/金额，窗口+类型过滤+分页。"""
    days = max(1, min(days, 365))
    limit = max(1, min(limit, 200))
    ids = _competitor_ids(session)
    signals = _comp_signals(session, ids)
    # BV 体检：名字映射只查竞对企业（几十行）——全库 27k 行扫描是首版 734ms 的主因
    names = {
        e.id: e.name
        for e in session.query(Enterprise.id, Enterprise.name)
        .filter(Enterprise.deleted_at.is_(None))
        .filter(or_(*[Enterprise.id == i for i in ids]))
        .all()
    } if ids else {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    items: list[dict] = []
    for s in signals:
        d = _sig_date(s)
        if d < cutoff:
            continue
        if type and s.type != type:
            continue
        text = s.title + " " + (s.raw_excerpt or "")
        stolen_from = ""
        if s.type == "tender_win":
            mb = _BUYER_RE.search(s.evidence_claim or "")
            if mb:
                stolen_from = mb.group(1)
        items.append({
            "id": s.id,
            "enterprise_id": s.enterprise_id,
            "enterprise_name": names.get(s.enterprise_id, ""),
            "type": s.type,
            "title": s.title,
            "pub_date": d,
            "url": s.url,
            "amount_wan": _amount(text),
            "stolen_from": stolen_from,
        })
    total = len(items)
    items = items[offset:offset + limit]
    return {"items": items, "total": total}


@router.get("/competitors")
def competitor_list(
    q: str = "",
    limit: int = 100,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """竞对榜单：逐竞对统计，30 天中标数降序 → 累计信号降序。"""
    limit = max(1, min(limit, 500))
    cond = [Enterprise.is_competitor == 1, Enterprise.deleted_at.is_(None)]
    if q.strip():
        cond.append(Enterprise.name.contains(q.strip()))
    comps = session.query(Enterprise).filter(*cond).all()
    cutoff30 = (date.today() - timedelta(days=30)).isoformat()
    items: list[dict] = []
    for e in comps:
        sigs = (
            session.query(Signal)
            .filter(Signal.enterprise_id == e.id)
            .order_by(Signal.id.desc())
            .limit(2000)
            .all()
        )
        wins = [s for s in sigs if s.type == "tender_win"]
        wins_30 = [s for s in wins if _sig_date(s) >= cutoff30]
        buyers = Counter(
            m.group(1)
            for s in wins
            if (m := _BUYER_RE.search(s.evidence_claim or ""))
        )
        last_date = max((_sig_date(s) for s in sigs), default="")
        items.append({
            "id": e.id,
            "name": e.name,
            "industry_name": e.industry_name,
            "signal_count": len(sigs),
            "win_count": len(wins),
            "win_amount_total_wan": round(
                sum(_amount(s.title + " " + (s.raw_excerpt or "")) for s in wins), 1
            ),
            "wins_30d": len(wins_30),
            "last_signal_date": last_date,
            "top_buyer": buyers.most_common(1)[0][0] if buyers else "",
        })
    items.sort(key=lambda x: (-x["wins_30d"], -x["signal_count"]))
    return {"items": items[:limit], "total": len(items)}


@router.post("/competitors/{eid}/mark")
def mark_competitor(
    eid: int,
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """手动标记/取消竞对（SPEC-BN BN4）：此前只有自动化 tag_competitor 入口。"""
    flag = body.get("flag")
    if flag not in (0, 1, True, False):
        raise HTTPException(422, "flag 须为 0 或 1")
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    e.is_competitor = int(bool(flag))
    session.commit()
    return {"ok": True, "id": eid, "is_competitor": e.is_competitor}


# ---- SPEC-RADAR：官网变更监控雷达（工业获客前兆，借鉴 changedetection.io） ----
# 盯竞对/重点客户的官网页面：新闻页更新=新产线/中标/扩建；招聘页更新=扩张。
# 运行时层（fetch/词表/check_one）在 jobs.py（sweep 与端点共用，防漂移）；
# 端点只做参数校验与持久化编排。

from ..jobs import watch_fetch_text as _watch_fetch  # noqa: E402
from ..jobs import watch_check_one  # noqa: E402


@router.get("/watches")
def list_watches(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """监控列表（含最近变更预览）。"""
    rows = (
        session.query(Watch, Enterprise)
        .join(Enterprise, Enterprise.id == Watch.enterprise_id)
        .filter(Watch.enabled == 1)
        .order_by(Watch.last_changed_at.desc().nulls_last(), Watch.id.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": w.id, "label": w.label, "url": w.url,
                "ent_id": e.id, "ent_name": e.name,
                "last_checked_at": w.last_checked_at,
                "last_changed_at": w.last_changed_at,
                "snapshot_head": w.snapshot_head,
                "check_count": w.check_count,
                "error_last": w.error_last,
            }
            for w, e in rows
        ]
    }


@router.post("/watches")
def add_watch(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """添加监控：立即首采建基线（SSRF 守卫失败 → 400 可读错误）。"""
    import hashlib
    from datetime import datetime

    eid = int(body.get("enterprise_id") or 0)
    label = str(body.get("label") or "").strip()
    url = str(body.get("url") or "").strip()
    if not eid or not label or not url:
        raise HTTPException(422, "enterprise_id / label / url 必填")
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "企业不存在")
    try:
        text = _watch_fetch(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001 — 网络/HTTP 错误转可读
        raise HTTPException(400, f"首采失败：{exc}")
    w = Watch(
        enterprise_id=eid, label=label, url=url,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        snapshot_head=text[:300] or None,
        last_checked_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        check_count=1,
    )
    session.add(w)
    session.commit()
    return {"ok": True, "id": w.id}


@router.get("/watches/{wid}/diff")
def watch_diff(
    wid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """SPEC-RADAR v2：变更前后正文 unified diff（决定是否跟进的关键信息）。"""
    import difflib

    w = session.get(Watch, wid)
    if w is None:
        raise HTTPException(404, "监控不存在")
    if not w.prev_snapshot_text:
        return {"diff": "", "changed_at": w.last_changed_at, "note": "无变更前快照（首采后尚无变更）"}
    diff = difflib.unified_diff(
        (w.prev_snapshot_text or "").splitlines(keepends=False),
        (w.snapshot_text or "").splitlines(keepends=False),
        fromfile="变更前", tofile="变更后", lineterm="", n=2,
    )
    return {
        "diff": "\n".join(diff)[:60000],
        "changed_at": w.last_changed_at,
        "label": w.label,
    }


@router.delete("/watches/{wid}")
def delete_watch(
    wid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    w = session.get(Watch, wid)
    if w is None:
        raise HTTPException(404, "监控不存在")
    w.enabled = 0
    session.commit()
    return {"ok": True}


@router.post("/watches/{wid}/check")
def check_watch(
    wid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """手动立即检查（与 sweep 同逻辑，返回是否变更）。"""
    w = session.get(Watch, wid)
    if w is None or w.enabled != 1:
        raise HTTPException(404, "监控不存在")
    changed = watch_check_one(session, w)
    return {"ok": True, "changed": changed}
