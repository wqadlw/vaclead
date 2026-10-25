"""自动化规则 API（吸收 twenty workflow：规则定义 + 手动运行 + 运行记录可见）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import admin_only
from ..db import get_session
from ..jobs import run_automation_rules
from ..models import AutomationRule, User, utcnow

router = APIRouter(tags=["automation"])

ACTION_TYPES = {"notify", "create_lead", "tag_competitor", "bump_probability", "set_grade", "create_todo"}


class RuleIn(BaseModel):
    name: str
    enabled: int = 1
    cond_signal_type: str | None = None
    cond_industry_key: str | None = None
    cond_source: str | None = None
    cond_min_score: float | None = None
    cond_has_contact: int | None = None   # 1=有联系人 0=无 None=不限
    cond_min_intent: float | None = None  # 意图分下限
    action_type: str = "notify"           # v1 单动作（兼容）
    actions: list[str] | None = None      # 动作链（顺序执行；设置后覆盖 action_type）
    else_actions: list[str] | None = None # 条件不满足时的备选链


def _actions_of(r: AutomationRule) -> tuple[list[str], list[str]]:
    """读动作链（action_params.actions/else_actions；v1 单动作自动包成链）。"""
    import json as _json

    from ..jobs import _rule_actions

    return _rule_actions(r)


def _params_json(body: RuleIn) -> str:
    """把动作链组装进 action_params JSON（清掉旧 _last_signal_id 语义键时保留）。"""
    import json as _json

    params: dict = {}
    if body.actions:
        params["actions"] = body.actions
    if body.else_actions:
        params["else_actions"] = body.else_actions
    return _json.dumps(params, ensure_ascii=False)


def _rule_dict(r: AutomationRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "enabled": r.enabled,
        "cond_signal_type": r.cond_signal_type,
        "cond_industry_key": r.cond_industry_key,
        "cond_source": r.cond_source,
        "cond_min_score": r.cond_min_score,
        "action_type": r.action_type,
        "actions": _actions_of(r)[0],
        "else_actions": _actions_of(r)[1],
        "cond_has_contact": r.cond_has_contact,
        "cond_min_intent": r.cond_min_intent,
        "run_count": r.run_count,
        "last_run_at": r.last_run_at,
        "last_result": r.last_result,
    }


@router.get("/automation-rules")
def list_rules(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    rows = session.query(AutomationRule).order_by(AutomationRule.id).all()
    return {"items": [_rule_dict(r) for r in rows], "total": len(rows)}


@router.post("/automation-rules", status_code=201)
def create_rule(
    body: RuleIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    if not body.name.strip():
        raise HTTPException(422, "规则名称不能为空")
    if body.action_type not in ACTION_TYPES:
        raise HTTPException(422, f"动作类型需为：{'/'.join(sorted(ACTION_TYPES))}")
    rule = AutomationRule(
        name=body.name.strip(),
        enabled=body.enabled,
        cond_signal_type=body.cond_signal_type or None,
        cond_industry_key=body.cond_industry_key or None,
        cond_source=body.cond_source or None,
        cond_min_score=body.cond_min_score,
        cond_has_contact=body.cond_has_contact,
        cond_min_intent=body.cond_min_intent,
        action_type=body.action_type,
        action_params=_params_json(body),
    )
    session.add(rule)
    session.commit()
    return _rule_dict(rule)


@router.patch("/automation-rules/{rid}")
def patch_rule(
    rid: int,
    body: RuleIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    rule = session.get(AutomationRule, rid)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    if body.action_type not in ACTION_TYPES:
        raise HTTPException(422, f"动作类型需为：{'/'.join(sorted(ACTION_TYPES))}")
    rule.name = body.name.strip() or rule.name
    rule.enabled = body.enabled
    rule.cond_signal_type = body.cond_signal_type or None
    rule.cond_industry_key = body.cond_industry_key or None
    rule.cond_source = body.cond_source or None
    rule.cond_min_score = body.cond_min_score
    rule.cond_has_contact = body.cond_has_contact
    rule.cond_min_intent = body.cond_min_intent
    rule.action_type = body.action_type
    # 重建动作链 JSON（保留 _last_signal_id 水位）
    import json as _json

    try:
        prev = _json.loads(rule.action_params or "{}")
    except ValueError:
        prev = {}
    prev.pop("actions", None)
    prev.pop("else_actions", None)
    if body.actions:
        prev["actions"] = body.actions
    if body.else_actions:
        prev["else_actions"] = body.else_actions
    rule.action_params = _json.dumps(prev, ensure_ascii=False)
    rule.updated_at = utcnow()
    session.commit()
    return _rule_dict(rule)


@router.delete("/automation-rules/{rid}")
def delete_rule(
    rid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    rule = session.get(AutomationRule, rid)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    session.delete(rule)
    session.commit()
    return {"ok": True}


@router.post("/automation-rules/run")
def run_rules_now(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """手动执行一轮规则扫描（每日调度 04:06 与采集任务尾也会自动执行）。"""
    stats = run_automation_rules(session)
    return stats


@router.get("/automation-rules/{rid}/runs")
def rule_runs(
    rid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """规则运行明细（吸收 twenty workflow-run：逐条执行记录可排障）。"""
    from ..models import Enterprise, WorkflowRun

    rule = session.get(AutomationRule, rid)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    rows = (
        session.query(WorkflowRun)
        .filter(WorkflowRun.rule_id == rid)
        .order_by(WorkflowRun.id.desc())
        .limit(50)
        .all()
    )
    ent_ids = {r.enterprise_id for r in rows}
    ents = (
        {e.id: e for e in session.query(Enterprise).filter(Enterprise.id.in_(ent_ids)).all()}
        if ent_ids
        else {}
    )
    return {
        "items": [
            {
                "id": r.id,
                "signal_id": r.signal_id,
                "enterprise_name": ents[r.enterprise_id].name if r.enterprise_id in ents else "?",
                "action_type": r.action_type,
                "result": r.result,
                "ok": r.ok,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/automation-rules/{rid}/dry-run")
def rule_dry_run(
    rid: int,
    limit: int = 10,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """预览：按规则条件扫描最近信号，返回会命中的企业/动作，不执行任何写操作。

    二十没有但销售刚需——规则上线前先看它会打到谁。
    """
    from ..jobs import _rule_actions, _rule_matches
    from ..models import Enterprise, Signal

    rule = session.get(AutomationRule, rid)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    signals = session.query(Signal).order_by(Signal.id.desc()).limit(300).all()
    hits: list[dict] = []
    else_hits = 0
    actions_chain, else_chain = _rule_actions(rule)
    for sig in signals:
        if len(hits) >= limit:
            break
        ent = session.get(Enterprise, sig.enterprise_id)
        if ent is None:
            continue
        if _rule_matches(rule, sig, ent, session):
            hits.append({
                "signal_id": sig.id,
                "enterprise_name": ent.name,
                "signal_title": sig.title[:40],
                "would_run": actions_chain,
            })
        else:
            else_hits += 1
    return {
        "rule": rule.name,
        "would_hit": len(hits),
        "else_branch_would": else_hits,
        "hits": hits,
        "actions": actions_chain,
        "else_actions": else_chain,
    }
