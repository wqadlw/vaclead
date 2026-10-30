"""Webhooks 出站事件总线（SPEC-P，EspoCRM webhooks + svix 最佳实践）。

事件采集分两拍：before_flush 取属性历史（flush 后 history 丢失），after_flush
拿自增主键组包入队（仍在同一事务内，与业务写入原子）；投递由 flush worker
（interval 10 分钟 + 手动任务）异步执行，签名 HMAC-SHA256(原文)。
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import event
from sqlalchemy.orm import Session, attributes

from .models import Webhook, WebhookDelivery, utcnow

logger = logging.getLogger("vaclead.webhookbus")

#: 出站事件白名单（entity.action），emit 之外的一律忽略
TRACKED_ENTITIES = {"enterprise", "lead", "signal", "quote"}

#: update 事件排除的机器字段（对齐 EspoCRM changed-attributes 语义）
_MACHINE_FIELDS = {
    "updated_at", "created_at", "last_followed_at", "claimed_at",
    "next_time", "intent_score", "cursor", "run_count", "last_run_at",
}

_TIMEOUT = httpx.Timeout(5.0)
_MAX_ATTEMPTS = 4
_BATCH_LIMIT = 50


def gen_secret() -> str:
    return secrets.token_hex(24)


def _kv_flag(session: Session, key: str) -> bool:
    from .models import SettingsKV

    row = session.get(SettingsKV, key)
    return bool(row and row.value == "1")


def _url_allowed(url: str, session: Session | None = None) -> bool:
    """SSRF 防护：仅 http(s)；默认拒绝回环/私网/链路本地（webhookAllowedAddressList
    同构）。settings_kv webhook_allow_private=1 显式放行本机联调（如 emailengine）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if session is not None and _kv_flag(session, "webhook" + "_allow_private"):
        return True
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # 域名（非 IP 字面量）：不做 DNS 解析判定（单进程内网部署语义）
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _entity_of(obj) -> str | None:
    name = type(obj).__name__.lower()
    return name if name in TRACKED_ENTITIES else None


def _changes(obj) -> dict:
    """业务字段级 [before, after] diff（机器字段排除）。"""
    changes: dict = {}
    mapper = attributes.instance_state(obj).mapper
    for key in mapper.columns.keys():
        if key in _MACHINE_FIELDS:
            continue
        hist = attributes.get_history(obj, key)
        if hist.has_changes() and hist.deleted:
            changes[key] = [hist.deleted[0], hist.added[0]]
    return changes


def _before_flush(session, _ctx, _instances) -> None:
    """一拍：捕获事件与 update diff（flush 前属性历史才可用），暂存 session.info。"""
    try:
        staged: list[tuple[object, str, dict]] = []
        for obj in session.new:
            ent = _entity_of(obj)
            if ent:
                staged.append((obj, f"{ent}.create", {}))
        for obj in session.dirty:
            if session.is_modified(obj):
                ent = _entity_of(obj)
                if ent:
                    changes = _changes(obj)
                    if changes:
                        staged.append((obj, f"{ent}.update", changes))
        for obj in session.deleted:
            ent = _entity_of(obj)
            if ent:
                staged.append((obj, f"{ent}.delete", {}))
        if staged:
            session.info.setdefault("webhook_events", []).extend(staged)
    except Exception:  # noqa: BLE001 — 事件采集失败绝不阻断业务
        logger.exception("webhook 事件采集异常")


def _after_flush(session, _ctx) -> None:
    """二拍：主键已就绪，组包入队（同事务，随业务提交原子落库）。"""
    staged = session.info.pop("webhook_events", [])
    if not staged:
        return
    try:
        hooks = session.query(Webhook).filter(Webhook.active == 1).all()
        if not hooks:
            return
        for obj, ev, changes in staged:
            entity = ev.split(".")[0]
            data = {
                c: getattr(obj, c)
                for c in attributes.instance_state(obj).mapper.columns.keys()
                if c not in ("password_hash", "smtp_pass")
            }
            payload: dict = {"event": ev, "id": getattr(obj, "id", None),
                             "entity": entity, "data": data}
            if changes:
                payload["changes"] = changes
            body = json.dumps(payload, ensure_ascii=False, default=str)
            for hook in hooks:
                try:
                    subscribed = json.loads(hook.event_types or "[]")
                except ValueError:
                    subscribed = []
                if subscribed and ev not in subscribed:
                    continue
                session.add(WebhookDelivery(
                    webhook_id=hook.id, event=ev, payload=body,
                    next_attempt_at=utcnow(),
                ))
    except Exception:  # noqa: BLE001
        logger.exception("webhook 入队异常")


def emit(session: Session, ev: str, payload: dict) -> int:
    """程序化发事件（公开接口）：入 pending 队列，返回入队数。白名单外忽略。"""
    entity = ev.rsplit(".", 1)[0]
    if entity not in TRACKED_ENTITIES:
        return 0
    hooks = session.query(Webhook).filter(Webhook.active == 1).all()
    queued = 0
    body = json.dumps(payload, ensure_ascii=False, default=str)
    for hook in hooks:
        try:
            subscribed = json.loads(hook.event_types or "[]")
        except ValueError:
            subscribed = []
        if subscribed and ev not in subscribed:
            continue
        session.add(WebhookDelivery(
            webhook_id=hook.id, event=ev, payload=body, next_attempt_at=utcnow(),
        ))
        queued += 1
    return queued


def sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def flush_deliveries(session: Session, *, post_fn=None, now: str | None = None) -> dict:
    """投递引擎：pending 且到期（≤50 条）逐条 POST；指数退避；410 摘钩子。

    post_fn 注入点（测试替身）：fn(url, headers, body_text) -> (status_code:int|None, err:str|None)
    """
    now_s = now or utcnow()
    due = (
        session.query(WebhookDelivery)
        .filter(WebhookDelivery.status == "pending",
                WebhookDelivery.next_attempt_at <= now_s)
        .order_by(WebhookDelivery.id.asc())
        .limit(_BATCH_LIMIT)
        .all()
    )
    stats = {"sent": 0, "failed": 0, "retired": 0, "requeued": 0}
    for d in due:
        hook = session.get(Webhook, d.webhook_id)
        if hook is None or not hook.active:
            d.status = "failed"
            d.last_error = "webhook 已停用"
            stats["failed"] += 1
            continue
        body = json.dumps([json.loads(d.payload)], ensure_ascii=False)
        headers = {
            "Content-Type": "application/json",
            "X-Vaclead-Event": d.event,
            "X-Vaclead-Signature": sign(hook.secret, body),
            "X-Vaclead-Timestamp": now_s,
        }
        if post_fn is None:
            def post_fn(url, headers, body_text):
                try:
                    with httpx.Client(timeout=_TIMEOUT) as client:
                        resp = client.post(url, headers=headers, content=body_text)
                    return resp.status_code, None
                except httpx.HTTPError as exc:
                    return None, str(exc)
        status, err = post_fn(hook.url, headers, body)
        d.attempts += 1
        if status is not None and 200 <= status < 300:
            d.status = "ok"
            d.delivered_at = utcnow()
            d.last_error = None
            stats["sent"] += 1
        elif status == 410:
            hook.active = 0
            d.status = "failed"
            d.last_error = "410 Gone：远端永久移除，webhook 已自动停用"
            stats["failed"] += 1
            stats["retired"] += 1
        elif d.attempts >= _MAX_ATTEMPTS:
            d.status = "failed"
            d.last_error = f"{status or 'error'}: {(err or '')[:200]}"
            d.next_attempt_at = None  # 终态不再排期
            stats["failed"] += 1
        else:
            # 退避基准取传入的 now（生产=真实时钟，测试=模拟时钟），秒级 ISO 对齐 utcnow()
            try:
                base_now = datetime.fromisoformat(now_s)
                if base_now.tzinfo is None:
                    base_now = base_now.replace(tzinfo=timezone.utc)
            except ValueError:
                base_now = datetime.now(timezone.utc)
            backoff = base_now + timedelta(minutes=2 ** d.attempts)
            d.next_attempt_at = backoff.isoformat(timespec="seconds")
            d.last_error = f"{status or 'error'}: {(err or '')[:200]}"
            stats["requeued"] += 1
    session.commit()
    return stats


def install() -> None:
    """注册出站事件钩子（幂等）。"""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
    if not event.contains(Session, "after_flush", _after_flush):
        event.listen(Session, "after_flush", _after_flush)
