"""韧性层测试（吸收 crawlee Session 错误积分制）。"""
from __future__ import annotations

from app.collectors.resilience import SessionHealth, get_health


def test_error_score_and_block():
    h = SessionHealth("test-a")
    assert not h.is_blocked()
    h.mark_bad()  # 1.0
    h.mark_bad()  # 2.0
    assert not h.is_blocked()
    h.mark_bad()  # 3.0 → block，冷却 60s
    assert h.is_blocked()
    assert h.block_count == 1


def test_mark_good_recovers():
    h = SessionHealth("test-b")
    h.mark_bad()          # 1.0
    h.mark_good()         # 0.5
    h.mark_good()         # 0.0
    assert h.error_score == 0.0
    assert not h.is_blocked()


def test_exponential_cooldown():
    h = SessionHealth("test-c")
    for _ in range(6):
        h.mark_bad()
    # 3 次后第一次 block（60s），继续 3 次 → 第二次 block（120s）
    assert h.block_count == 2
    assert h.is_blocked()


def test_stats_shape():
    h = SessionHealth("test-d")
    h.mark_good()   # 0
    h.mark_good()   # 0
    h.mark_bad()    # 1.0
    s = h.stats()
    assert s["requests_total"] == 3
    assert s["requests_ok"] == 2
    assert s["requests_failed"] == 1
    assert s["error_score"] == 1.0


def test_domain_singleton():
    a = get_health("ggzy")
    b = get_health("ggzy")
    assert a is b
    c = get_health("ccgp")
    assert a is not c
