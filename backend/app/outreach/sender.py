"""发信双通道（SPEC-J J4）：emailengine 托管优先，SMTP 直连后备。

send_one 的 send_fn 参数是测试注入点：fn(account, to_email=, subject=, body=)
-> (ok, message_id, error)。生产路径 emailengine 拿远端 messageId，
SMTP 取本地生成的 Message-ID 头。
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid

from sqlalchemy.orm import Session

logger = logging.getLogger("vaclead.outreach.sender")

_SMTP_TIMEOUT = 20


def _smtp_send(account, *, to_email: str, subject: str, body: str,
               unsubscribe_link: str | None = None) -> tuple[str | None, str | None]:
    """SMTP 直连一封（STARTTLS）。返回 (message_id, error)。"""
    msg = MimeMessage()
    msg["From"] = account.email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(account.email.split("@")[-1] or "localhost"))
    if unsubscribe_link:
        # RFC 8058：让主流邮箱客户端展示一键退订
        msg["List-Unsubscribe"] = f"<{unsubscribe_link}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body)
    try:
        with smtplib.SMTP(account.smtp_host, account.smtp_port or 587,
                          timeout=_SMTP_TIMEOUT) as srv:
            srv.starttls()
            if account.smtp_user:
                srv.login(account.smtp_user, account.smtp_pass or "")
            srv.send_message(msg)
        return str(msg["Message-ID"]), None
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("SMTP 发信失败 to=%s: %s", to_email, exc)
        return None, str(exc)


def send_one(session: Session, message, account, *, send_fn=None,
             unsubscribe_link: str | None = None) -> tuple[bool, str | None, str | None]:
    """发送单封，返回 (ok, message_id, error)。"""
    if send_fn is None:
        if account.provider == "emailengine":
            from ..notify.emailengine_client import submit_full

            def send_fn(acc, *, to_email, subject, body, **_kw):
                ok, mid = submit_full(session, to=to_email, subject=subject, text=body)
                return ok, mid, None if ok else "emailengine-submit-failed"
        else:
            def send_fn(acc, *, to_email, subject, body, **kw):
                mid, err = _smtp_send(acc, to_email=to_email, subject=subject,
                                      body=body, unsubscribe_link=kw.get("unsubscribe_link"))
                return mid is not None, mid, err
    return send_fn(account, to_email=message.to_email, subject=message.subject,
                   body=message.body, unsubscribe_link=unsubscribe_link)
