"""EmailEngine 客户端（P2 触达发信契约——配 emailengine_url 即激活）。

照 refs/emailengine API 面实现（lib/api-routes/submit-routes.js）：
- POST /v1/submit {account, from, to, subject, text, html, trackClick, trackOpen}
  返回 {response: 'queued', messageId}；带 idempotencyKey 防重发
- 认证：Authorization: Bearer <token>
- 收件/Webhook（送达/打开事件）在 P2 并排部署时接入

设置键：emailengine_url（如 http://127.0.0.1:3000）/ emailengine_token /
emailengine_account（发信账号 id）/ emailengine_from（发件地址）。
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from ..models import SettingsKV

logger = logging.getLogger("vaclead.notify.emailengine")

_TIMEOUT = httpx.Timeout(15.0)


def _conf(session: Session) -> dict[str, str | None]:
    def g(key: str) -> str | None:
        row = session.get(SettingsKV, key)
        return row.value if row and row.value else None

    return {
        "url": g("emailengine_url"),
        "token": g("emailengine_token"),
        "account": g("emailengine_account"),
        "from": g("emailengine_from"),
    }


def is_configured(session: Session) -> bool:
    c = _conf(session)
    return bool(c["url"] and c["token"] and c["account"] and c["from"])


def submit_full(session: Session, *, to: str, subject: str, text: str,
                html: str | None = None, track: bool = True) -> tuple[bool, str | None]:
    """发信并取回远端 messageId（J4 触达回信归并依赖它）；返回 (ok, message_id)。"""
    if not is_configured(session):
        logger.info("emailengine 未配置，跳过发信")
        return False, None
    c = _conf(session)
    payload: dict = {
        "account": c["account"],
        "from": c["from"],
        "to": [to],
        "subject": subject,
        "text": text,
        "trackClick": track,
        "trackOpen": track,
    }
    if html:
        payload["html"] = html
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                f"{c['url']}/v1/submit",
                json=payload,
                headers={"Authorization": f"Bearer {c['token']}"},
            )
        if resp.status_code == 200 and resp.json().get("response") == "queued":
            return True, resp.json().get("messageId")
        logger.warning("emailengine 发信异常 %s: %s", resp.status_code, resp.text[:120])
        return False, None
    except httpx.HTTPError as exc:
        logger.warning("emailengine 请求失败: %s", exc)
        return False, None


def submit(session: Session, *, to: str, subject: str, text: str,
           html: str | None = None, track: bool = True) -> bool:
    """发信（fire-and-forget 语义：失败记日志返回 False，不抛异常）。"""
    ok, _ = submit_full(session, to=to, subject=subject, text=text,
                        html=html, track=track)
    return ok


def list_messages(session: Session, *, limit: int = 20, folder: str = "INBOX") -> list[dict]:
    """收件箱（P2 收件侧入口：读回复/退订事件）。

    契约：GET /v1/account/{account}/messages?page=0&pageSize=
    """
    if not is_configured(session):
        return []
    c = _conf(session)
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{c['url']}/v1/account/{c['account']}/messages",
                params={"page": 0, "pageSize": limit},
                headers={"Authorization": f"Bearer {c['token']}"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("messages", [])[:limit]
        return []
    except httpx.HTTPError as exc:
        logger.warning("emailengine 收件失败: %s", exc)
        return []
