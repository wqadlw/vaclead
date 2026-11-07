"""客户旅程时间线 API（SPEC-BT）：全源合流按时间排好。

打开客户即见完整故事：信号/电话/跟进/备注/报价/合同/回款/装机八源
合流，统一条目结构，ts 降序。Todo 不入时间线（待办是未来不是历史）。
Playbook"客户资产账本"的载体；参照 HubSpot engagements / twenty timeline
（refs/STUDY.md 已深读）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import (
    Contact,
    Contract,
    Enterprise,
    EnterpriseNote,
    Followup,
    InstalledUnit,
    Lead,
    PaymentMilestone,
    Quote,
    Signal,
    User,
)

router = APIRouter(tags=["timeline"])

_CALL_CN = {
    "pending": "未拨",
    "no_answer": "无人接",
    "voicemail": "语音信箱",
    "invalid": "空号",
    "connected": "已接通",
    "interested": "有意向",
    "not_interested": "无需求",
}


def _ts(raw: str | None) -> str:
    return (raw or "")[:19]


@router.get("/enterprises/{eid}/timeline")
def enterprise_timeline(
    eid: int,
    type: str = "",
    limit: int = 60,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """八源合流时间线：每源取 40-80 条上限，ts 降序，全局 limit 60。"""
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "企业不存在")
    limit = max(1, min(limit, 200))

    items: list[dict] = []

    # 1) 信号（公告/备案/中标）
    for s in (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid)
        .order_by(Signal.id.desc())
        .limit(80)
    ):
        items.append({
            "id": f"signal:{s.id}", "type": "signal",
            "ts": _ts(s.pub_date or s.created_at),
            "title": s.title, "detail": s.evidence_claim or "",
            "meta": s.type, "url": s.url or "",
        })

    # 2) 电话触达（联系人有 called_at 即事件）
    for c in (
        session.query(Contact)
        .filter(Contact.enterprise_id == eid, Contact.called_at.is_not(None))
        .order_by(Contact.called_at.desc())
        .limit(40)
    ):
        result_cn = _CALL_CN.get(c.call_result or "", c.call_result or "未拨")
        items.append({
            "id": f"call:{c.id}", "type": "call",
            "ts": _ts(c.called_at),
            "title": f"电话触达：{c.name}",
            "detail": f"结果：{result_cn}" + (f"｜{c.evidence_claim}" if c.evidence_claim else ""),
            "meta": c.call_result or "pending", "url": "",
        })

    # 线索族（followup/quote/contract/payment 经线索挂企业）
    lead_ids = [
        r[0] for r in session.query(Lead.id).filter(Lead.enterprise_id == eid).all()
    ]
    if lead_ids:
        # 3) 跟进记录
        for f in (
            session.query(Followup)
            .filter(or_(*[Followup.lead_id == i for i in lead_ids]))
            .order_by(Followup.id.desc())
            .limit(40)
        ):
            items.append({
                "id": f"followup:{f.id}", "type": "followup",
                "ts": _ts(f.created_at),
                "title": f"跟进记录：{(f.content or '')[:40]}",
                "detail": f.content or "",
                "meta": f.next_time or "", "url": "",
            })
        # 4) 报价
        for q in (
            session.query(Quote)
            .filter(or_(*[Quote.lead_id == i for i in lead_ids]))
            .order_by(Quote.id.desc())
            .limit(40)
        ):
            items.append({
                "id": f"quote:{q.id}", "type": "quote",
                "ts": _ts(q.created_at),
                "title": f"报价单：{q.total_wan} 万（{q.status}）",
                "detail": q.remark or "", "meta": q.status, "url": "",
            })
        # 5) 合同
        contracts = (
            session.query(Contract)
            .filter(or_(*[Contract.lead_id == i for i in lead_ids]))
            .order_by(Contract.id.desc())
            .limit(20)
        ).all()
        for ct in contracts:
            items.append({
                "id": f"contract:{ct.id}", "type": "contract",
                "ts": _ts(ct.sign_date or ct.start_date or ct.created_at),
                "title": f"合同：{ct.name or '未命名'}（{ct.amount_wan} 万，{ct.status}）",
                "detail": ct.payment_terms or "",
                "meta": ct.status, "url": "",
            })
        # 6) 回款分期
        contract_ids = [ct.id for ct in contracts]
        if contract_ids:
            for pm in (
                session.query(PaymentMilestone)
                .filter(or_(*[PaymentMilestone.contract_id == i for i in contract_ids]))
                .order_by(PaymentMilestone.id.desc())
                .limit(40)
            ):
                state = pm.status + (f"，实付 {pm.paid_date}" if pm.paid_date else "")
                items.append({
                    "id": f"payment:{pm.id}", "type": "payment",
                    "ts": _ts(pm.paid_date or pm.due_date or pm.created_at),
                    "title": f"回款：{pm.label or '分期'} {pm.amount_wan} 万（{state}）",
                    "detail": "", "meta": pm.status, "url": "",
                })

    # 7) 备注
    for n in (
        session.query(EnterpriseNote)
        .filter(EnterpriseNote.enterprise_id == eid)
        .order_by(EnterpriseNote.id.desc())
        .limit(40)
    ):
        items.append({
            "id": f"note:{n.id}", "type": "note",
            "ts": _ts(n.created_at),
            "title": f"备注：{(n.content or '')[:40]}",
            "detail": n.content or "", "meta": "", "url": "",
        })

    # 8) 装机
    for u in (
        session.query(InstalledUnit)
        .filter(InstalledUnit.enterprise_id == eid)
        .order_by(InstalledUnit.id.desc())
        .limit(40)
    ):
        items.append({
            "id": f"unit:{u.id}", "type": "unit",
            "ts": _ts(u.install_date or u.created_at),
            "title": f"装机：{u.model}" + (f"（{u.status}）" if u.status else ""),
            "detail": u.notes or "", "meta": u.status or "", "url": "",
        })

    if type:
        items = [i for i in items if i["type"] == type]
    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"items": items[:limit], "total": len(items)}
