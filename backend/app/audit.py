"""审计（SPEC-J J1，espocrm 审计思想）：关键实体写操作统一落 audit_log。

- update/delete：SQLAlchemy before_flush 钩子统一抓字段 diff（历史可用、id 已知，
  官方支持在该事件内 session.add 追加对象）
- insert：API 层显式调用 log_insert（建档只发生在用户驱动的端点；系统/采集批量
  建档本来就要排除为噪音）。必须在对象 flush 出 id 之后调用
- actor 由 FastAPI 依赖 current_user 写入 current_actor（contextvar）；后台任务/
  采集线程取默认 0=系统
- 降噪：机器计算字段（intent/icp 分数）不入 diff；无业务字段变化的 update 不落
"""
from __future__ import annotations

import json
from contextvars import ContextVar

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from .models import AuditLog, Contact, Enterprise, Lead, utcnow

#: 当前操作者（用户 id）；0=系统（采集/任务/调度）
current_actor: ContextVar[int] = ContextVar("vaclead_actor", default=0)

#: 审计对象：表名 → ORM 类
AUDITED: dict[str, type] = {
    "enterprise": Enterprise,
    "lead": Lead,
    "contact": Contact,
}

#: 机器计算/系统维护字段不进 diff（重算 intent 每天全表刷，审计无价值纯噪音）
_EXCLUDE_FIELDS = {
    "id", "created_at", "updated_at", "intent_score", "icp_score", "icp_reasons",
}


def _serialize(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return json.dumps(value)
    return str(value)


def _changes(obj) -> dict[str, list[str]]:
    """字段级 diff：{field: [before, after]}。"""
    state = sa_inspect(obj)
    changes: dict[str, list[str]] = {}
    for attr in state.attrs:
        key = attr.key
        if key in _EXCLUDE_FIELDS:
            continue
        hist = attr.history
        if not hist.has_changes():
            continue
        old = hist.deleted[0] if hist.deleted else None
        new = hist.added[0] if hist.added else None
        changes[key] = [_serialize(old), _serialize(new)]
    return changes


def _before_flush(session, _flush_context, _instances) -> None:
    for obj in (*session.dirty, *session.deleted):
        entity = getattr(obj, "__tablename__", None)
        if entity not in AUDITED:
            continue
        changes = _changes(obj)
        if not changes:
            continue
        session.add(AuditLog(
            user_id=current_actor.get(),
            action="delete" if obj in session.deleted else "update",
            entity=entity,
            entity_id=obj.id,
            changes_json=json.dumps(changes, ensure_ascii=False),
            created_at=utcnow(),
        ))


def log_insert(session: Session, obj) -> None:
    """显式记录建档（API 层在对象拿到 id 后调用；与业务同一事务提交）。

    系统来源（采集/任务，actor=0）静默跳过——批量建档是噪音。
    """
    if current_actor.get() == 0:
        return
    entity = getattr(obj, "__tablename__", None)
    if entity not in AUDITED or obj.id is None:
        return
    session.add(AuditLog(
        user_id=current_actor.get(),
        action="insert",
        entity=entity,
        entity_id=obj.id,
        changes_json="{}",
        created_at=utcnow(),
    ))


_installed = False


def install() -> None:
    """挂全局 session 事件（幂等，启动时调用一次）。"""
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", _before_flush)
    _installed = True
