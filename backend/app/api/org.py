"""组织端点（SPEC-N）：用户管理 / 团队 / 线索分配规则 / dry-run / 业绩排行榜。"""
from __future__ import annotations

import bcrypt
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import (
    AssignmentRule,
    Enterprise,
    Followup,
    Lead,
    Team,
    User,
    utcnow,
)

router = APIRouter(tags=["org"])


# ---------------- 用户管理（N1） ----------------
class UserIn(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    display_name: str | None = None
    email: str | None = None
    team_id: int | None = None
    active: bool | None = None


def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "email": u.email, "role": u.role, "team_id": u.team_id,
        "active": bool(u.active),
    }


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _last_active_admin(session: Session) -> int:
    return (
        session.query(func.count())
        .select_from(User)
        .filter(User.role == "admin", User.active == 1)
        .scalar()
        or 0
    )


@router.get("/users")
def list_users(session: Session = Depends(get_session), user: User = Depends(admin_only)):
    rows = session.query(User).order_by(User.id.asc()).all()
    return {"items": [_user_dict(u) for u in rows]}


@router.post("/users", status_code=201)
def create_user(body: UserIn, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    if not body.username or not body.username.strip():
        raise HTTPException(422, "用户名不能为空")
    if not body.password or len(body.password) < 6:
        raise HTTPException(422, "密码至少 6 位")
    username = body.username.strip()
    if session.query(User).filter(User.username == username).first() is not None:
        raise HTTPException(409, "用户名已存在")
    if body.role is not None and body.role not in ("admin", "sales"):
        raise HTTPException(422, "角色只能是 admin 或 sales")
    if body.team_id is not None and session.get(Team, body.team_id) is None:
        raise HTTPException(404, "团队不存在")
    u = User(
        username=username, password_hash=_hash_password(body.password),
        role=body.role or "sales", display_name=(body.display_name or "").strip(),
        email=body.email, team_id=body.team_id, active=1,
    )
    session.add(u)
    session.commit()
    return {"id": u.id}


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    target = session.get(User, uid)
    if target is None:
        raise HTTPException(404, "用户不存在")
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(422, "密码至少 6 位")
        target.password_hash = _hash_password(body.password)
    if body.role is not None:
        if body.role not in ("admin", "sales"):
            raise HTTPException(422, "角色只能是 admin 或 sales")
        if target.role == "admin" and body.role != "admin" and target.active \
                and _last_active_admin(session) <= 1:
            raise HTTPException(422, "不能降级最后一个管理员")
        target.role = body.role
    if body.active is not None:
        if not body.active:
            if target.id == user.id:
                raise HTTPException(422, "不能禁用自己")
            if target.role == "admin" and _last_active_admin(session) <= 1:
                raise HTTPException(422, "不能禁用最后一个管理员")
        target.active = 1 if body.active else 0
    if body.display_name is not None:
        target.display_name = body.display_name.strip()
    if body.email is not None:
        target.email = body.email.strip() or None
    if body.team_id is not None and session.get(Team, body.team_id) is None:
        raise HTTPException(404, "团队不存在")
    if "team_id" in body.model_fields_set:
        target.team_id = body.team_id
    session.commit()
    return {"ok": True}


# ---------------- 团队（N2） ----------------
class TeamIn(BaseModel):
    name: str
    leader_id: int | None = None


def _team_dict(session: Session, t: Team) -> dict:
    members = session.query(User).filter(User.team_id == t.id).all()
    leader = session.get(User, t.leader_id) if t.leader_id else None
    return {
        "id": t.id, "name": t.name, "leader_id": t.leader_id,
        "leader_name": leader.display_name or leader.username if leader else None,
        "member_count": len(members),
        "members": [{"id": m.id, "username": m.username,
                     "display_name": m.display_name} for m in members],
        "created_at": t.created_at,
    }


@router.get("/teams")
def list_teams(session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.query(Team).order_by(Team.id.asc()).all()
    return {"items": [_team_dict(session, t) for t in rows]}


@router.post("/teams", status_code=201)
def create_team(body: TeamIn, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    if not body.name.strip():
        raise HTTPException(422, "团队名不能为空")
    if session.query(Team).filter(Team.name == body.name.strip()).first() is not None:
        raise HTTPException(409, "团队名已存在")
    if body.leader_id is not None and session.get(User, body.leader_id) is None:
        raise HTTPException(404, "负责人不存在")
    t = Team(name=body.name.strip(), leader_id=body.leader_id)
    session.add(t)
    session.commit()
    return {"id": t.id}


@router.put("/teams/{tid}")
def update_team(tid: int, body: TeamIn, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    t = session.get(Team, tid)
    if t is None:
        raise HTTPException(404, "团队不存在")
    if body.name.strip():
        dup = session.query(Team).filter(Team.name == body.name.strip(), Team.id != tid).first()
        if dup is not None:
            raise HTTPException(409, "团队名已存在")
        t.name = body.name.strip()
    if body.leader_id is not None and session.get(User, body.leader_id) is None:
        raise HTTPException(404, "负责人不存在")
    t.leader_id = body.leader_id
    session.commit()
    return {"ok": True}


@router.delete("/teams/{tid}")
def delete_team(tid: int, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    t = session.get(Team, tid)
    if t is None:
        raise HTTPException(404, "团队不存在")
    members = session.query(User).filter(User.team_id == tid).count()
    if members:
        raise HTTPException(409, f"仍有 {members} 名成员，先移出团队")
    used = session.query(AssignmentRule).filter(AssignmentRule.target_team_id == tid).count()
    if used:
        raise HTTPException(409, "被分配规则引用，先解除")
    session.delete(t)
    session.commit()
    return {"ok": True}


# ---------------- 线索分配规则（N3） ----------------
class RuleIn(BaseModel):
    name: str
    conditions: dict = Field(default_factory=dict)
    target_team_id: int | None = None
    target_user_ids: list[int] = Field(default_factory=list)
    strategy: str = "fixed"  # fixed|round_robin
    enabled: bool = True
    priority: int = 100


def _rule_dict(r: AssignmentRule, session: Session) -> dict:
    team = session.get(Team, r.target_team_id) if r.target_team_id else None
    return {
        "id": r.id, "name": r.name,
        "conditions": json.loads(r.conditions or "{}"),
        "target_team_id": r.target_team_id, "team_name": team.name if team else None,
        "target_user_ids": json.loads(r.target_user_ids or "[]"),
        "strategy": r.strategy, "enabled": bool(r.enabled), "priority": r.priority,
        "run_count": r.run_count, "last_run_at": r.last_run_at,
    }


def _validate_rule(session: Session, body: RuleIn) -> None:
    if not body.name.strip():
        raise HTTPException(422, "规则名不能为空")
    if body.strategy not in ("fixed", "round_robin"):
        raise HTTPException(422, "策略只能是 fixed 或 round_robin")
    if not body.target_user_ids:
        raise HTTPException(422, "至少选择一名目标用户")
    for uid in body.target_user_ids:
        if session.get(User, uid) is None:
            raise HTTPException(404, f"目标用户 {uid} 不存在")
    if body.target_team_id is not None and session.get(Team, body.target_team_id) is None:
        raise HTTPException(404, "目标团队不存在")


@router.get("/assignment-rules")
def list_rules(session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.query(AssignmentRule).order_by(
        AssignmentRule.priority.asc(), AssignmentRule.id.asc()).all()
    return {"items": [_rule_dict(r, session) for r in rows]}


@router.post("/assignment-rules", status_code=201)
def create_rule(body: RuleIn, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    _validate_rule(session, body)
    r = AssignmentRule(
        name=body.name.strip(),
        conditions=json.dumps(body.conditions, ensure_ascii=False),
        target_team_id=body.target_team_id,
        target_user_ids=json.dumps(body.target_user_ids),
        strategy=body.strategy, enabled=1 if body.enabled else 0,
        priority=body.priority,
    )
    session.add(r)
    session.commit()
    return {"id": r.id}


@router.put("/assignment-rules/{rid}")
def update_rule(rid: int, body: RuleIn, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    r = session.get(AssignmentRule, rid)
    if r is None:
        raise HTTPException(404, "规则不存在")
    _validate_rule(session, body)
    r.name = body.name.strip()
    r.conditions = json.dumps(body.conditions, ensure_ascii=False)
    r.target_team_id = body.target_team_id
    r.target_user_ids = json.dumps(body.target_user_ids)
    r.strategy = body.strategy
    r.enabled = 1 if body.enabled else 0
    r.priority = body.priority
    session.commit()
    return {"ok": True}


@router.delete("/assignment-rules/{rid}")
def delete_rule(rid: int, session: Session = Depends(get_session),
                user: User = Depends(admin_only)):
    r = session.get(AssignmentRule, rid)
    if r is None:
        raise HTTPException(404, "规则不存在")
    session.delete(r)
    session.commit()
    return {"ok": True}


@router.post("/assignment-rules/dry-run")
def dry_run(body: dict, session: Session = Depends(get_session),
            user: User = Depends(current_user)):
    """模拟企业字段 → 命中规则与目标用户（零写操作，对齐 automation dry-run 惯例）。"""
    from ..assign import match_rule, pick_target

    ent = Enterprise(
        id=0, name="[模拟]", industry_key=body.get("industry_key"),
        province=body.get("province"), source=body.get("source"),
    )
    rules = (
        session.query(AssignmentRule)
        .filter(AssignmentRule.enabled == 1)
        .order_by(AssignmentRule.priority.asc(), AssignmentRule.id.asc())
        .all()
    )
    for rule in rules:
        if not match_rule(rule, ent):
            continue
        target_id = pick_target(rule, advance=False)
        if target_id is None:
            continue
        target = session.get(User, target_id)
        return {
            "matched": True, "rule_id": rule.id, "rule_name": rule.name,
            "target_id": target_id,
            "target_name": (target.display_name or target.username) if target else None,
        }
    return {"matched": False}


# ---------------- 业绩排行榜（N4） ----------------
@router.get("/analytics-leaderboard")
def leaderboard(month: str | None = None, session: Session = Depends(get_session),
                user: User = Depends(current_user)):
    """月度业绩榜：成交金额(won 商机)/新增线索/跟进次数，按成交额降序（探迹激励同构）。"""
    m = month or datetime.now(timezone.utc).strftime("%Y-%m")
    prefix = f"{m}-"
    users = session.query(User).filter(User.active == 1).all()
    won = (
        session.query(Lead.owner_id, func.sum(Lead.amount_wan))
        .filter(Lead.status == "won", Lead.updated_at.like(f"{prefix}%"))
        .group_by(Lead.owner_id)
        .all()
    )
    won_map = {int(uid): float(amt or 0) for uid, amt in won if uid is not None}
    new_leads = (
        session.query(Lead.owner_id, func.count())
        .filter(Lead.created_at.like(f"{prefix}%"), Lead.owner_id.is_not(None))
        .group_by(Lead.owner_id)
        .all()
    )
    leads_map = {int(uid): int(c) for uid, c in new_leads if uid is not None}
    followups = (
        session.query(Lead.owner_id, func.count())
        .join(Followup, Followup.lead_id == Lead.id)
        .filter(Followup.created_at.like(f"{prefix}%"), Lead.owner_id.is_not(None))
        .group_by(Lead.owner_id)
        .all()
    )
    fu_map = {int(uid): int(c) for uid, c in followups if uid is not None}
    items = [
        {
            "user_id": u.id, "name": u.display_name or u.username,
            "won_amount": round(won_map.get(u.id, 0.0), 2),
            "new_leads": leads_map.get(u.id, 0),
            "followups": fu_map.get(u.id, 0),
        }
        for u in users
        if won_map.get(u.id) or leads_map.get(u.id) or fu_map.get(u.id)
    ]
    items.sort(key=lambda x: (-x["won_amount"], -x["new_leads"], -x["followups"]))
    for rank, it in enumerate(items, 1):
        it["rank"] = rank
    return {"month": m, "items": items}


# ---------------- 团队动态（SPEC-S 过程监管，audit_log 复用） ----------------
@router.get("/org/activity")
def team_activity(days: int = 7, limit: int = 50,
                  session: Session = Depends(get_session),
                  user: User = Depends(admin_only)):
    """管理员过程监管：成员最近的人工操作（audit_log）+ 周度跟进/更新计数。"""
    from datetime import timedelta

    from ..models import AuditLog, Followup

    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=min(days, 90))).isoformat(timespec="seconds")
    rows = (
        session.query(AuditLog, User)
        .outerjoin(User, AuditLog.user_id == User.id)
        .filter(AuditLog.user_id != 0, AuditLog.created_at >= cutoff)
        .order_by(AuditLog.id.desc())
        .limit(min(limit, 200))
        .all()
    )
    items = [
        {
            "id": a.id, "actor": (u.display_name or u.username) if u else f"user#{a.user_id}",
            "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
            "changes": a.changes_json, "created_at": a.created_at,
        }
        for a, u in rows
    ]
    fu = (
        session.query(Lead.owner_id, func.count())
        .join(Followup, Followup.lead_id == Lead.id)
        .filter(Followup.created_at >= cutoff, Lead.owner_id.is_not(None))
        .group_by(Lead.owner_id)
        .all()
    )
    fu_map = {int(uid): int(c) for uid, c in fu if uid is not None}
    upd = (
        session.query(AuditLog.user_id, func.count())
        .filter(AuditLog.user_id != 0, AuditLog.action == "update",
                AuditLog.created_at >= cutoff)
        .group_by(AuditLog.user_id)
        .all()
    )
    upd_map = {int(uid): int(c) for uid, c in upd if uid is not None}
    weekly = []
    for uid in sorted(set(fu_map) | set(upd_map),
                      key=lambda k: -(fu_map.get(k, 0) + upd_map.get(k, 0))):
        u = session.get(User, uid)
        if u is None:
            continue
        weekly.append({"user_id": uid, "name": u.display_name or u.username,
                       "followups": fu_map.get(uid, 0), "updates": upd_map.get(uid, 0)})
    return {"items": items, "weekly": weekly, "days": days}
