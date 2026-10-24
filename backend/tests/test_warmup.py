"""warmupramp 直译测试（对照 warmbly 语义）：净天数/目标量/日随机/健康五态。"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-warmup-"), "t.db")
)

from app.emailing.warmup import (  # noqa: E402
    HealthWindow,
    WarmupConfig,
    apply,
    can_exit,
    classify_health,
    daily_volume_factor,
    net_warmup_days,
    target_volume,
)

CFG = WarmupConfig(base=2, increase=1, max_per_day=20)


# ---- 净天数 ----
def test_net_days_basic():
    assert net_warmup_days(started_at_days_ago=10, spam_events=[], throttled=False, placed_last_48h=False) == 10


def test_net_days_spam_freeze_union_not_stack():
    # 两个相邻垃圾箱事件（第2天、第4天），冻结3天 → 区间 [2,5)+[4,7) 并集=5 天
    net = net_warmup_days(started_at_days_ago=10, spam_events=[2, 4], throttled=False, placed_last_48h=False)
    assert net == 5


def test_net_days_discounts():
    # 10 天无冻结，48h 内落箱 ×0.75 → 7.5 → floor 7；再 throttled ×0.5 → 3.75 → 3
    n1 = net_warmup_days(started_at_days_ago=10, spam_events=[], throttled=False, placed_last_48h=True)
    assert n1 == 7
    n2 = net_warmup_days(started_at_days_ago=10, spam_events=[], throttled=True, placed_last_48h=True)
    assert n2 == 3


# ---- 目标量 ----
def test_target_growth_and_cap():
    assert target_volume(CFG, 0, False) == 2
    assert target_volume(CFG, 5, False) == 7
    assert target_volume(CFG, 100, False) == 20  # cap


def test_active_campaign_caps_at_5():
    assert target_volume(CFG, 30, True) == 5


# ---- 日随机 ----
def test_daily_factor_deterministic():
    a = daily_volume_factor("acc-1", "2026-10-24")
    b = daily_volume_factor("acc-1", "2026-10-24")
    c = daily_volume_factor("acc-1", "2026-10-25")
    assert a == b and 0.60 <= c <= 1.10


def test_apply_floor_and_zero_start():
    # 刚开始预热（0 天）→ 0 封
    assert apply(CFG, account_id="a", date_iso="2026-10-24", started_at_days_ago=0, spam_events=[]) == 0
    # 正常路径保底 1
    v = apply(CFG, account_id="a", date_iso="2026-10-24", started_at_days_ago=1, spam_events=[])
    assert v >= 1


def test_apply_respects_partner_count():
    v = apply(
        CFG, account_id="a", date_iso="2026-10-24",
        started_at_days_ago=100, spam_events=[], available_partners=3,
    )
    assert v <= 3


# ---- 健康五态 ----
def test_health_insufficient_sample_is_healthy():
    assert classify_health([HealthWindow("spam_rate", 0.9, 5)]) == "healthy"


def test_health_escalation():
    w = [HealthWindow("spam_rate", 0.25, 50)]
    assert classify_health(w) == "throttled"
    c = [HealthWindow("complaint_rate_30d", 0.35, 200)]
    assert classify_health(c) == "blocked"
    b = [HealthWindow("bounce_rate", 0.12, 150)]
    assert classify_health(b) == "blocked"


def test_exit_requires_075_factor():
    # 进入 watch 的阈值 0.10；0.08（≥0.075）不可退出；0.07（<0.075）可退出
    assert not can_exit("watch", [HealthWindow("spam_rate", 0.08, 50)])
    assert can_exit("watch", [HealthWindow("spam_rate", 0.07, 50)])
