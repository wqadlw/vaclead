"""Webhooks 管理端点（SPEC-P）：CRUD / 投递日志 / 测试投递 / 手动 flush 任务。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import User, Webhook, WebhookDelivery, utcnow
from ..webhookbus import _url_allowed, flush_deliveries, gen_secret  # noqa: PLC2701

router = APIRouter(tags=["webhooks"])

#: 可订阅事件全集（entity.action），创建/编辑时校验
ALLOWED_EVENTS = {
    f"{ent}.{action}"
    for ent in ("enterprise", "lead", "signal", "quote")
    for action in ("create", "update", "delete")
}


class WebhookIn(BaseModel):
    name: str | None = None
    url: str | None = None
    event_types: list[str] | None = None
    active: bool | None = None


def _mask(secret: str) -> str:
    return secret[:4] + "…"


def _dict(w: Webhook, session: Session) -> dict:
    last = (
        session.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == w.id)
        .order_by(WebhookDelivery.id.desc())
        .first()
    )
    return {
        "id": w.id, "name": w.name, "url": w.url,
        "event_types": json.loads(w.event_types or "[]"),
        "secret_masked": _mask(w.secret), "active": bool(w.active),
        "last_delivery": (
            {"status": last.status, "event": last.event, "attempts": last.attempts,
             "created_at": last.created_at}
            if last else None
        ),
        "created_at": w.created_at,
    }


@router.get("/webhooks")
def list_webhooks(session: Session = Depends(get_session),
                  user: User = Depends(admin_only)):
    rows = session.query(Webhook).order_by(Webhook.id.desc()).limit(100).all()
    return {"items": [_dict(w, session) for w in rows]}


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, session: Session = Depends(get_session),
                   user: User = Depends(admin_only)):
    if not body.name or not body.name.strip():
        raise HTTPException(422, "名称不能为空")
    if not body.url or not _url_allowed(body.url.strip(), session):
        raise HTTPException(422, "URL 不合法：仅 http(s) 且默认禁止内网/回环地址")
    event_types = body.event_types or []
    bad = [e for e in event_types if e not in ALLOWED_EVENTS]
    if bad:
        raise HTTPException(422, f"未知事件类型: {bad}")
    secret = gen_secret()
    w = Webhook(
        name=body.name.strip(), url=body.url.strip(),
        event_types=json.dumps(event_types),
        secret=secret, created_by=user.id,
    )
    session.add(w)
    session.commit()
    return {"id": w.id, "secret": secret}  # 明文 secret 仅此一次返回


@router.put("/webhooks/{wid}")
def update_webhook(wid: int, body: WebhookIn, session: Session = Depends(get_session),
                   user: User = Depends(admin_only)):
    w = session.get(Webhook, wid)
    if w is None:
        raise HTTPException(404, "webhook 不存在")
    if body.name is not None and body.name.strip():
        w.name = body.name.strip()
    if body.url is not None and body.url.strip():
        if not _url_allowed(body.url.strip(), session):
            raise HTTPException(422, "URL 不合法")
        w.url = body.url.strip()
    if body.event_types is not None:
        bad = [e for e in body.event_types if e not in ALLOWED_EVENTS]
        if bad:
            raise HTTPException(422, f"未知事件类型: {bad}")
        w.event_types = json.dumps(body.event_types)
    if body.active is not None:
        w.active = 1 if body.active else 0
    session.commit()
    return {"ok": True}


@router.delete("/webhooks/{wid}")
def delete_webhook(wid: int, session: Session = Depends(get_session),
                   user: User = Depends(admin_only)):
    w = session.get(Webhook, wid)
    if w is None:
        raise HTTPException(404, "webhook 不存在")
    session.delete(w)
    session.commit()
    return {"ok": True}


@router.get("/webhooks/{wid}/deliveries")
def list_deliveries(wid: int, page: int = 1, page_size: int = 50,
                    session: Session = Depends(get_session),
                    user: User = Depends(admin_only)):
    if session.get(Webhook, wid) is None:
        raise HTTPException(404, "webhook 不存在")
    size = min(max(1, page_size), 200)
    q = session.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wid)
    total = q.count()
    rows = (
        q.order_by(WebhookDelivery.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "total": total,
        "items": [
            {"id": d.id, "event": d.event, "status": d.status, "attempts": d.attempts,
             "last_error": d.last_error, "delivered_at": d.delivered_at,
             "created_at": d.created_at}
            for d in rows
        ],
    }


@router.post("/webhooks/{wid}/test")
def test_webhook(wid: int, session: Session = Depends(get_session),
                 user: User = Depends(admin_only)):
    """投递一封 ping 测试事件（走真实投递引擎，可观测结果）。"""
    w = session.get(Webhook, wid)
    if w is None:
        raise HTTPException(404, "webhook 不存在")
    if not w.active:
        raise HTTPException(422, "webhook 已停用")
    from ..models import WebhookDelivery

    # 定向投递测试事件（不经 emit：避免触发其他 webhook 的订阅副作用）
    session.add(WebhookDelivery(
        webhook_id=w.id, event="ping",
        payload=json.dumps({"event": "ping", "entity": "test",
                            "data": {"note": "vaclead webhook 测试事件"}},
                           ensure_ascii=False),
        next_attempt_at=utcnow(),
    ))
    session.commit()
    stats = flush_deliveries(session)
    return {"ok": True, "flush": stats}


@router.post("/tasks/webhook-flush-manual")
def manual_flush(session: Session = Depends(get_session),
                 user: User = Depends(admin_only)):
    """手动触发一轮投递（轻量同步版；SSE 任务版见 /api/tasks/webhook-flush）。"""
    stats = flush_deliveries(session)
    return {"ok": True, **stats}
