"""产品目录与报价单（SPEC-J J3，espocrm Quotes 思想）：intent→won 之间的转化单据。

- total_wan 服务端由 items 重算，不信前端传值
- 行项存产品名/价格快照，目录调价不改历史报价
- 状态机：draft → sent → won|lost（sent 终局可双向）
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Enterprise, Product, Quote, QuoteItem, User, Lead, utcnow

router = APIRouter(tags=["quotes"])

QUOTE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"sent", "won", "lost"},
    "sent": {"won", "lost", "draft"},  # 发出后仍可撤回改稿/终局
    "won": set(),
    "lost": {"draft"},  # 输单可复活重报
}


# ---------------- 产品目录 ----------------

class ProductIn(BaseModel):
    name: str
    model: str | None = None
    spec: str | None = None
    price: float = 0.0
    price_unit: str = "台"


@router.get("/products")
def list_products(
    q: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    stmt = select(Product).where(Product.active == 1)
    if q:
        stmt = stmt.where(Product.name.like(f"%{q.strip()}%"))
    rows = session.scalars(stmt.order_by(Product.id.desc()).limit(200)).all()
    return {
        "items": [
            {"id": p.id, "name": p.name, "model": p.model, "spec": p.spec,
             "price": p.price, "price_unit": p.price_unit}
            for p in rows
        ]
    }


@router.post("/products", status_code=201)
def create_product(
    body: ProductIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if not body.name.strip():
        raise HTTPException(422, "产品名不能为空")
    if body.price < 0:
        raise HTTPException(422, "价格不能为负")
    p = Product(name=body.name.strip(), model=body.model, spec=body.spec,
                price=body.price, price_unit=body.price_unit or "台")
    session.add(p)
    session.commit()
    return {"id": p.id}


@router.patch("/products/{pid}")
def update_product(
    pid: int,
    body: ProductIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    p = session.get(Product, pid)
    if p is None:
        raise HTTPException(404, "产品不存在")
    if body.price < 0:
        raise HTTPException(422, "价格不能为负")
    p.name = body.name.strip() or p.name
    p.model = body.model
    p.spec = body.spec
    p.price = body.price
    p.price_unit = body.price_unit or p.price_unit
    session.commit()
    return {"ok": True}


@router.delete("/products/{pid}")
def delete_product(
    pid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    p = session.get(Product, pid)
    if p is None:
        raise HTTPException(404, "产品不存在")
    p.active = 0  # 软删：历史报价行项引用不受影响
    session.commit()
    return {"ok": True}


# ---------------- 报价单 ----------------

class QuoteItemIn(BaseModel):
    product_id: int | None = None
    name: str
    spec: str | None = None
    qty: float = Field(default=1.0, gt=0)
    price: float = Field(default=0.0, ge=0)
    price_unit: str = "台"


class QuoteIn(BaseModel):
    lead_id: int
    valid_until: str | None = None
    remark: str | None = None
    items: list[QuoteItemIn] = Field(default_factory=list, max_length=50)


def _quote_payload(session: Session, q: Quote) -> dict:
    lead = session.get(Lead, q.lead_id)
    ent = session.get(Enterprise, lead.enterprise_id) if lead else None
    items = session.query(QuoteItem).filter(QuoteItem.quote_id == q.id).all()
    return {
        "id": q.id, "lead_id": q.lead_id, "status": q.status,
        "valid_until": q.valid_until, "total_wan": q.total_wan,
        "remark": q.remark, "created_at": q.created_at,
        "enterprise_name": ent.name if ent else None,
        "items": [
            {"id": i.id, "product_id": i.product_id, "name": i.name,
             "spec": i.spec, "qty": i.qty, "price": i.price,
             "price_unit": i.price_unit, "subtotal": round(i.qty * i.price, 2)}
            for i in items
        ],
    }


@router.get("/quotes")
def list_quotes(
    status: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    stmt = select(Quote)
    if status:
        stmt = stmt.where(Quote.status == status)
    rows = session.scalars(stmt.order_by(Quote.id.desc()).limit(200)).all()
    return {"items": [_quote_payload(session, q) for q in rows]}


@router.get("/quotes/{qid}")
def get_quote(
    qid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    q = session.get(Quote, qid)
    if q is None:
        raise HTTPException(404, "报价单不存在")
    return _quote_payload(session, q)


@router.post("/quotes", status_code=201)
def create_quote(
    body: QuoteIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    lead = session.get(Lead, body.lead_id)
    if lead is None:
        raise HTTPException(404, "线索不存在")
    if not body.items:
        raise HTTPException(422, "报价单至少一行")
    q = Quote(lead_id=body.lead_id, status="draft", valid_until=body.valid_until,
              remark=body.remark, owner_id=user.id)
    session.add(q)
    session.flush()
    total = 0.0
    for it in body.items:
        session.add(QuoteItem(
            quote_id=q.id, product_id=it.product_id, name=it.name.strip(),
            spec=it.spec, qty=it.qty, price=it.price,
            price_unit=it.price_unit or "台",
        ))
        total += it.qty * it.price
    q.total_wan = round(total, 2)
    session.commit()
    return {"id": q.id, "total_wan": q.total_wan}


@router.patch("/quotes/{qid}")
def update_quote(
    qid: int,
    body: QuoteIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """改稿（draft 状态）：整单替换行项并重算总额。"""
    q = session.get(Quote, qid)
    if q is None:
        raise HTTPException(404, "报价单不存在")
    if q.status not in ("draft", "lost"):
        raise HTTPException(422, "仅草稿/输单状态可改稿")
    if not body.items:
        raise HTTPException(422, "报价单至少一行")
    session.query(QuoteItem).filter(QuoteItem.quote_id == q.id).delete()
    total = 0.0
    for it in body.items:
        session.add(QuoteItem(
            quote_id=q.id, product_id=it.product_id, name=it.name.strip(),
            spec=it.spec, qty=it.qty, price=it.price,
            price_unit=it.price_unit or "台",
        ))
        total += it.qty * it.price
    q.total_wan = round(total, 2)
    q.valid_until = body.valid_until
    q.remark = body.remark
    q.updated_at = utcnow()
    session.commit()
    return {"ok": True, "total_wan": q.total_wan}


class QuoteStatusIn(BaseModel):
    status: str


@router.patch("/quotes/{qid}/status")
def transition_quote(
    qid: int,
    body: QuoteStatusIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    q = session.get(Quote, qid)
    if q is None:
        raise HTTPException(404, "报价单不存在")
    if body.status not in QUOTE_TRANSITIONS.get(q.status, set()):
        raise HTTPException(422, f"非法状态迁移 {q.status} → {body.status}")
    q.status = body.status
    q.updated_at = utcnow()
    session.commit()
    return {"ok": True, "status": q.status}
