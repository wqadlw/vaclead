"""留资表单（SPEC-J J5，espocrm Lead Capture 思想）：公开渠道捕获进线索。

- 公开端点无需登录：token 鉴权 + honeypot 字段 + 每 IP 限流防垃圾
- 入库走归一引擎（resolve_enterprise）：企业匹配/新建，来源 source=webform
- 原始载荷留档 lead_capture_log（防垃圾审计 + 可追溯）
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import current_actor
from ..auth import admin_only, current_user
from ..db import get_session
from ..models import Enterprise, Lead, LeadCaptureLog, LeadForm, User, utcnow

router = APIRouter(tags=["leadforms"])

#: 每 IP 提交限流：5 次 / 10 分钟（内存实现，单进程语义，同登录限流模式）
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_MAX, _RATE_WINDOW = 5, 600.0


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _RATE[ip] if now - t < _RATE_WINDOW]
    if len(hits) >= _RATE_MAX:
        _RATE[ip] = hits
        return True
    hits.append(now)
    _RATE[ip] = hits
    return False


# ---------------- 公开捕获（无鉴权） ----------------

class CaptureIn(BaseModel):
    company: str = Field(min_length=2, max_length=80)
    contact_name: str | None = Field(default=None, max_length=40)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=80)
    need: str | None = Field(default=None, max_length=500)
    website: str = ""  # honeypot：人类不可见，机器人填了就丢


@router.post("/public/lead-capture/{token}", status_code=201)
def capture(
    token: str,
    body: CaptureIn,
    request: Request,
    session: Session = Depends(get_session),
):
    form = (
        session.query(LeadForm)
        .filter(LeadForm.token == token, LeadForm.active == 1)
        .first()
    )
    if form is None:
        raise HTTPException(404, "表单不存在或已停用")
    ip = (request.client.host if request.client else "") or "unknown"
    if body.website:  # honeypot 命中：静默丢弃（机器人以为成功了）
        session.add(LeadCaptureLog(
            form_id=form.id, company="[honeypot]", ip=ip,
            need="honeypot", created_at=utcnow(),
        ))
        session.commit()
        return {"ok": True}
    if _rate_limited(ip):
        raise HTTPException(429, "提交过于频繁，请稍后再试")
    if not body.phone and not body.email:
        raise HTTPException(422, "电话与邮箱至少留一个")

    from ..normalize import resolve_enterprise

    # actor=0：系统捕获路径，不进审计（批量噪音）
    current_actor.set(0)
    ent, _is_new = resolve_enterprise(session, body.company.strip(), source="webform")
    dup = session.query(Lead).filter(Lead.enterprise_id == ent.id).first()
    lead_id: int | None
    if dup is not None:  # 撞单：已有线索只记日志，不动销售资产
        lead_id = dup.id
    else:
        lead = Lead(enterprise_id=ent.id, status="new", tenant_id=ent.tenant_id)
        session.add(lead)
        session.flush()
        lead_id = lead.id
    session.add(LeadCaptureLog(
        form_id=form.id, company=body.company.strip(),
        contact_name=body.contact_name, phone=body.phone, email=body.email,
        need=body.need, ip=ip, enterprise_id=ent.id, lead_id=lead_id,
    ))
    form.submissions += 1
    session.commit()
    return {"ok": True, "enterprise_id": ent.id, "lead_id": lead_id}


# ---------------- 管理端 ----------------

class FormIn(BaseModel):
    name: str


@router.get("/lead-forms")
def list_forms(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    rows = session.query(LeadForm).order_by(LeadForm.id.desc()).limit(100).all()
    return {
        "items": [
            {"id": f.id, "name": f.name, "token": f.token, "active": f.active,
             "submissions": f.submissions, "created_at": f.created_at}
            for f in rows
        ]
    }


@router.post("/lead-forms", status_code=201)
def create_form(
    body: FormIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if not body.name.strip():
        raise HTTPException(422, "表单名不能为空")
    f = LeadForm(name=body.name.strip(), token=secrets.token_urlsafe(12),
                 created_by=user.id)
    session.add(f)
    session.commit()
    return {"id": f.id, "token": f.token}


@router.patch("/lead-forms/{fid}")
def toggle_form(
    fid: int,
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    f = session.get(LeadForm, fid)
    if f is None:
        raise HTTPException(404, "表单不存在")
    f.active = 1 if body.get("active") else 0
    session.commit()
    return {"ok": True, "active": f.active}


@router.get("/lead-forms/{fid}/submissions")
def list_submissions(
    fid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    rows = (
        session.query(LeadCaptureLog)
        .filter(LeadCaptureLog.form_id == fid, LeadCaptureLog.company != "[honeypot]")
        .order_by(LeadCaptureLog.id.desc())
        .limit(100)
        .all()
    )
    return {
        "items": [
            {"id": r.id, "company": r.company, "contact_name": r.contact_name,
             "phone": r.phone, "email": r.email, "need": r.need,
             "enterprise_id": r.enterprise_id, "lead_id": r.lead_id,
             "created_at": r.created_at}
            for r in rows
        ]
    }
