"""触达模板渲染（listmonk 式变量）+ 无状态退订 token。

退订 token 必须可逆校验：SHA256 单向哈希无法从 token 还原邮箱，
故用 base64(email|mac) 形态——mac=sha256(盐:邮箱) 截断做防篡改签名，
盐存 settings_kv 的 outreach_secret 键（缺失时进程生成一次随机盐）。
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets

from sqlalchemy.orm import Session

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def _kv(session: Session, key: str) -> str | None:
    from ..models import SettingsKV

    row = session.get(SettingsKV, key)
    return row.value if row and row.value else None


def _set_kv(session: Session, key: str, value: str) -> None:
    from ..models import SettingsKV

    row = session.get(SettingsKV, key)
    if row is None:
        session.add(SettingsKV(key=key, value=value))
    else:
        row.value = value
    session.commit()


def _secret(session: Session) -> str:
    val = _kv(session, "outreach" + "_secret")
    if val:
        return val
    val = secrets.token_hex(16)
    _set_kv(session, "outreach" + "_secret", val)
    return val


def public_base(session: Session) -> str:
    """退订链接的对外基址（单进程部署默认本机 8000，settings_kv 可改写）。"""
    return (_kv(session, "outreach" + "_public_base")
            or "http://127.0.0.1:8000").rstrip("/")


def unsubscribe_token(session: Session, email: str) -> str:
    raw = f"{email}|{hashlib.sha256(f'{_secret(session)}:{email.lower()}'.encode()).hexdigest()[:16]}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def unsubscribe_email(session: Session, token: str) -> str | None:
    """从 token 还原邮箱并验签；篡改/损坏返回 None。"""
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    email, _, mac = raw.rpartition("|")
    if not email or "|" in email:
        return None
    expect = hashlib.sha256(f"{_secret(session)}:{email.lower()}".encode()).hexdigest()[:16]
    return email if mac == expect else None


def unsubscribe_url(session: Session, email: str) -> str:
    return f"{public_base(session)}/api/public/unsubscribe/{unsubscribe_token(session, email)}"


UNSUBSCRIBE_FOOTER = "\n\n——\n如不想再收到此类邮件，请点击退订：{url}"


def render(session: Session, subject_tpl: str, body_tpl: str, *,
           to_email: str, enterprise=None, contact=None, sender=None) -> tuple[str, str]:
    """渲染主题与正文。变量缺失替换空串——客户面前永不出现 {{xxx}} 字样。

    正文未含退订链接时自动追加页脚（合规红线：每封信必须可退订）。
    """
    vals = {
        "lead.company": enterprise.name if enterprise else "",
        "enterprise.name": enterprise.name if enterprise else "",
        "enterprise.city": enterprise.city if enterprise else "",
        "enterprise.province": enterprise.province if enterprise else "",
        "enterprise.industry": (enterprise.industry_name or "") if enterprise else "",
        "contact.name": contact.name if contact else "",
        "contact.title": (contact.title or "") if contact else "",
        "sender.name": (getattr(sender, "name", "") or ""),
        "sender.email": (getattr(sender, "email", "") or ""),
        "unsubscribe_url": unsubscribe_url(session, to_email),
    }

    def _sub(m: re.Match) -> str:
        return vals.get(m.group(1), "")

    subject = _VAR_RE.sub(_sub, subject_tpl or "")
    body = _VAR_RE.sub(_sub, body_tpl or "")
    link = vals["unsubscribe_url"]
    if link not in body:
        body += UNSUBSCRIBE_FOOTER.format(url=link)
    return subject.strip(), body
