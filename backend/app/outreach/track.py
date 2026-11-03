"""邮件打开/点击跟踪（SPEC-AF AF1，listmonk 实践吸收）。

形态与退订 token 同构的无状态签名 token：base64(msg_id|mac)，
mac=sha256(盐:msg_id)[:16] 防篡改，盐复用 settings_kv 的 outreach_secret。

- 打开：1×1 GIF 像素（Apple MPP 预取会虚高——报表以点击率为可靠度优先）
- 点击：正文链接改写到自有域名 + 302 跳转（点击比打开可靠）
- 正文是纯文本模板，发送时才生成 HTML 部分（text/plain 保底 + multipart html）
"""
from __future__ import annotations

import base64
import hashlib
import html as html_mod
import re
from urllib.parse import quote

from sqlalchemy.orm import Session

from .templates import _secret

_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

_URL_RE = re.compile(r"(https?://[^\s<>\"')]+)")


def track_token(session: Session, message_id: int) -> str:
    raw = f"{message_id}|{hashlib.sha256(f'{_secret(session)}:{message_id}'.encode()).hexdigest()[:16]}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def track_message_id(session: Session, token: str) -> int | None:
    """从 token 还原 message_id 并验签；篡改/损坏返回 None。"""
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    mid_s, _, mac = raw.rpartition("|")
    if not mid_s or not mid_s.isdigit():
        return None
    mid = int(mid_s)
    expect = hashlib.sha256(f"{_secret(session)}:{mid}".encode()).hexdigest()[:16]
    return mid if mac == expect else None


def open_url(session: Session, message_id: int) -> str:
    from .templates import public_base

    return f"{public_base(session)}/api/public/track/open/{track_token(session, message_id)}"


def click_url(session: Session, message_id: int, target: str) -> str:
    from .templates import public_base

    return f"{public_base(session)}/api/public/track/click/{track_token(session, message_id)}?u={quote(target, safe='')}"


def to_tracked_html(body: str, session: Session, message_id: int) -> str:
    """纯文本正文 → 带跟踪的 HTML（转义 + 换行 + 链接化 + 像素）。

    顺序敏感：先按 URL 切分、对 URL 原文生成跟踪 <a>（href 用 quote 后的
    原文），非 URL 文本才做 HTML 转义——先整体转义会把 & 变 &amp; 污染跳转目标。
    """
    parts: list[str] = []
    pos = 0
    for m in _URL_RE.finditer(body):
        parts.append(html_mod.escape(body[pos:m.start()], quote=False))
        url = m.group(1)
        parts.append(
            f'<a href="{click_url(session, message_id, url)}">{html_mod.escape(url, quote=False)}</a>'
        )
        pos = m.end()
    parts.append(html_mod.escape(body[pos:], quote=False))
    linked = "".join(parts)
    pixel = f'<img src="{open_url(session, message_id)}" width="1" height="1" alt="" style="display:none"/>'
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,PingFang SC,'
        'Microsoft YaHei,sans-serif;font-size:14px;line-height:1.7;color:#1f2328;white-space:pre-wrap">'
        f"{linked}</div>{pixel}"
    )
