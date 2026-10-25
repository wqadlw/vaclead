"""通知渠道层（吸收 Novu 多渠道抽象：subscriber 偏好 + 渠道路由）。

Novu 模型：一条通知 → 按 subscriber 的 per-channel 偏好分发（in-app/email/sms/chat）。
vaclead 适配（单进程 SQLite）：
- 渠道注册表 CHANNELS：in_app（已实现）/ email（emailengine client，未配置时跳过）/
  wechat（P3 企微 stub）
- 用户渠道偏好存 settings_kv（key=notify_channels，JSON 数组，默认 ["in_app"]）
- notify_dispatch()：自动化规则与订阅扫播的统一入口——按偏好逐渠道投递，
  渠道失败不互相影响（每渠道独立 try），全部结果可观测。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from ..models import Notification, SettingsKV, User, utcnow

logger = logging.getLogger("vaclead.notify")

#: 渠道注册表（Novu provider registry 的极简形态）
CHANNELS: dict[str, dict] = {
    "in_app": {"label": "站内", "enabled": True},
    "email": {"label": "邮件", "enabled": False},   # 配置 emailengine_url 后启用
    "wechat": {"label": "企微", "enabled": False},  # P3 WEWORK_INTEGRATION 契约
}

DEFAULT_PREFERENCES = ["in_app"]


def user_channels(session: Session, user_id: int) -> list[str]:
    """读用户渠道偏好（settings_kv.notify_channels），非法值回退默认。"""
    row = session.get(SettingsKV, "notify_channels")
    if row and row.value:
        try:
            prefs = json.loads(row.value)
            valid = [c for c in prefs if c in CHANNELS]
            return valid or DEFAULT_PREFERENCES
        except ValueError:
            pass
    return DEFAULT_PREFERENCES


def _in_quiet_hours(session: Session) -> bool:
    """静默时段判断（SPEC-F F1，吸收 novu digest 防轰炸）。

    settings_kv.notify_quiet_hours 格式 "22:00-08:00"（跨零点合法）；未配置=无静默。
    """
    from datetime import datetime

    from ..models import SettingsKV

    row = session.get(SettingsKV, "notify_quiet_hours")
    if not row or not row.value or "-" not in row.value:
        return False
    try:
        start_s, end_s = row.value.split("-")
        now = datetime.now().strftime("%H:%M")
        start, end = start_s.strip(), end_s.strip()
        if start <= end:
            return start <= now < end
        return now >= start or now < end  # 跨零点（如 22:00-08:00）
    except ValueError:
        return False


def _deliver_in_app(session: Session, user_id: int, *, kind: str, title: str,
                    body: str, ref_type: str, ref_id: int) -> str:
    """幂等：同 (ref_type,ref_id,user) 已存在直接返回 dedup（SPEC-E E1-A3）。

    静默时段内非 automation 类通知折入 digest（digested=0 挂起，等 digest_flush
    聚合成单条；automation 类保底放行——规则命中是销售要立刻看的）。
    """
    dup = (
        session.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.ref_type == ref_type,
            Notification.ref_id == ref_id,
        )
        .first()
    )
    if dup is not None:
        return "dedup"
    digested = 0
    if kind != "automation" and _in_quiet_hours(session):
        digested = 1  # 挂起待聚合
    session.add(Notification(
        user_id=user_id, kind=kind, title=title, body=body,
        ref_type=ref_type, ref_id=ref_id,
        digest_key=f"{ref_type}:{ref_id // 1}",  # 单条键；flush 聚合时改写
        digested=digested,
    ))
    return "ok" if not digested else "queued-for-digest"


def _deliver_email(session: Session, user_id: int, *, kind: str, title: str,
                   body: str, ref_type: str, ref_id: int) -> str:
    """邮件渠道（EmailEngine /v1/submit 契约，P2）——未配置时明确跳过。"""
    from .emailengine_client import is_configured, submit

    if not is_configured(session):
        return "skipped:not-configured"
    user = session.get(User, user_id)
    addr = getattr(user, "email", None) if user else None
    if not addr:
        return "skipped:no-address"
    ok = submit(session, to=addr, subject=title, text=body)
    return "ok" if ok else "failed"


def _deliver_wechat(session: Session, user_id: int, *, kind: str, title: str,
                    body: str, ref_type: str, ref_id: int) -> str:
    """企微渠道（P3 WEWORK_INTEGRATION 契约）——未实现明确跳过。"""
    return "skipped:not-implemented"


_DISPATCH = {"in_app": _deliver_in_app, "email": _deliver_email, "wechat": _deliver_wechat}


def notify_dispatch(session: Session, user_id: int, *, kind: str, title: str,
                    body: str, ref_type: str = "custom", ref_id: int = 0) -> dict[str, str]:
    """按用户偏好多渠道投递一条通知（统一入口）。

    返回 {channel: result}；渠道失败不影响其他渠道；in_app 永远投（保底）。
    """
    prefs = user_channels(session, user_id)
    if "in_app" not in prefs:
        prefs.append("in_app")  # 保底渠道不可关
    results: dict[str, str] = {}
    for ch in prefs:
        try:
            results[ch] = _DISPATCH[ch](
                session, user_id, kind=kind, title=title,
                body=body, ref_type=ref_type, ref_id=ref_id,
            )
        except Exception as exc:  # noqa: BLE001 — 单渠道失败不阻断
            results[ch] = f"error:{exc}"
            logger.warning("通知渠道 %s 失败: %s", ch, exc)
    return results


def digest_flush(session: Session) -> dict:
    """聚合挂起通知为单条汇总（SPEC-F F1：每日 08:05 定时）。

    digested=1 的挂起行 → 按用户聚合 → 每用户一条 digest 汇总
    （标题"N 家企业/条动态汇总"，正文列前 8 条）→ 原行标记 digested=2（已折入）。
    """
    pending = (
        session.query(Notification)
        .filter(Notification.digested == 1)
        .order_by(Notification.id.asc())
        .limit(500)
        .all()
    )
    if not pending:
        return {"digested": 0}
    by_user: dict[int, list[Notification]] = {}
    for n in pending:
        by_user.setdefault(n.user_id, []).append(n)
    created = 0
    for uid, items in by_user.items():
        titles = [it.title for it in items[:8]]
        more = f"…等 {len(items)} 条" if len(items) > 8 else f"{len(items)} 条"
        session.add(Notification(
            user_id=uid,
            kind="digest",
            title=f"📋 静默期动态汇总（{len(items)} 条）",
            body="；".join(titles) + more,
            ref_type="digest",
            ref_id=items[0].id,
        ))
        for it in items:
            it.digested = 2  # 已折入
        created += 1
    session.commit()
    return {"digested": len(pending), "digests": created}
