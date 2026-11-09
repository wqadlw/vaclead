"""杂项路由：登录 / 设置 / Agent 日志。"""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import admin_only, create_token, current_user, verify_password
from ..db import get_session
from ..models import AgentRun, Enterprise, Lead, PoolSetting, SettingsKV, Signal, User

router = APIRouter(tags=["misc"])


@router.get("/meta/vertical")
def vertical_meta(user: User = Depends(current_user)):
    """垂直词表元数据（采集中心下拉的唯一数据源，避免前端硬编码副本漂移）。"""
    from ..vertical import (
        device_strong_terms,
        device_weak_terms,
        downstream_industries,
        ggzy_name_terms,
    )

    return {
        "strong_terms": device_strong_terms(),
        "weak_terms": device_weak_terms(),
        "ggzy_terms": ggzy_name_terms(),
        "industries": downstream_industries(),
    }


@router.get("/health")
def health(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """平台就绪度聚合（学 OpenOutreach `status --json` 契约）：
    onboarding 缺什么、各实体计数、被什么阻塞、建议的下一步。"""
    from ..collectors import amap_poi, tender  # 探测能力位

    amap_key = _get(session, "amap_key")
    zhil_key = _get(session, "zhiliaoke_key")
    counts = {
        "enterprises": session.query(func.count()).select_from(Enterprise).scalar() or 0,
        "signals": session.query(func.count()).select_from(Signal).scalar() or 0,
        "leads": session.query(func.count()).select_from(Lead).scalar() or 0,
    }
    missing: list[str] = []
    if not amap_key:
        missing.append("amap_key")
    if not zhil_key:
        missing.append("zhiliaoke_key")

    # next_action 决策树：按"当前最该做的事"排序
    if counts["enterprises"] == 0:
        next_action = {"action": "import_enterprises", "hint": "在拓客任务页导入企业 CSV 或启动 POI 采集"}
    elif counts["signals"] == 0:
        next_action = {"action": "import_tenders", "hint": "导入招标公告 CSV，命中设备词自动生成意图信号"}
    elif not amap_key:
        next_action = {"action": "configure_amap", "hint": "配置高德 Key 解锁地图拓客"}
    else:
        next_action = {"action": "filter_enterprises", "hint": "去企业筛选页按信号和评分找客户"}

    return {
        "version": "0.3",
        "onboarding_complete": len(missing) == 0,
        "missing_config": missing,
        "capabilities": {
            "poi_quadtree": bool(amap_key),
            "tender_api": bool(zhil_key),
            "tender_csv": True,
            "llm_backends": False,  # P2：配置 GLM Key 后为 True
        },
        "counts": counts,
        "next_action": next_action,
    }


class PoolSettingIn(BaseModel):
    recycle_days: int | None = None
    receive_limit: int | None = None
    remind_days: int | None = None


def _get_pool(session: Session) -> PoolSetting:
    pool = session.query(PoolSetting).filter(PoolSetting.active == 1).first()
    if pool is None:
        pool = PoolSetting(name="默认公海")
        session.add(pool)
        session.commit()
        session.refresh(pool)
    return pool


@router.get("/pool-settings")
def get_pool_settings(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    pool = _get_pool(session)
    return {
        "name": pool.name,
        "recycle_days": pool.recycle_days,
        "receive_limit": pool.receive_limit,
        "remind_days": pool.remind_days,
    }


@router.put("/pool-settings")
def put_pool_settings(
    body: PoolSettingIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    pool = _get_pool(session)
    if body.recycle_days is not None:
        if not 1 <= body.recycle_days <= 365:
            raise HTTPException(422, "回收天数需在 1-365 之间")
        pool.recycle_days = body.recycle_days
    if body.receive_limit is not None:
        if body.receive_limit < 0 or body.receive_limit > 999:
            raise HTTPException(422, "领取上限需在 0-999 之间（0 为不限）")
        pool.receive_limit = body.receive_limit
    if body.remind_days is not None:
        if not 0 <= body.remind_days <= 30:
            raise HTTPException(422, "提醒天数需在 0-30 之间")
        pool.remind_days = body.remind_days
    session.commit()
    return {"ok": True}


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(
    body: LoginIn,
    request: Request = None,  # type: ignore[assignment]
    session: Session = Depends(get_session),
):
    from ..auth import clear_login_failures, login_rate_limited, record_login_failure

    if request is not None:
        login_rate_limited(request)
    user = session.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        if request is not None:
            record_login_failure(request)
        raise HTTPException(401, "用户名或密码错误")
    if not user.active:  # SPEC-N N1：禁用账号拒登录
        raise HTTPException(401, "账号已禁用，请联系管理员")
    if request is not None:
        clear_login_failures(request)
    return {
        "token": create_token(user),
        "role": user.role,
        "display_name": user.display_name or user.username,
    }


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:4]}****{value[-4:]}" if len(value) > 8 else "****"


@router.get("/settings")
def get_settings(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    amap = _get(session, "amap_key")
    zhil = _get(session, "zhiliaoke_key")
    try:
        backends = __import__("json").loads(_get(session, "agent_backends") or "{}")
    except Exception:
        backends = {}
    return {
        "amap_key_masked": _mask(amap),
        "zhiliaoke_key_masked": _mask(zhil),
        "agent_backends": backends or {"signal_parser": "rule", "icp_judge": "rule"},
    }


def _get(session: Session, key: str) -> str | None:
    row = session.get(SettingsKV, key)
    return row.value if row else None


class SettingsPut(BaseModel):
    amap_key: str | None = None
    zhiliaoke_key: str | None = None


@router.put("/settings")
def put_settings(
    body: SettingsPut,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    for key, value in (("amap_key", body.amap_key), ("zhiliaoke_key", body.zhiliaoke_key)):
        if value:
            row = session.get(SettingsKV, key)
            if row is None:
                session.add(SettingsKV(key=key, value=value))
            else:
                row.value = value
    session.commit()
    return {"ok": True}


class NotifyPrefsIn(BaseModel):
    channels: list[str]  # ["in_app"] / ["in_app","email"] / 含 wechat


def _mask_webhook_url(url: str | None) -> str | None:
    """群机器人 URL 脱敏（SPEC-WH）：保留头 60 尾 12，中段打码。

    过短（<=72 位）时 key 必在段内，整体只露前 8 位防泄漏。
    """
    if not url:
        return None
    if len(url) <= 72:
        return f"{url[:8]}***"
    return f"{url[:60]}***{url[-12:]}"


@router.get("/notify-preferences")
def get_notify_prefs(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """渠道偏好（吸收 Novu subscriber preferences）+ 渠道注册表。

    SPEC-WH：webhook 渠道 available = URL 已配置（其余渠道保持注册表 enabled）；
    响应附 webhook 配置摘要（URL 脱敏）。
    """
    from ..notify.channels import CHANNELS, user_channels

    wh_url = _get(session, "notify_webhook_url")
    channels = [
        {
            "key": k,
            "label": v["label"],
            "available": bool(wh_url) if k == "webhook" else v["enabled"],
        }
        for k, v in CHANNELS.items()
    ]
    return {
        "channels": channels,
        "preferences": user_channels(session, user.id),
        "webhook": {
            "url": _mask_webhook_url(wh_url),
            "type": _get(session, "notify_webhook_type") or "wecom",
            "configured": bool(wh_url),
        },
    }


@router.put("/notify-preferences")
def put_notify_prefs(
    body: NotifyPrefsIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from ..notify.channels import CHANNELS

    valid = [c for c in body.channels if c in CHANNELS]
    if not valid:
        raise HTTPException(422, "至少选择一个合法渠道")
    row = session.get(SettingsKV, "notify_channels")
    if row is None:
        session.add(SettingsKV(key="notify_channels", value=json.dumps(valid)))
    else:
        row.value = json.dumps(valid)
    session.commit()
    return {"ok": True, "preferences": valid}


# ---------------- 群机器人 Webhook（SPEC-WH） ----------------
class NotifyWebhookIn(BaseModel):
    url: str
    type: str = "wecom"


@router.put("/notify-webhook")
def put_notify_webhook(
    body: NotifyWebhookIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """群机器人配置（SPEC-WH）：URL 必须 https、type 白名单 → 写 settings_kv 两键。

    key：notify_webhook_url / notify_webhook_type（wecom|dingtalk|feishu）。
    SSRF 边界：域名白名单双端校验的配置入口侧——只接受三类官方机器人域名。
    """
    from urllib.parse import urlparse

    from ..notify.channels import WEBHOOK_TYPES, _WEBHOOK_ALLOWED_HOSTS

    if not body.url.startswith("https://"):
        raise HTTPException(422, "机器人地址必须以 https:// 开头")
    host = (urlparse(body.url).hostname or "").lower()
    if host not in _WEBHOOK_ALLOWED_HOSTS:
        raise HTTPException(422, f"机器人域名须为官方地址（企微/钉钉/飞书），拒绝 {host or '空'}")
    if body.type not in WEBHOOK_TYPES:
        raise HTTPException(422, "type 须为 wecom/dingtalk/feishu")
    for key, value in (("notify_webhook_url", body.url), ("notify_webhook_type", body.type)):
        row = session.get(SettingsKV, key)
        if row is None:
            session.add(SettingsKV(key=key, value=value))
        else:
            row.value = value
    session.commit()
    return {"ok": True}


@router.post("/notify-webhook/test")
def test_notify_webhook(
    body: dict | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """群机器人测试消息（SPEC-WH）：真发一条 → {ok, detail}，失败也 200 不 500。

    body 可带 {url, type} 支持先测后存；缺省用已存配置（未配置返回 ok:false）。
    """
    from ..notify.channels import _deliver_webhook_test

    payload = body or {}
    url = payload.get("url") or _get(session, "notify_webhook_url")
    wh_type = payload.get("type") or _get(session, "notify_webhook_type") or "wecom"
    if not url:
        return {"ok": False, "detail": "skipped:not-configured"}
    return _deliver_webhook_test(session, url, wh_type)


@router.get("/agent-runs")
def list_agent_runs(
    agent_name: str | None = None,
    task_id: str | None = None,
    page: int = 1,
    page_size: int = 15,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    stmt = select(AgentRun)
    if agent_name:
        stmt = stmt.where(AgentRun.agent_name == agent_name)
    if task_id:
        stmt = stmt.where(AgentRun.task_id == task_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(AgentRun.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "agent_name": r.agent_name,
                "backend": r.backend,
                "input_json": r.input_json,
                "output_json": r.output_json,
                "decision_summary": r.decision_summary,
                "duration_ms": r.duration_ms,
                "task_id": r.task_id,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/notifications")
def list_notifications(
    unread: int = 0,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """站内通知列表 + 未读数（SPEC-C F1）。

    digested=2 的原行已折入 digest 汇总，列表不再展示（SPEC-I I2 防重复）。
    """
    from ..models import Notification

    base = (
        session.query(Notification)
        .filter(Notification.user_id == user.id)
        .filter(Notification.digested != 2)
    )
    unread_count = base.filter(Notification.read == 0).count()
    stmt = base
    if unread:
        stmt = stmt.filter(Notification.read == 0)
    rows = (
        stmt.order_by(Notification.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # 信号族通知回填 ent_id（SPEC 流程打磨 2026-11-10：通知点击直达企业详情——
    # ref_id 存的是 signal.id，列表一次 in 查询换出 enterprise_id）
    _SIGNAL_REF_TYPES = ("signal", "sub_signal", "owner_signal", "competitor_win")
    sig_ids = [n.ref_id for n in rows if n.ref_type in _SIGNAL_REF_TYPES]
    ent_map: dict[int, int] = {}
    if sig_ids:
        srows = (
            session.query(Signal.id, Signal.enterprise_id)
            .filter(Signal.id.in_(sig_ids))
            .all()
        )
        ent_map = {sid: eid for sid, eid in srows}
    return {
        "unread_count": unread_count,
        "items": [
            {
                "id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
                "ref_type": n.ref_type, "ref_id": n.ref_id, "read": n.read,
                "created_at": n.created_at,
                "ent_id": ent_map.get(n.ref_id) if n.ref_type in _SIGNAL_REF_TYPES else None,
            }
            for n in rows
        ],
    }


@router.post("/notifications/{nid}/read")
def read_notification(
    nid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from ..models import Notification

    n = session.get(Notification, nid)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "通知不存在")
    n.read = 1
    session.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all_notifications(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from ..models import Notification

    session.query(Notification).filter(
        Notification.user_id == user.id, Notification.read == 0
    ).update({"read": 1}, synchronize_session=False)
    session.commit()
    return {"ok": True}


@router.get("/recommendations")
def get_recommendations(
    limit: int = 50,
    product: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """今日推荐（SPEC-AL）：可触达性×采购信号的可解释排序，全员可用。

    product（SPEC-CH CH3）：按产品线过滤（应用面行业集）。
    """
    from ..recommend import score_candidates

    return {
        "items": score_candidates(
            session, limit=max(1, min(limit, 100)),
            product=(product or None) or None,
        )
    }


# ---------------- 私有成交案例库（SPEC-AZ AZ2） ----------------
class CaseStudyIn(BaseModel):
    enterprise_id: int | None = None
    industry_key: str | None = None
    title: str
    content: str = ""
    year: str | None = None


@router.get("/cases")
def list_case_studies(
    industry_key: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """私有成交案例（昊志真实交付——比公开招标证据更强的提案资产）。"""
    from ..models import CaseStudy

    stmt = select(CaseStudy).order_by(CaseStudy.id.desc())
    if industry_key:
        stmt = stmt.where(CaseStudy.industry_key == industry_key)
    rows = session.scalars(stmt.limit(50)).all()
    return {
        "items": [
            {"id": c.id, "enterprise_id": c.enterprise_id, "industry_key": c.industry_key,
             "title": c.title, "content": c.content, "year": c.year}
            for c in rows
        ]
    }


@router.post("/cases", status_code=201)
def create_case_study(
    body: CaseStudyIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """私有成交案例（SPEC-BB BB1 权限修正）：销售成交后自己录案例。"""
    from ..models import CaseStudy

    if len(body.title) < 4:
        raise HTTPException(422, "案例标题过短")
    c = CaseStudy(
        enterprise_id=body.enterprise_id, industry_key=body.industry_key,
        title=body.title, content=body.content, year=body.year,
    )
    session.add(c)
    session.commit()
    return {"id": c.id}


@router.delete("/cases/{cid}")
def delete_case_study(
    cid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    from ..models import CaseStudy

    c = session.get(CaseStudy, cid)
    if c is None:
        raise HTTPException(404, "案例不存在")
    session.delete(c)
    session.commit()
    return {"ok": True}


@router.post("/recommendations/{ent_id}/convert")
def convert_recommendation(
    ent_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """推荐漏斗收口（SPEC-AO AO2）：一键转线索（幂等——复用已有 following/intent）。"""
    from datetime import datetime, timedelta, timezone

    from ..models import Contact, Lead, Todo

    ent = session.get(Enterprise, ent_id)
    if ent is None or ent.deleted_at:
        raise HTTPException(404, "企业不存在")
    lead = session.query(Lead).filter(
        Lead.enterprise_id == ent_id,
        or_(Lead.status == "following", Lead.status == "intent"),
    ).first()
    created = False
    if lead is None:
        lead = Lead(enterprise_id=ent_id, status="following", owner_id=user.id)
        session.add(lead)
        session.flush()
        created = True
    # 首联待办（48h 期限；open 唯一索引防重建）
    due = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime("%Y-%m-%d")
    exists = session.query(Todo).filter(
        Todo.enterprise_id == ent_id,
        Todo.title == "推荐转化：48 小时内首联",
        Todo.done == 0,
    ).first()
    if exists is None:
        session.add(Todo(
            title="推荐转化：48 小时内首联",
            enterprise_id=ent_id, lead_id=lead.id,
            due_date=due, owner_id=user.id, source="automation",
        ))
    # interested 触达结果联动：有联系人未标则补
    session.query(Contact).filter(
        Contact.enterprise_id == ent_id, Contact.call_result == "pending"
    ).update({"call_result": "interested"}, synchronize_session=False)
    session.commit()
    return {"ok": True, "lead_id": lead.id, "created": created}


@router.post("/recommendations/{ent_id}/mark-call")
def mark_recommendation_call(
    ent_id: int,
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """触达快标（SPEC-AW AW2）：按企业标记主联系人通话结果——日活循环收口。

    result ∈ connected|interested|no_answer|voicemail|invalid（快标五态）。
    标记对象=该企业首个带可拨电话的联系人。
    """
    from datetime import datetime, timezone

    from sqlalchemy import or_

    from ..models import Contact

    result = str(body.get("result") or "")
    if result not in ("connected", "interested", "no_answer", "voicemail", "invalid"):
        raise HTTPException(422, "非法结果（快标五态）")
    ent = session.get(Enterprise, ent_id)
    if ent is None or ent.deleted_at:
        raise HTTPException(404, "企业不存在")
    contact = session.query(Contact).filter(
        Contact.enterprise_id == ent_id,
        or_(
            Contact.phones_json != "[]",
            Contact.title.op("GLOB")("*[0-9]*"),
        ),
    ).order_by(Contact.id.asc()).first()
    if contact is None:
        raise HTTPException(404, "该企业无可标记电话联系人")
    contact.call_result = result
    contact.called_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    session.commit()
    return {"ok": True, "contact_id": contact.id, "call_result": result}


@router.get("/analytics/call-funnel")
def call_funnel(
    days: int = 30,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """触达漏斗（SPEC-AN AN3，Salesloft 连通率语义）：dials→connected→interested。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func

    from ..models import Contact

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    ).strftime("%Y-%m-%dT%H:%M:%S")
    by = dict(
        session.execute(
            select(Contact.call_result, func.count())
            .where(Contact.called_at.is_not(None), Contact.called_at >= cutoff)
            .group_by(Contact.call_result)
        ).all()
    )
    dials = sum(by.values())
    connected = int(by.get("connected", 0)) + int(by.get("interested", 0)) + int(
        by.get("not_interested", 0)
    )
    # SPEC-BA BA1：未触达池计数（有可拨电话且全部联系人未拨）
    from sqlalchemy import or_

    from ..models import Enterprise

    phone_ents = select(Contact.enterprise_id).where(Contact.phones_json != "[]")
    touched_ents = select(Contact.enterprise_id).where(Contact.call_result != "pending")
    untouched_pool = (
        session.query(func.count())
        .select_from(Enterprise)
        .filter(
            Enterprise.deleted_at.is_(None),
            Enterprise.id.in_(phone_ents),
            ~Enterprise.id.in_(touched_ents),
        )
        .scalar()
        or 0
    )
    return {
        "days": days,
        "dials": dials,
        "connected": connected,
        "interested": int(by.get("interested", 0)),
        "no_answer": int(by.get("no_answer", 0)),
        "voicemail": int(by.get("voicemail", 0)),
        "invalid": int(by.get("invalid", 0)),
        "connect_rate": round(connected / dials, 3) if dials else 0.0,
        "interest_rate": round(int(by.get("interested", 0)) / dials, 3) if dials else 0.0,
        "untouched_pool": untouched_pool,
    }


@router.get("/cases/industry/{key}")
def industry_cases(
    key: str,
    limit: int = 8,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """同行业公开采购案例库（SPEC-AV AV1）。

    自有信号池按行业聚合（中标/采购/招标三类，法定公开可引用）——
    工程师的"不背锅"证据：同行产线在用同类设备。
    """
    from datetime import datetime, timedelta, timezone

    from ..models import Enterprise, Signal

    limit_n = max(1, min(limit, 20))
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=1095)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    rows = session.execute(
        select(Signal.title, Signal.evidence_claim, Signal.pub_date, Signal.url,
               Enterprise.name)
        .join(Enterprise, Signal.enterprise_id == Enterprise.id)
        .where(
            Enterprise.industry_key == key,
            Enterprise.deleted_at.is_(None),
            Signal.type.in_(["tender_win", "tender_buy", "tender"]),
            Signal.created_at >= cutoff,
        )
        .order_by(Signal.id.desc())
        .limit(limit_n)
    ).all()
    return {
        "items": [
            {"title": t, "evidence": ev or "", "pub_date": d or "",
             "url": u or "", "ent_name": n}
            for t, ev, d, u, n in rows
        ],
        "total": len(rows),
    }
