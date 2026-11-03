"""合同与回款管理（SPEC-AD）：赢单→签约→回款→续约的后销售链路。

- 合同状态机：draft→active→completed|terminated
- 回款分期：合同 1:N milestones，自动按比例分摊
- 续约提醒：end_date - 30d 自动创建 Todo + 每日 cron 扫描即将到期
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contract, Enterprise, Lead, PaymentMilestone, Todo, User, utcnow

router = APIRouter(tags=["contracts"])

CONTRACT_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "terminated"},
    "active": {"completed", "terminated"},
    "completed": set(),
    "terminated": set(),
}


def _milestones(session: Session, cid: int) -> list[dict]:
    rows = (
        session.query(PaymentMilestone)
        .filter(PaymentMilestone.contract_id == cid)
        .order_by(PaymentMilestone.seq)
        .all()
    )
    return [
        {"id": m.id, "seq": m.seq, "label": m.label, "stage": m.stage,
         "ratio": m.ratio, "amount_wan": m.amount_wan,
         "due_date": m.due_date, "trigger_desc": m.trigger_desc,
         "status": m.status, "paid_date": m.paid_date}
        for m in rows
    ]


def _contract_detail(session: Session, c) -> dict:
    ent = (
        session.query(Enterprise)
        .join(Lead, Lead.enterprise_id == Enterprise.id)
        .filter(Lead.id == c.lead_id)
        .first()
    )
    return {
        "id": c.id, "lead_id": c.lead_id, "quote_id": c.quote_id,
        "name": c.name, "amount_wan": c.amount_wan, "status": c.status,
        "sign_date": c.sign_date, "start_date": c.start_date,
        "end_date": c.end_date, "renewal_date": c.renewal_date,
        "payment_terms": c.payment_terms, "remark": c.remark,
        "owner_id": c.owner_id, "created_at": c.created_at,
        "enterprise_name": ent.name if ent else None,
        "milestones": _milestones(session, c.id),
    }


class ContractIn(BaseModel):
    name: str
    lead_id: int
    quote_id: int | None = None
    amount_wan: float = Field(ge=0)
    sign_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    payment_terms: str | None = None
    remark: str | None = None
    milestones: list[dict] = Field(default_factory=list, max_length=20)
    # 每项: {seq, label, stage, ratio, amount_wan, due_date?, trigger_desc?}


class StatusIn(BaseModel):
    status: str


@router.get("/contracts")
def list_contracts(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    stmt = select(Contract)
    if status:
        stmt = stmt.where(Contract.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Contract.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [_contract_detail(session, c) for c in rows],
            "total": total, "page": page, "page_size": page_size}


@router.get("/contracts/{cid}")
def get_contract(
    cid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    c = session.get(Contract, cid)
    if c is None:
        raise HTTPException(404, "合同不存在")
    return _contract_detail(session, c)


def _set_milestones(session: Session, contract: Contract, items: list[dict]):
    session.query(PaymentMilestone).filter(
        PaymentMilestone.contract_id == contract.id).delete()
    total = 0.0
    for i, m in enumerate(items, 1):
        amount = float(m.get("amount_wan", 0))
        session.add(PaymentMilestone(
            contract_id=contract.id, seq=i,
            label=m.get("label", f"第{i}期"),
            stage=m.get("stage", "payment"),
            ratio=float(m.get("ratio", 0)),
            amount_wan=amount,
            due_date=m.get("due_date"),
            trigger_desc=m.get("trigger_desc"),
        ))
        total += amount
    contract.amount_wan = round(total, 2)


@router.post("/contracts", status_code=201)
def create_contract(
    body: ContractIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    lead = session.get(Lead, body.lead_id)
    if lead is None:
        raise HTTPException(404, "线索不存在")
    if lead.status != "won":
        raise HTTPException(422, "仅赢单线索可创建合同")
    c = Contract(
        lead_id=body.lead_id, quote_id=body.quote_id,
        name=body.name.strip() or f"合同 {utcnow()[:10]}",
        amount_wan=0.0, status="draft",
        start_date=body.start_date, end_date=body.end_date,
        payment_terms=body.payment_terms, remark=body.remark,
        owner_id=user.id,
    )
    session.add(c)
    session.flush()
    _set_milestones(session, c, body.milestones)
    # 续约日期自动计算（end_date - 30 天）
    if body.end_date:
        from datetime import datetime as _dt

        try:
            ed = _dt.strptime(body.end_date, "%Y-%m-%d")
            c.renewal_date = (ed - timedelta(days=30)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    session.commit()
    return {"id": c.id, "total_wan": c.amount_wan}


@router.patch("/contracts/{cid}")
def update_contract(
    cid: int,
    body: ContractIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    c = session.get(Contract, cid)
    if c is None:
        raise HTTPException(404, "合同不存在")
    if c.status not in ("draft",):
        raise HTTPException(422, "仅草稿状态可编辑")
    _set_milestones(session, c, body.milestones)
    c.name = body.name.strip() or c.name
    c.start_date = body.start_date
    c.end_date = body.end_date
    c.payment_terms = body.payment_terms
    c.remark = body.remark
    c.updated_at = utcnow()
    session.commit()
    return {"ok": True, "total_wan": c.amount_wan}


@router.patch("/contracts/{cid}/status")
def transition_contract(
    cid: int,
    body: StatusIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    c = session.get(Contract, cid)
    if c is None:
        raise HTTPException(404, "合同不存在")
    if body.status not in CONTRACT_TRANSITIONS.get(c.status, set()):
        raise HTTPException(422, f"非法迁移 {c.status} → {body.status}")
    c.status = body.status
    c.updated_at = utcnow()
    session.commit()
    return {"ok": True, "status": c.status}


# ---------------------------------------------------------------------------
# 续约提醒自动化（SPEC-AD AD3）
# ---------------------------------------------------------------------------
def check_contract_renewals(session: Session) -> dict:
    """每日扫描即将到期合同 → 自动创建续约 Todo。"""
    today = datetime.now().strftime("%Y-%m-%d")
    warn_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    due = (
        session.query(Contract)
        .filter(
            Contract.status == "active",
            Contract.end_date.is_not(None),
            Contract.end_date <= warn_date,
            Contract.end_date >= today,
        )
        .all()
    )
    created = 0
    for c in due:
        dup = (
            session.query(Todo)
            .filter(
                Todo.title.contains(f"续约：{c.name}"),
                Todo.done == 0,
            )
            .first()
        )
        if dup is not None:
            continue
        session.add(Todo(
            title=f"续约：{c.name}（{c.end_date} 到期）",
            lead_id=c.lead_id, owner_id=c.owner_id,
            due_date=c.end_date, source="automation",
        ))
        created += 1
    session.commit()
    return {"renewal_todos": created, "active_contracts": len(due)}


@router.get("/contracts/summary")
def contract_summary(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """合同概览：总额/回款进度/即将到期。"""
    active = session.query(Contract).filter(Contract.status == "active").all()
    total_amount = sum(c.amount_wan for c in active)
    paid = (
        session.query(func.sum(PaymentMilestone.amount_wan))
        .join(Contract, PaymentMilestone.contract_id == Contract.id)
        .filter(Contract.status == "active", PaymentMilestone.status == "paid")
        .scalar() or 0.0
    )
    return {
        "active_contracts": len(active),
        "total_amount_wan": round(total_amount, 1),
        "paid_wan": round(paid, 1),
        "outstanding_wan": round(total_amount - paid, 1),
    }
