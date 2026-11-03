"""客户健康分与生命周期自动升降级（SPEC-AE，吸收 HubSpot lifecycle stage）。

健康分模型：基于可量化行为信号加权计算，无 LLM。
- 近 30 天有新信号 +30
- 近 7 天有跟进 +20
- 合同履约中 +20
- 超期未跟进 -20
- 超 30 天无任何信号 -30
分数 clamp 到 0-100。

生命周期自动流转：
- Lead.status 变为 won → lifecycle_stage 升为 customer_success
- 健康分 < 30 且 stage=customer_success → 降为 at_risk（触发续约预警 Todo）
- 健康分 ≥ 60 且 stage=at_risk → 升回 customer_success
- 同步更新 Enterprise.completeness 不受影响
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Contract, Enterprise, Followup, Lead, Signal, Todo, utcnow


def compute_health_score(session: Session, lead: Lead) -> int:
    """基于行为信号计算健康分 0-100。"""
    now = datetime.now(timezone.utc)
    score = 50  # 基线

    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")

    # 近 30 天有新信号 +30
    sig_count = (
        session.query(func.count())
        .select_from(Signal)
        .filter(Signal.enterprise_id == lead.enterprise_id, Signal.created_at >= cutoff_30d)
        .scalar()
        or 0
    )
    score += min(sig_count * 5, 30)

    # 近 7 天有跟进 +20
    fu_count = (
        session.query(func.count())
        .select_from(Followup)
        .join(Lead, Followup.lead_id == Lead.id)
        .filter(Lead.id == lead.id, Followup.created_at >= cutoff_7d)
        .scalar()
        or 0
    )
    score += min(fu_count * 10, 20)

    # 合同履约中 +20
    contract = (
        session.query(func.count())
        .select_from(Contract)
        .filter(Contract.lead_id == lead.id, Contract.status == "active")
        .scalar()
        or 0
    )
    if contract:
        score += 20

    # 超 7 天未跟进 -20（以真实最近跟进为准，last_followed_at 只是冗余字段）
    last_fu = (
        session.query(func.max(Followup.created_at))
        .join(Lead, Followup.lead_id == Lead.id)
        .filter(Lead.id == lead.id)
        .scalar()
    ) or lead.last_followed_at
    if last_fu and last_fu < cutoff_7d:
        score -= 20

    # 超 30 天无任何信号 -30
    last_sig = (
        session.query(func.max(Signal.created_at))
        .filter(Signal.enterprise_id == lead.enterprise_id)
        .scalar()
    )
    if not last_sig or last_sig < cutoff_30d:
        score -= 30

    return max(0, min(100, score))


def auto_lifecycle(session: Session, lead: Lead) -> str:
    """自动升降级生命周期阶段，返回新 stage。

    规则（HubSpot lifecycle stage 吸收）：
    - won → customer_success（一次性）
    - customer_success + 健康分 < 30 → at_risk
    - at_risk + 健康分 ≥ 60 → customer_success（回升）
    - 其他不变
    """
    score = compute_health_score(session, lead)
    lead.health_score = score

    stage = lead.lifecycle_stage or "lead"
    # 赢单判定看 lead.status（"won" 是状态机值，不是生命周期值）
    if lead.status == "won" and stage not in ("customer_success", "at_risk", "churned"):
        lead.lifecycle_stage = "customer_success"
        return "customer_success"
    if stage == "customer_success":
        if score < 30:
            lead.lifecycle_stage = "at_risk"
            return "at_risk"
        return "customer_success"
    if stage == "at_risk" and score >= 60:
        lead.lifecycle_stage = "customer_success"
        return "customer_success"
    return stage


def batch_health_refresh(session: Session) -> dict:
    """批量刷新所有活跃线索的健康分与生命周期（每日 cron 调用）。"""
    leads = (
        session.query(Lead)
        .filter(Lead.status.in_(["following", "intent", "won"]))
        .all()
    )
    updated = 0
    at_risk = 0
    for lead in leads:
        old_stage = lead.lifecycle_stage
        new_stage = auto_lifecycle(session, lead)
        if new_stage != old_stage:
            updated += 1
        if new_stage == "at_risk":
            at_risk += 1
    session.commit()
    return {"total": len(leads), "stage_changes": updated, "at_risk": at_risk}
