"""企业情报 API（SPEC-D）：采购关系图谱 / 关联企业 / 关键联系人。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, Signal, User

router = APIRouter(tags=["intel"])

_AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")
_BUYER_RE = re.compile(r"采购人[：:]\s*([^\s，,。；;]+)")
_WINNER_RE = re.compile(r"由(.+?)中标")


def _amount(text: str) -> float:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _counterparties(signals: list[Signal]) -> tuple[set[str], set[str]]:
    """从信号证据句反抽（买家集合, 供应商集合）。"""
    buyers: set[str] = set()
    suppliers: set[str] = set()
    for sig in signals:
        text = sig.evidence_claim or ""
        mb = _BUYER_RE.search(text)
        mw = _WINNER_RE.search(text)
        if sig.type == "tender_win" and mb:
            buyers.add(mb.group(1))
        if sig.type == "tender_buy" and mw:
            suppliers.add(mw.group(1))
    return buyers, suppliers


def _my_signals(session: Session, eid: int) -> list[Signal]:
    return (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid, Signal.type.in_(("tender_win", "tender_buy")))
        .all()
    )


@router.get("/enterprises/{eid}/graph")
def enterprise_graph(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """采购关系图谱（SPEC-D 1）：本企业 ↔ 对手方带金额聚合的边。

    tender_win 信号 → 对手方是采购人（我方卖给他）；tender_buy → 对手方是中标方
    （他卖给我方）。金额从证据句回抽，全部可溯源。
    """
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    edges: dict[str, dict] = {}
    for sig in _my_signals(session, eid):
        text = sig.evidence_claim or ""
        mb = _BUYER_RE.search(text)
        mw = _WINNER_RE.search(text)
        if sig.type == "tender_win" and mb:
            counterparty, role = mb.group(1), "buyer"
        elif sig.type == "tender_buy" and mw:
            counterparty, role = mw.group(1), "supplier"
        else:
            continue
        edge = edges.setdefault(
            counterparty,
            {"name": counterparty, "role": role, "amount": 0.0, "count": 0, "last_date": None},
        )
        edge["amount"] += _amount(text)
        edge["count"] += 1
        if sig.pub_date and (edge["last_date"] is None or sig.pub_date > edge["last_date"]):
            edge["last_date"] = sig.pub_date
    edge_list = sorted(edges.values(), key=lambda x: -x["amount"])
    return {"center": {"id": e.id, "name": e.name, "source": e.source}, "edges": edge_list[:12]}


@router.get("/enterprises/{eid}/related")
def related_enterprises(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """关联企业（SPEC-D 3）：共享同一采购人的其他供应商（竞争情报）、
    共享同一中标方的其他采购人（同行动态）。"""
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    my_buyers, my_suppliers = _counterparties(_my_signals(session, eid))
    result: dict[str, list[dict]] = {"competitors": [], "peers": []}
    if my_buyers or my_suppliers:
        candidates = session.query(Enterprise).filter(Enterprise.id != eid).all()
        for c in candidates:
            c_buyers, c_suppliers = _counterparties(_my_signals(session, c.id))
            shared_buyer = my_buyers & c_buyers
            shared_supplier = my_suppliers & c_suppliers
            if shared_buyer:
                result["competitors"].append({
                    "id": c.id, "name": c.name, "via": sorted(shared_buyer)[0],
                    "is_competitor": c.is_competitor,
                })
            elif shared_supplier:
                result["peers"].append({
                    "id": c.id, "name": c.name, "via": sorted(shared_supplier)[0],
                })
    result["competitors"] = result["competitors"][:6]
    result["peers"] = result["peers"][:6]
    return result


@router.get("/enterprises/{eid}/contacts")
def list_contacts(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """关键联系人（SPEC-D 2）：全部带证据溯源，不发明数据。"""
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    rows = session.query(Contact).filter(Contact.enterprise_id == eid).all()
    return {
        "items": [
            {"id": c.id, "name": c.name, "role": c.role, "title": c.title,
             "source": c.source, "evidence_claim": c.evidence_claim}
            for c in rows
        ]
    }
