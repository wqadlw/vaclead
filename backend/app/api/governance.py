"""治理端点（SPEC-J J1/J7）：审计日志查询、API 密钥管理、关键词/地区订阅规则。"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import SubscriptionRule, User, UserApiKey, utcnow

router = APIRouter(tags=["governance"])


# ---------------- J1 审计日志（admin only） ----------------

@router.get("/audit")
def list_audit(
    entity: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """审计日志倒序分页（espocrm 审计列表语义）。"""
    from ..models import AuditLog

    q = session.query(AuditLog)
    if entity:
        q = q.filter(AuditLog.entity == entity)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    rows = (
        q.order_by(AuditLog.id.desc())
        .offset(max(0, (page - 1)) * page_size)
        .limit(min(page_size, 100))
        .all()
    )
    usernames = {
        u.id: u.display_name or u.username
        for u in session.query(User).all()
    }
    return {
        "items": [
            {
                "id": r.id,
                "user": usernames.get(r.user_id, "系统" if r.user_id == 0 else str(r.user_id)),
                "action": r.action,
                "entity": r.entity,
                "entity_id": r.entity_id,
                "changes": _safe_json(r.changes_json),
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _safe_json(raw: str) -> dict:
    import json as _json

    try:
        return _json.loads(raw or "{}")
    except ValueError:
        return {}


# ---------------- J1 API 密钥 ----------------

class ApiKeyIn(BaseModel):
    name: str
    scopes: str = "read"  # read|rw


@router.post("/api-keys")
def create_api_key(
    body: ApiKeyIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """创建 API Key：明文仅本次响应返回一次。"""
    if body.scopes not in ("read", "rw"):
        raise HTTPException(422, "scopes 取值 read|rw")
    raw = f"vla_{secrets.token_urlsafe(24)}"
    row = UserApiKey(
        user_id=user.id,
        name=body.name.strip() or "未命名",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:12],
        scopes=body.scopes,
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "name": row.name, "scopes": row.scopes, "key": raw}


@router.get("/api-keys")
def list_api_keys(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    rows = (
        session.query(UserApiKey)
        .filter(UserApiKey.user_id == user.id, UserApiKey.revoked == 0)
        .order_by(UserApiKey.id.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": r.id, "name": r.name, "scopes": r.scopes,
                "prefix": r.prefix, "last_used_at": r.last_used_at,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.delete("/api-keys/{kid}")
def revoke_api_key(
    kid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    row = session.get(UserApiKey, kid)
    if row is None or (row.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "密钥不存在")
    row.revoked = 1
    session.commit()
    return {"ok": True}


# ---------------- J7 关键词/地区订阅规则 ----------------

class SubRuleIn(BaseModel):
    kind: str  # keyword|region
    value: str


@router.get("/subscription-rules")
def list_sub_rules(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    rows = (
        session.query(SubscriptionRule)
        .filter(SubscriptionRule.user_id == user.id)
        .order_by(SubscriptionRule.id.desc())
        .all()
    )
    return {
        "items": [
            {"id": r.id, "kind": r.kind, "value": r.value, "created_at": r.created_at}
            for r in rows
        ]
    }


@router.post("/subscription-rules")
def create_sub_rule(
    body: SubRuleIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if body.kind not in ("keyword", "region"):
        raise HTTPException(422, "kind 取值 keyword|region")
    value = body.value.strip()
    if not 2 <= len(value) <= 30:
        raise HTTPException(422, "value 长度 2-30")
    dup = (
        session.query(SubscriptionRule)
        .filter(
            SubscriptionRule.user_id == user.id,
            SubscriptionRule.kind == body.kind,
            SubscriptionRule.value == value,
        )
        .first()
    )
    if dup is not None:
        raise HTTPException(409, "该订阅规则已存在")
    row = SubscriptionRule(user_id=user.id, kind=body.kind, value=value)
    session.add(row)
    session.commit()
    return {"id": row.id, "kind": row.kind, "value": row.value}


@router.delete("/subscription-rules/{rid}")
def delete_sub_rule(
    rid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    row = session.get(SubscriptionRule, rid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "规则不存在")
    session.delete(row)
    session.commit()
    return {"ok": True}


# ---------------- J6 月度目标 ----------------

class TargetIn(BaseModel):
    month: str  # YYYY-MM
    amount_wan: float


@router.get("/my-target")
def get_my_target(
    month: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from ..models import UserTarget

    month = month or utcnow()[:7]
    row = (
        session.query(UserTarget)
        .filter(UserTarget.user_id == user.id, UserTarget.month == month)
        .first()
    )
    return {"month": month, "amount_wan": float(row.amount_wan) if row else None}


@router.put("/my-target")
def put_my_target(
    body: TargetIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    import re as _re

    from ..models import UserTarget

    if not _re.fullmatch(r"\d{4}-\d{2}", body.month):
        raise HTTPException(422, "month 格式 YYYY-MM")
    if body.amount_wan < 0:
        raise HTTPException(422, "目标金额不能为负")
    row = (
        session.query(UserTarget)
        .filter(UserTarget.user_id == user.id, UserTarget.month == body.month)
        .first()
    )
    if row is None:
        row = UserTarget(user_id=user.id, month=body.month, amount_wan=body.amount_wan)
        session.add(row)
    else:
        row.amount_wan = body.amount_wan
    session.commit()
    return {"month": row.month, "amount_wan": float(row.amount_wan)}
