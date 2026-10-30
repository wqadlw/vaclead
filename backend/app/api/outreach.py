"""触达端点（SPEC-J J4）：活动/序列/账户/消息/收件箱/退订名单 + 公开退订页。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import (
    Campaign,
    EmailAccount,
    EmailMessage,
    Enterprise,
    Lead,
    OutreachEvent,
    Sequence,
    Unsubscribe,
    User,
    utcnow,
)
from ..outreach.budget import available_budget, sent_today
from ..outreach.dispatcher import enqueue_campaign
from ..outreach.templates import unsubscribe_email, unsubscribe_url

router = APIRouter(tags=["outreach"])


# ---------------- 发信账户 ----------------
class AccountIn(BaseModel):
    name: str
    email: str
    provider: str = "emailengine"  # emailengine|smtp
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_pass: str | None = None
    warmup_started_at: str | None = None
    daily_cap: int = Field(default=20, ge=1, le=500)


def _account_dict(a: EmailAccount) -> dict:
    return {
        "id": a.id, "name": a.name, "email": a.email, "provider": a.provider,
        "smtp_host": a.smtp_host, "smtp_port": a.smtp_port, "smtp_user": a.smtp_user,
        "warmup_started_at": a.warmup_started_at, "daily_cap": a.daily_cap,
        "status": a.status, "health": a.health,
        "has_password": bool(a.smtp_pass),
    }


@router.get("/outreach/accounts")
def list_accounts(session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.query(EmailAccount).order_by(EmailAccount.id.desc()).limit(100).all()
    return {"items": [_account_dict(a) for a in rows]}


@router.post("/outreach/accounts", status_code=201)
def create_account(body: AccountIn, session: Session = Depends(get_session),
                   user: User = Depends(current_user)):
    if "@" not in body.email:
        raise HTTPException(422, "邮箱格式不正确")
    if body.provider == "smtp" and not body.smtp_host:
        raise HTTPException(422, "SMTP 直连必须提供 smtp_host")
    dup = session.query(EmailAccount).filter(EmailAccount.email == body.email).first()
    if dup is not None:
        raise HTTPException(409, "该邮箱账户已存在")
    a = EmailAccount(**body.model_dump())
    session.add(a)
    session.commit()
    return {"id": a.id}


@router.patch("/outreach/accounts/{aid}")
def update_account(aid: int, body: dict, session: Session = Depends(get_session),
                   user: User = Depends(current_user)):
    a = session.get(EmailAccount, aid)
    if a is None:
        raise HTTPException(404, "账户不存在")
    for k in ("name", "status", "daily_cap", "warmup_started_at", "health"):
        if k in body:
            setattr(a, k, body[k])
    session.commit()
    return {"ok": True}


# ---------------- 序列 ----------------
class SequenceIn(BaseModel):
    name: str
    steps: list[dict] = Field(default_factory=list)


def _seq_dict(s: Sequence) -> dict:
    try:
        steps = json.loads(s.steps or "[]")
    except ValueError:
        steps = []
    return {"id": s.id, "name": s.name, "steps": steps, "created_at": s.created_at}


@router.get("/outreach/sequences")
def list_sequences(session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.query(Sequence).order_by(Sequence.id.desc()).limit(100).all()
    return {"items": [_seq_dict(s) for s in rows]}


@router.post("/outreach/sequences", status_code=201)
def create_sequence(body: SequenceIn, session: Session = Depends(get_session),
                    user: User = Depends(current_user)):
    if not body.name.strip():
        raise HTTPException(422, "序列名不能为空")
    for i, st in enumerate(body.steps):
        if not (st.get("subject") or "").strip():
            raise HTTPException(422, f"第 {i + 1} 步缺少邮件主题")
    s = Sequence(name=body.name.strip(), steps=json.dumps(body.steps, ensure_ascii=False))
    session.add(s)
    session.commit()
    return {"id": s.id}


@router.put("/outreach/sequences/{sid}")
def update_sequence(sid: int, body: SequenceIn, session: Session = Depends(get_session),
                    user: User = Depends(current_user)):
    s = session.get(Sequence, sid)
    if s is None:
        raise HTTPException(404, "序列不存在")
    s.name = body.name.strip() or s.name
    s.steps = json.dumps(body.steps, ensure_ascii=False)
    session.commit()
    return {"ok": True}


@router.delete("/outreach/sequences/{sid}")
def delete_sequence(sid: int, session: Session = Depends(get_session),
                    user: User = Depends(current_user)):
    s = session.get(Sequence, sid)
    if s is None:
        raise HTTPException(404, "序列不存在")
    used = session.query(Campaign).filter(Campaign.sequence_id == sid).count()
    if used:
        raise HTTPException(409, f"被 {used} 个活动引用，先解除关联")
    session.delete(s)
    session.commit()
    return {"ok": True}


# ---------------- 活动 ----------------
class CampaignIn(BaseModel):
    name: str
    sequence_id: int | None = None
    email_account_id: int | None = None
    daily_limit: int = Field(default=50, ge=1, le=1000)
    target_filter: dict = Field(default_factory=dict)


def _campaign_dict(session: Session, c: Campaign) -> dict:
    account = session.get(EmailAccount, c.email_account_id) if c.email_account_id else None
    queued = (
        session.query(EmailMessage)
        .filter(EmailMessage.campaign_id == c.id, EmailMessage.status == "queued")
        .count()
    )
    today = sent_today(session, account.email) if account else 0
    budget = available_budget(session, account, c) if account else 0
    return {
        "id": c.id, "name": c.name, "sequence_id": c.sequence_id,
        "email_account_id": c.email_account_id,
        "account_email": account.email if account else None,
        "status": c.status, "daily_limit": c.daily_limit,
        "target_filter": json.loads(c.target_filter or "{}"),
        "queued": queued, "today_sent": today, "budget": budget,
        "started_at": c.started_at, "created_at": c.created_at,
    }


@router.get("/outreach/campaigns")
def list_campaigns(session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.query(Campaign).order_by(Campaign.id.desc()).limit(100).all()
    return {"items": [_campaign_dict(session, c) for c in rows]}


@router.post("/outreach/campaigns", status_code=201)
def create_campaign(body: CampaignIn, session: Session = Depends(get_session),
                    user: User = Depends(current_user)):
    if not body.name.strip():
        raise HTTPException(422, "活动名不能为空")
    if body.sequence_id and session.get(Sequence, body.sequence_id) is None:
        raise HTTPException(404, "序列不存在")
    if body.email_account_id and session.get(EmailAccount, body.email_account_id) is None:
        raise HTTPException(404, "发信账户不存在")
    c = Campaign(
        name=body.name.strip(), sequence_id=body.sequence_id,
        email_account_id=body.email_account_id, daily_limit=body.daily_limit,
        target_filter=json.dumps(body.target_filter, ensure_ascii=False),
        owner_id=user.id,
    )
    session.add(c)
    session.commit()
    return {"id": c.id}


@router.put("/outreach/campaigns/{cid}")
def update_campaign(cid: int, body: dict, session: Session = Depends(get_session),
                    user: User = Depends(current_user)):
    c = session.get(Campaign, cid)
    if c is None:
        raise HTTPException(404, "活动不存在")
    if "status" in body:
        nxt = body["status"]
        allowed = {"draft": {"running"}, "running": {"paused", "done"}, "paused": {"running", "done"}}
        if nxt not in allowed.get(c.status, set()):
            raise HTTPException(422, f"状态 {c.status} 不能切换到 {nxt}")
        c.status = nxt
        if nxt == "running" and not c.started_at:
            c.started_at = utcnow()
    if "daily_limit" in body:
        c.daily_limit = max(1, min(int(body["daily_limit"]), 1000))
    session.commit()
    return {"ok": True, "status": c.status}


@router.post("/outreach/campaigns/{cid}/enqueue")
def enqueue(cid: int, session: Session = Depends(get_session), user: User = Depends(current_user)):
    c = session.get(Campaign, cid)
    if c is None:
        raise HTTPException(404, "活动不存在")
    if c.status != "running":
        raise HTTPException(422, "仅 running 状态可生成发送队列")
    try:
        result = enqueue_campaign(session, c)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


# ---------------- 消息与收件箱 ----------------
@router.get("/outreach/messages")
def list_messages_page(campaign_id: int | None = None, status: str | None = None,
                       page: int = 1, page_size: int = 50,
                       session: Session = Depends(get_session),
                       user: User = Depends(current_user)):
    q = (
        session.query(EmailMessage, Lead, Enterprise)
        .join(Lead, EmailMessage.lead_id == Lead.id)
        .join(Enterprise, Lead.enterprise_id == Enterprise.id)
    )
    if campaign_id is not None:
        q = q.filter(EmailMessage.campaign_id == campaign_id)
    if status:
        q = q.filter(EmailMessage.status == status)
    size = min(max(1, page_size), 500)
    rows = (
        q.order_by(EmailMessage.id.desc())
        .offset(max(0, (page - 1) * size))
        .limit(size)
        .all()
    )
    return {
        "items": [
            {
                "id": m.id, "campaign_id": m.campaign_id, "step_idx": m.step_idx,
                "enterprise": e.name, "to_email": m.to_email,
                "subject": m.subject, "status": m.status,
                "scheduled_at": m.scheduled_at, "sent_at": m.sent_at,
                "error": m.error,
            }
            for m, _l, e in rows
        ]
    }


@router.get("/outreach/inbox")
def outreach_inbox(session: Session = Depends(get_session), user: User = Depends(current_user)):
    """会话视图：thread_id 归组（无则自身），replied 会话排前。"""
    rows = (
        session.query(EmailMessage, Lead, Enterprise)
        .join(Lead, EmailMessage.lead_id == Lead.id)
        .join(Enterprise, Lead.enterprise_id == Enterprise.id)
        .filter(EmailMessage.status.in_(["sent", "replied"]))
        .order_by(EmailMessage.id.asc())
        .limit(500)
        .all()
    )
    threads: dict[str, dict] = {}
    for m, lead, ent in rows:
        key = m.thread_id or f"m{m.id}"
        th = threads.get(key)
        if th is None:
            th = threads[key] = {
                "thread_id": key, "enterprise": ent.name, "lead_id": lead.id,
                "to_email": m.to_email, "subject": m.subject or "(无主题)",
                "status": "sent", "last_at": m.sent_at or m.created_at,
                "events": [], "reply_body": None,
            }
        if m.status == "replied":
            th["status"] = "replied"
        evs = (
            session.query(OutreachEvent)
            .filter(OutreachEvent.message_id == m.id, OutreachEvent.type == "reply")
            .order_by(OutreachEvent.id.desc())
            .first()
        )
        if evs is not None:
            try:
                payload = json.loads(evs.payload or "{}")
            except ValueError:
                payload = {}
            th["reply_body"] = payload.get("text") or payload.get("subject", "")
            th["reply_at"] = evs.created_at
            th["last_at"] = max(th["last_at"], evs.created_at)
    items = sorted(threads.values(), key=lambda t: (t["status"] != "replied", t["last_at"]),
                   reverse=False)
    return {"items": items[:50]}


# ---------------- 退订名单 ----------------
@router.get("/outreach/unsubscribe-list")
def unsubscribe_list(session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.query(Unsubscribe).order_by(Unsubscribe.id.desc()).limit(200).all()
    return {"items": [
        {"id": u.id, "email": u.email, "lead_id": u.lead_id,
         "reason": u.reason, "created_at": u.created_at} for u in rows
    ]}


@router.delete("/outreach/unsubscribe-list/{email}")
def remove_unsubscribe(email: str, session: Session = Depends(get_session),
                       user: User = Depends(current_user)):
    row = session.query(Unsubscribe).filter(Unsubscribe.email == email).first()
    if row is None:
        raise HTTPException(404, "不在退订名单中")
    session.delete(row)
    session.commit()
    return {"ok": True}


# ---------------- 公开退订页（无鉴权） ----------------
def _page(title: str, body_html: str) -> str:
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#f5f5f4;"
        "display:flex;align-items:center;justify-content:center;min-height:100vh}"
        "main{background:#fff;border:1px solid #e5e5e3;padding:32px;max-width:420px}"
        "h1{font-size:18px;margin:0 0 12px}p{color:#555;line-height:1.6}"
        "button{background:#1b2937;color:#fff;border:0;padding:10px 24px;"
        "cursor:pointer;font-size:14px;margin-top:8px}</style></head>"
        f"<body><main><h1>{title}</h1>{body_html}</main></body></html>"
    )


@router.get("/public/unsubscribe/{token}")
def unsubscribe_page(token: str, session: Session = Depends(get_session)):
    email = unsubscribe_email(session, token)
    if email is None:
        return _page("链接无效", "<p>退订链接无效或已过期。</p>")
    already = session.query(Unsubscribe).filter(Unsubscribe.email == email).first() is not None
    if already:
        return _page("已退订", f"<p><b>{email}</b> 已在退订名单中，不会再收到营销邮件。</p>")
    form = (
        f"<p>确认将 <b>{email}</b> 加入退订名单？退订后不再收到任何营销邮件。</p>"
        f"<form method='post'><button type='submit'>确认退订</button></form>"
    )
    return _page("退订确认", form)


@router.post("/public/unsubscribe/{token}")
def unsubscribe_confirm(token: str, session: Session = Depends(get_session)):
    email = unsubscribe_email(session, token)
    if email is None:
        return _page("链接无效", "<p>退订链接无效或已过期。</p>")
    dup = session.query(Unsubscribe).filter(Unsubscribe.email == email).first()
    if dup is None:
        session.add(Unsubscribe(email=email, reason="user_click"))
        # 关联触达消息补 unsubscribed 事件（最近 20 封，供时间线可见）
        rel = (
            session.query(EmailMessage)
            .filter(EmailMessage.to_email == email)
            .order_by(EmailMessage.id.desc())
            .limit(20)
            .all()
        )
        for m in rel:
            session.add(OutreachEvent(message_id=m.id, type="unsubscribed", payload="{}"))
        session.commit()
    return _page("退订成功", f"<p><b>{email}</b> 已加入退订名单，不会再收到营销邮件。</p>")
