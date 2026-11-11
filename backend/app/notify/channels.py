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

import ipaddress
import json
import logging
import socket

import requests
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from ..models import Notification, SettingsKV, Signal, User, utcnow

logger = logging.getLogger("vaclead.notify")

#: 渠道注册表（Novu provider registry 的极简形态）
CHANNELS: dict[str, dict] = {
    "in_app": {"label": "站内", "enabled": True},
    "email": {"label": "邮件", "enabled": False},   # 配置 emailengine_url 后启用
    "wechat": {"label": "企微", "enabled": False},  # P3 WEWORK_INTEGRATION 契约
    "webhook": {"label": "群机器人", "enabled": True},  # SPEC-WH：企微/钉钉/飞书群机器人
}

DEFAULT_PREFERENCES = ["in_app"]

#: SPEC-WH SSRF 边界：只允许三类官方群机器人域名（出站前 _webhook_guard 校验；
#: admin 配置入口 misc.py PUT 同步校验——双端一致）
_WEBHOOK_ALLOWED_HOSTS = {
    "qyapi.weixin.qq.com",   # 企业微信群机器人
    "oapi.dingtalk.com",     # 钉钉群机器人
    "open.feishu.cn",        # 飞书群机器人
}

#: SPEC-WH 群机器人类型白名单（settings_kv.notify_webhook_type）
WEBHOOK_TYPES = ("wecom", "dingtalk", "feishu")

#: 群消息截断上限（SPEC-WH：企微 markdown content 4096 字节限，留余量）
_GROUP_MSG_MAX = 3800


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


DEFAULT_GROUP_TEMPLATE = "**[vaclead] {title}**\n{body}\n{link}"


def render_group_message(title: str, body: str, link: str = "", template: str | None = None) -> str:
    """群消息渲染（SPEC-WH4 模板化）：占位符 {title}/{body}/{link}。

    - template 来自 settings_kv.notify_group_template（管理员自定义，可空）
    - link 为空时 {link} 占位符整行移除（不发空链接行）
    - 超 3800 字符截断（企微 markdown content 4096 字节限，留余量防多字节越界）
    """
    tpl = template if template and template.strip() else DEFAULT_GROUP_TEMPLATE
    if link:
        tpl = tpl.replace("{link}", link)
    else:
        # 空链接时移除含 {link} 的整行（避免发出空行/占位符残留）
        tpl = "\n".join(
            line for line in tpl.split("\n") if "{link}" not in line
        )
    msg = (
        tpl.replace("{title}", title)
        .replace("{body}", body)
    )
    return msg[:_GROUP_MSG_MAX]


def _webhook_payload(wh_type: str, msg: str, title: str) -> dict:
    """按群机器人类型构造 payload（SPEC-WH 3.1 三格式）。

    - wecom：markdown content（默认格式，未知类型也回退到这里，最稳）
    - dingtalk：markdown title + text
    - feishu：纯 text（兼容最稳，绕开各卡片格式版本差异）
    """
    if wh_type == "feishu":
        return {"msg_type": "text", "content": {"text": msg}}
    if wh_type == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": msg}}
    return {"msgtype": "markdown", "markdown": {"content": msg}}


def _webhook_type_of(session: Session) -> str:
    """读群机器人类型（settings_kv.notify_webhook_type），存量空值回退 wecom。"""
    row = session.get(SettingsKV, "notify_webhook_type")
    return row.value if row and row.value else "wecom"


def _webhook_guard(url: str) -> None:
    """SSRF 边界（SPEC-WH，Mimosa high 修复）：四层校验缺一不可。

    1. 协议 https only；2. 官方机器人域名白名单；3. 解析后全部 IP 必须公网
    （阻断私网/环回/链路本地/保留段——防 DNS rebinding 绕白名单）；
    4. requests 侧 allow_redirects=False（防 302 跳内网）。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("webhook 必须 https")
    host = (parsed.hostname or "").lower()
    if host not in _WEBHOOK_ALLOWED_HOSTS:
        raise ValueError(f"webhook 域名不在白名单: {host}")
    for _, _, _, _, sa in socket.getaddrinfo(host, 443):
        ip = ipaddress.ip_address(sa[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"webhook 域名解析到非公网地址: {ip}")


def _webhook_post(url: str, payload: dict):
    """出站 POST——显式禁代理（SPEC-WH：继承"死代理毒链路"教训，后台出站必须绕系统代理）；
    出站前过 _webhook_guard 四层 SSRF 边界；禁重定向。"""
    _webhook_guard(url)
    return requests.post(
        url, json=payload, timeout=10,
        proxies={"http": None, "https": None}, allow_redirects=False,
    )


def _webhook_code(wh_type: str, data: dict) -> int:
    """群机器人响应校验码：wecom/dingtalk 看 errcode；feishu 看 code。"""
    return int(data.get("code", 0) if wh_type == "feishu" else data.get("errcode", 0))


def _platform_base_url(session: Session) -> str:
    """平台访问地址（SPEC-WH v2：群消息带跳转链接——群里看到信号要能点进平台）。
    settings_kv.platform_base_url，缺省本地地址。"""
    row = session.get(SettingsKV, "platform_base_url")
    return (row.value if row and row.value else "http://127.0.0.1:8000").rstrip("/")


def _webhook_url_for(session: Session, ref_type: str) -> str | None:
    """按通知类型路由到不同群（SPEC-WH3 分群版）：

    competitor_win → 竞对群（notify_webhook_competitor_url，老板看竞对动态）；
    未配竞对群 → 落回默认群（notify_webhook_url）；其余一律默认群。"""
    url_row = session.get(SettingsKV, "notify_webhook_url")
    base_url = url_row.value if url_row else None
    if ref_type == "competitor_win":
        comp_row = session.get(SettingsKV, "notify_webhook_competitor_url")
        if comp_row and comp_row.value:
            return comp_row.value
    return base_url


def _deliver_webhook(session: Session, user_id: int, *, kind: str, title: str,
                     body: str, ref_type: str, ref_id: int) -> str:
    """群机器人渠道（SPEC-WH）：全局一个群，销售不打开平台群里就能看到新信号。

    - 未配置 URL → skipped:not-configured（渠道列表 available 跟随）
    - dedup：Notification 存在同 (ref_type, ref_id) 行 → skipped:duplicate
      （群消息不可轰炸；调用方水位防重之上再兜一层）
    - 信号族消息尾带企业详情链接（群→平台动线闭环，SPEC-WH v2）
    - 响应码非 0 → f"failed:{code}"；网络异常直接抛（notify_dispatch per-channel try 兜）
    """
    url_row = session.get(SettingsKV, "notify_webhook_url")
    url = _webhook_url_for(session, ref_type)
    if not url:
        return "skipped:not-configured"
    dup = (
        session.query(Notification)
        .filter(
            Notification.ref_type == ref_type,
            Notification.ref_id == ref_id,
        )
        .first()
    )
    if dup is not None:
        return "skipped:duplicate"
    wh_type = _webhook_type_of(session)
    tpl_row = session.get(SettingsKV, "notify_group_template")
    template = tpl_row.value if tpl_row and tpl_row.value else None
    # 信号族 → 构造企业详情链接（SPEC-WH4：链接作为模板 {link} 占位符注入）
    link = ""
    if ref_type in ("signal", "sub_signal", "owner_signal", "competitor_win"):
        row = session.execute(
            sa_select(Signal.enterprise_id).where(Signal.id == ref_id)
        ).first()
        if row and row[0]:
            base = _platform_base_url(session)
            link = f"[查看企业档案 →]({base}/enterprises/view?id={row[0]})"
    msg = render_group_message(title, body, link=link, template=template)
    payload = _webhook_payload(wh_type, msg, title)
    resp = _webhook_post(url, payload)
    code = _webhook_code(wh_type, resp.json())
    if code != 0:
        return f"failed:{code}"
    return "ok"


def _deliver_webhook_test(session: Session, url: str, wh_type: str) -> dict:
    """发固定测试消息（SPEC-WH 3.1 + WH4 模板），返回 {"ok": bool, "detail": str}。

    供 POST /api/notify-webhook/test 复用；任何失败都不抛——HTTP 层永远 200。
    测试消息按已存模板渲染（管理员预览的就是真实群消息形态）。"""
    from ..db import get_session as _gs

    tpl_row = session.get(SettingsKV, "notify_group_template")
    template = tpl_row.value if tpl_row and tpl_row.value else None
    msg = render_group_message(
        "测试消息",
        "群机器人通道已打通——这条消息按你配置的模板渲染",
        link=f"[查看企业档案 →]({_platform_base_url(session)}/enterprises/view?id=1)",
        template=template,
    )
    payload = _webhook_payload(wh_type, msg, "测试消息")
    try:
        resp = _webhook_post(url, payload)
    except Exception as exc:  # noqa: BLE001 — 测试端点不 500，失败也 200
        return {"ok": False, "detail": f"error:{exc}"}
    try:
        code = _webhook_code(wh_type, resp.json())
    except ValueError:  # 非 JSON 响应（反代错误页等）
        return {"ok": False, "detail": f"failed:http-{resp.status_code}"}
    if code != 0:
        return {"ok": False, "detail": f"failed:{code}"}
    return {"ok": True, "detail": "ok"}


_DISPATCH = {
    "in_app": _deliver_in_app,
    "email": _deliver_email,
    "wechat": _deliver_wechat,
    "webhook": _deliver_webhook,  # SPEC-WH 群机器人
}


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
