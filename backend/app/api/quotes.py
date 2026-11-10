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


@router.get("/quotes/{qid}/print")
def quote_print(
    qid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """报价单打印页（SPEC-QTE：客户视角 A4，浏览器 Ctrl+P / 另存 PDF）。
    模式照抄 proposal_print；?token= 回退由 auth.current_user 统一支持。"""
    from fastapi.responses import HTMLResponse

    q = session.get(Quote, qid)
    if q is None:
        raise HTTPException(404, "报价单不存在")
    p = _quote_payload(session, q)
    rows_html = "".join(
        f"<tr><td>{i + 1}</td><td>{it['name']}</td>"
        f"<td>{(it.get('spec') or '')[:40]}</td><td class='num'>{it['qty']} {it['price_unit']}</td>"
        f"<td class='num'>{it['price']:,.2f}</td><td class='num'>{it['subtotal']:,.2f}</td></tr>"
        for i, it in enumerate(p["items"])
    )
    valid = p["valid_until"] or "—"
    remark = p["remark"] or ""
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>报价单 #{p['id']} · {p['enterprise_name'] or ''}</title><style>
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:#1a1a1a;margin:40px;font-size:14px}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:#666;font-size:12px;margin-bottom:18px}}
.head{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #1b6c80;padding-bottom:10px;margin-bottom:16px}}
.co{{font-size:16px;font-weight:700;color:#1b6c80}}.meta{{color:#666;font-size:12px;text-align:right}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
th,td{{border:1px solid #ddd;padding:7px 10px;text-align:left;font-size:13px}}
th{{background:#f2f6f7;color:#444}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.total{{margin-top:14px;text-align:right;font-size:18px;font-weight:700}}.total span{{color:#1b6c80}}
.note{{margin-top:10px;color:#555;font-size:12.5px;line-height:1.7}}
.foot{{margin-top:26px;color:#888;font-size:11.5px}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<div class="head">
  <div><div class="co">昊志机械设备（山东）有限公司</div>
  <div class="sub">工业真空系统解决方案 · 报价单</div></div>
  <div class="meta">报价单编号：#{p['id']}<br>创建日期：{(p['created_at'] or '')[:10]}</div>
</div>
<div><b>客户：</b>{p['enterprise_name'] or '—'}</div>
<div class="total">报价总额：<span>{p['total_wan']:,.2f}</span> 万元</div>
<table><thead><tr><th>#</th><th>产品</th><th>规格</th><th>数量</th><th>单价(万)</th><th>小计(万)</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<div class="note">{('有效期至 ' + valid + '。<br>') if valid != '—' else ''}{('备注：' + remark) if remark else ''}</div>
<p class="foot">本报价由昊志机械设备（山东）有限公司出具 · 联系您的销售顾问获取选型计算书与安装方案</p>
<p class="note noprint">打印：浏览器 Ctrl+P / 另存为 PDF（A4）· <a href="/quotes">← 返回报价单列表</a></p>
</body></html>"""
    return HTMLResponse(html)


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
