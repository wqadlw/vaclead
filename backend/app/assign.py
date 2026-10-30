"""线索自动分配引擎（SPEC-N N3，EspoCRM Assignment Rules 思想）。

挂 ORM before_flush 钩子：对 session.new 中 owner_id 为空的 Lead 做规则匹配——
一处接线覆盖全部线索创建入口（导入/留资/自动化/手动），不侵入任何业务代码。
规则 first-match（priority asc, id asc）；round_robin 用规则行上的持久 cursor 列
（EspoCRM 论坛已知"并发涌入轮转失效"同款问题的防御：游标不落内存）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from .models import AssignmentRule, Enterprise, Lead, utcnow

logger = logging.getLogger("vaclead.assign")


def load_conditions(rule: AssignmentRule) -> dict:
    try:
        return json.loads(rule.conditions or "{}")
    except ValueError:
        return {}


def match_rule(rule: AssignmentRule, ent: Enterprise | None) -> bool:
    """条件全命中才返回 True；conditions 全空=命中所有；企业缺失=不命中。"""
    if ent is None:
        return False
    cond = load_conditions(rule)
    if cond.get("industry_key") and (ent.industry_key or "") != cond["industry_key"]:
        return False
    if cond.get("province") and (ent.province or "") != cond["province"]:
        return False
    if cond.get("source") and (ent.source or "") != cond["source"]:
        return False
    return True


def pick_target(rule: AssignmentRule, *, advance: bool = True) -> int | None:
    """按策略选目标用户；目标列表空返回 None（线索保持无主）。

    advance=False 供 dry-run 预览（不推进游标、零副作用）。
    """
    try:
        targets = [int(u) for u in json.loads(rule.target_user_ids or "[]")]
    except ValueError:
        targets = []
    if not targets:
        return None
    if rule.strategy == "round_robin" and len(targets) > 1:
        chosen = targets[rule.cursor % len(targets)]
        if advance:
            rule.cursor = (rule.cursor + 1) % len(targets)
        return chosen
    return targets[0]


def assign_lead(lead: Lead, session: Session) -> bool:
    """对单条无主线索执行规则匹配并落 owner；返回是否分配。纯函数可测/dry-run 复用。"""
    rules = (
        session.query(AssignmentRule)
        .filter(AssignmentRule.enabled == 1)
        .order_by(AssignmentRule.priority.asc(), AssignmentRule.id.asc())
        .all()
    )
    return _assign_with_rules(lead, rules, session)


def _assign_with_rules(lead: Lead, rules: list[AssignmentRule], session: Session) -> bool:
    ent = session.get(Enterprise, lead.enterprise_id)
    for rule in rules:
        if not match_rule(rule, ent):
            continue
        target = pick_target(rule)
        if target is None:
            continue
        lead.owner_id = target
        lead.claimed_at = utcnow()
        rule.run_count = (rule.run_count or 0) + 1
        rule.last_run_at = utcnow()
        return True
    return False


def _before_flush(session: Session, _flush_context, _instances) -> None:
    """before_flush：给本次 flush 中新建的无主线索做自动分配。

    钩子内禁止再 flush/commit（会递归触发自身），只改属性随本次 flush 一并落库；
    遍历 session.new 而非 query——autoflush=False 下 pending 对象查询不可见。
    """
    try:
        pending = [o for o in session.new if isinstance(o, Lead) and o.owner_id is None]
        if not pending:
            return
        rules = (
            session.query(AssignmentRule)
            .filter(AssignmentRule.enabled == 1)
            .order_by(AssignmentRule.priority.asc(), AssignmentRule.id.asc())
            .all()
        )
        if not rules:
            return
        for lead in pending:
            _assign_with_rules(lead, rules, session)
    except Exception:  # noqa: BLE001 — 分配失败绝不阻断业务写入
        logger.exception("线索自动分配钩子异常")


def install() -> None:
    """注册全局 before_flush 钩子（幂等：重复调用只挂一次）。"""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
