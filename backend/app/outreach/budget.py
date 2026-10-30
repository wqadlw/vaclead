"""发送预算链（SPEC-J J4，warmbly 预热思想）：当日可发数 = min(三层) - 已发。

三层：账户 daily_cap → 活动 daily_limit → 预热 ramp（净天数爬坡曲线）。
预热语义：warmup_started_at 为空的账户视为成熟账户（不设 ramp 限制）；
刚起步的账户沿 ramp 爬坡，quarantined/blocked 健康态直接归零。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..emailing.warmup import WarmupConfig, apply as warmup_apply
from ..models import Campaign, EmailAccount, EmailMessage

#: 健康态硬熔断：预算直接归零（warmbly blocked/quarantined 语义）
_HARD_STOP_HEALTH = ("quarantined", "blocked")


def sent_today(session: Session, account_email: str, day_iso: str | None = None) -> int:
    """该发信账户当日已发送数（sent_at ISO 字符串前缀=当日，UTC 口径）。"""
    day = day_iso or datetime.now(timezone.utc).date().isoformat()
    return (
        session.query(EmailMessage)
        .filter(
            EmailMessage.account_email == account_email,
            EmailMessage.status == "sent",
            EmailMessage.sent_at.like(f"{day}%"),
        )
        .count()
    )


def warmup_days_started(account: EmailAccount) -> int:
    """预热净起步天数；未设置返回 -1（调用方据此判定成熟账户）。"""
    if not account.warmup_started_at:
        return -1
    try:
        start = datetime.fromisoformat(account.warmup_started_at)
    except ValueError:
        return -1
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - start).days


def available_budget(session: Session, account: EmailAccount,
                     campaign: Campaign, day_iso: str | None = None) -> int:
    """当日该活动还可发送的封数（≥0）。"""
    if account.status != "active" or account.health in _HARD_STOP_HEALTH:
        return 0
    cap = min(account.daily_cap, campaign.daily_limit)
    days = warmup_days_started(account)
    if days >= 0:
        # 预热账户：ramp 曲线封顶（has_active_campaign=False——这是真实触达而非预热池）
        ramp = warmup_apply(
            WarmupConfig(max_per_day=cap),
            account_id=str(account.id),
            date_iso=day_iso or datetime.now(timezone.utc).date().isoformat(),
            started_at_days_ago=days,
            spam_events=[],
            throttled=account.health == "throttled",
            placed_last_48h=False,
            has_active_campaign=False,
        )
        cap = min(cap, ramp)
    already = sent_today(session, account.email, day_iso)
    return max(0, cap - already)
