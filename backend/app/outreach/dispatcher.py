"""触达调度（SPEC-J J4）：收件同步（回信双链匹配）→ 活动内预算发送。

收件侧只在 emailengine 配置后工作（SMTP 直连无收件 API，回信靠邮箱端人工处理）。
发送逐条短事务提交（SQLite 单写者纪律，SPEC-I I1c）。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import (
    Campaign,
    Contact,
    EmailAccount,
    EmailMessage,
    Enterprise,
    Followup,
    Lead,
    OutreachEvent,
    Unsubscribe,
    utcnow,
)
from .budget import available_budget
from .sender import send_one
from .templates import render, unsubscribe_url

logger = logging.getLogger("vaclead.outreach.dispatcher")

#: auto-reply 词表：命中只记事件不落跟进（防止机器人/系统回执刷销售待办）
_AUTO_REPLY_MARKERS = (
    "auto reply", "automatic reply", "out of office", "自动回复", "自动答复",
    "退订确认", "unsubscribe", "mail delivery", "delivery status", "undeliver",
    "投递失败", "退回", "no-reply", "noreply", "postmaster",
)
_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:自动回复|自动答复|automatic reply|auto reply|re|fw|fwd|回复|转发|答复)\s*[:：]\s*",
    re.IGNORECASE,
)


def _strip_subject_prefixes(subject: str) -> str:
    """循环剥「自动回复：Re: xxx」类前缀，得到可匹配的原始主题。"""
    prev = None
    while prev != subject:
        prev = subject
        subject = _SUBJECT_PREFIX.sub("", subject)
    return subject.strip()


def is_auto_reply(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}".lower()
    return any(m in text for m in _AUTO_REPLY_MARKERS)


def _pick_contact(session: Session, enterprise_id: int, to_email: str) -> Contact | None:
    """收件人对应的联系人行：优先精确邮箱匹配，退而求其次决策人/已验证。"""
    if to_email:
        hit = (
            session.query(Contact)
            .filter(Contact.enterprise_id == enterprise_id, Contact.email == to_email)
            .first()
        )
        if hit is not None:
            return hit
    return (
        session.query(Contact)
        .filter(
            Contact.enterprise_id == enterprise_id,
            Contact.email.is_not(None),
            Contact.email != "",
        )
        .order_by(
            (Contact.decision_role == "decision_maker").desc(),
            (Contact.verify_state == "verified").desc(),
            Contact.id.asc(),
        )
        .first()
    )


def sync_inbox(session: Session, *, list_fn=None) -> dict:
    """收件同步：In-Reply-To↔message_id 精确匹配 + Re: 主题 fallback。

    命中 → message.status=replied + outreach_event(reply) + Followup 落线索时间线；
    auto-reply 只记事件不落跟进。list_fn 注入点（测试替身），生产走 emailengine。
    """
    from ..notify.emailengine_client import is_configured, list_messages

    if list_fn is None and not is_configured(session):
        return {"replies": 0, "skipped": "not-configured"}
    raw = (list_fn or list_messages)(session, limit=50) if list_fn else list_messages(session, limit=50)
    matched = 0
    for m in raw:
        subject = m.get("subject") or ""
        body_text = m.get("text") or m.get("intro") or ""
        in_reply_to = (m.get("inReplyTo") or m.get("in_reply_to") or m.get("references") or "").strip()
        msg: EmailMessage | None = None
        if in_reply_to:
            msg = (
                session.query(EmailMessage)
                .filter(EmailMessage.message_id == in_reply_to)
                .first()
            )
        if msg is None:
            clean = _strip_subject_prefixes(subject)
            if clean != subject.strip() and clean:
                msg = (
                    session.query(EmailMessage)
                    .filter(EmailMessage.subject == clean,
                            EmailMessage.status.in_(["sent", "replied"]))
                    .order_by(EmailMessage.sent_at.desc().nullslast(), EmailMessage.id.desc())
                    .first()
                )
        if msg is None:
            continue
        inbound_uid = str(m.get("id") or m.get("uid") or "")
        dup_q = session.query(OutreachEvent).filter(
            OutreachEvent.message_id == msg.id,
            OutreachEvent.type == "reply",
        )
        if inbound_uid:
            dup_q = dup_q.filter(OutreachEvent.payload.like(f'%"{inbound_uid}"%'))
        elif msg.status == "replied":
            continue  # 无 uid 的来源退化为状态幂等
        if dup_q.first() is not None:
            continue
        msg.status = "replied"
        session.add(OutreachEvent(
            message_id=msg.id, type="reply",
            payload=json.dumps(
                {"uid": inbound_uid, "subject": subject,
                 "from": m.get("from") or "", "date": m.get("date") or "",
                 "text": body_text[:800]},
                ensure_ascii=False,
            ),
        ))
        if not is_auto_reply(subject, body_text):
            session.add(Followup(
                lead_id=msg.lead_id,
                content=f"[自动回信] {subject}\n{body_text[:500]}",
            ))
            lead = session.get(Lead, msg.lead_id)
            if lead is not None:
                lead.last_followed_at = utcnow()
        matched += 1
    session.commit()
    return {"replies": matched}


def enqueue_campaign(session: Session, campaign: Campaign) -> dict:
    """按 target_filter 选在管线索 × 序列步骤生成 email_message（UNIQUE 幂等）。

    退订名单内的邮箱排除；无邮箱联系人跳过并计数。
    """
    from ..models import Sequence

    seq = session.get(Sequence, campaign.sequence_id) if campaign.sequence_id else None
    if seq is None:
        raise ValueError("活动未关联序列")
    steps = json.loads(seq.steps or "[]")
    if not steps:
        raise ValueError("序列无步骤")
    account = session.get(EmailAccount, campaign.email_account_id) if campaign.email_account_id else None
    account_email = account.email if account else ""
    try:
        filt = json.loads(campaign.target_filter or "{}")
    except ValueError:
        filt = {}
    q = (
        session.query(Lead)
        .join(Enterprise, Lead.enterprise_id == Enterprise.id)
        .filter(Lead.status.in_(["new", "following", "intent"]))
    )
    if filt.get("score_min") is not None:
        q = q.filter(Enterprise.icp_score >= filt["score_min"])
    if filt.get("industry"):
        q = q.filter(Enterprise.industry_key == filt["industry"])
    if filt.get("source"):
        q = q.filter(Enterprise.source == filt["source"])
    leads = q.limit(1000).all()
    unsub = {u.email.lower() for u in session.query(Unsubscribe).all()}
    base = datetime.now(timezone.utc).replace(microsecond=0)
    # 微秒归零：utcnow() 是秒级 ISO，带微秒的时间戳字符串比较会错序（. > +）
    created = skipped_dup = skipped_unsub = skipped_noemail = 0
    for lead in leads:
        ent = session.get(Enterprise, lead.enterprise_id)
        if ent is None:
            continue
        contact = _pick_contact(session, ent.id, "")
        to_email = (contact.email or "") if contact else ""
        if not to_email:
            skipped_noemail += 1
            continue
        if to_email.lower() in unsub:
            skipped_unsub += 1
            continue
        step_time = base
        for idx, step in enumerate(steps):
            if idx > 0:
                step_time = step_time + timedelta(days=int(step.get("delay_days", 0)))
            exists = (
                session.query(EmailMessage)
                .filter(EmailMessage.campaign_id == campaign.id,
                        EmailMessage.lead_id == lead.id,
                        EmailMessage.step_idx == idx)
                .first()
            )
            if exists is not None:
                skipped_dup += 1
                continue
            session.add(EmailMessage(
                campaign_id=campaign.id, lead_id=lead.id, step_idx=idx,
                account_email=account_email, to_email=to_email,
                subject=step.get("subject", ""), body=step.get("body", ""),
                scheduled_at=step_time.isoformat(),
            ))
            created += 1
    session.commit()
    return {"created": created, "skipped_dup": skipped_dup,
            "skipped_unsub": skipped_unsub, "skipped_noemail": skipped_noemail}


def run_outreach_dispatch(session: Session, *, send_fn=None, list_fn=None) -> dict:
    """触达调度主入口：收件同步 → running 活动逐封发送（每活动独立预算链）。

    send_fn/list_fn 为测试注入点。返回 totals 供任务 totals/水位落账。
    """
    totals = {"replies": 0, "sent": 0, "failed": 0,
              "skipped_unsub": 0, "no_recipient": 0, "budget_exhausted": 0, "campaigns": 0}
    reply_stats = sync_inbox(session, list_fn=list_fn)
    totals["replies"] = reply_stats.get("replies", 0)
    unsub = {u.email.lower() for u in session.query(Unsubscribe).all()}
    campaigns = session.query(Campaign).filter(Campaign.status == "running").all()
    for camp in campaigns:
        account = session.get(EmailAccount, camp.email_account_id) if camp.email_account_id else None
        if account is None or account.status != "active":
            continue
        totals["campaigns"] += 1
        budget = available_budget(session, account, camp)
        if budget <= 0:
            totals["budget_exhausted"] += (
                session.query(EmailMessage)
                .filter(EmailMessage.campaign_id == camp.id, EmailMessage.status == "queued")
                .count()
            )
            continue
        due = (
            session.query(EmailMessage)
            .filter(EmailMessage.campaign_id == camp.id,
                    EmailMessage.status == "queued",
                    EmailMessage.scheduled_at <= utcnow())
            .order_by(EmailMessage.scheduled_at.asc(), EmailMessage.id.asc())
            .limit(budget)
            .all()
        )
        for msg in due:
            if budget <= 0:
                totals["budget_exhausted"] += 1
                continue
            if msg.to_email.lower() in unsub:
                msg.status = "failed"
                msg.error = "unsubscribed"
                totals["skipped_unsub"] += 1
                session.commit()
                continue
            lead = session.get(Lead, msg.lead_id)
            ent = session.get(Enterprise, lead.enterprise_id) if lead else None
            contact = _pick_contact(session, lead.enterprise_id, msg.to_email) if lead else None
            if not msg.to_email:
                msg.status = "failed"
                msg.error = "no-recipient"
                totals["no_recipient"] += 1
                session.commit()
                continue
            try:
                subject, body = render(
                    session, msg.subject, msg.body, to_email=msg.to_email,
                    enterprise=ent, contact=contact, sender=account,
                )
            except Exception:  # noqa: BLE001 — 渲染失败不炸批
                logger.exception("模板渲染失败 msg=%s", msg.id)
                msg.status = "failed"
                msg.error = "render-error"
                totals["failed"] += 1
                session.commit()
                continue
            msg.subject, msg.body = subject, body  # 回写渲染结果留档
            ok, mid, err = send_one(
                session, msg, account, send_fn=send_fn,
                unsubscribe_link=unsubscribe_url(session, msg.to_email),
            )
            if ok:
                msg.status = "sent"
                msg.sent_at = utcnow()
                msg.message_id = mid
                msg.thread_id = msg.thread_id or mid
                session.add(OutreachEvent(message_id=msg.id, type="sent", payload="{}"))
                totals["sent"] += 1
                budget -= 1
            else:
                msg.status = "failed"
                msg.error = err or "send-failed"
                session.add(OutreachEvent(message_id=msg.id, type="failed",
                                          payload=json.dumps({"error": err or ""})))
                totals["failed"] += 1
            session.commit()
    return totals
