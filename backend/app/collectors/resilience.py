"""采集韧性层（吸收 crawlee-python Session 错误积分制 + Statistics 指标）。

crawlee 的 Session 设计：max_error_score=3，失败 +1，成功 -0.5，超阈值判死。
vaclead 适配：每采集域（ggzy/ccgp）一个健康度单例，错误累积触发指数冷却；
成功逐步恢复。所有采集器共用，任务 state 可读指标。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("vaclead.collectors.resilience")

#: 各域默认配置（照 crawlee Session 默认值）
_DEFAULTS = {
    "max_error_score": 3.0,
    "error_score_decrement": 0.5,
    "error_increment": 1.0,
    "cooldown_base_s": 60.0,   # 判死后基础冷却 1 分钟，指数 ×2
    "cooldown_max_s": 3600.0,  # 最长 1 小时
}


class SessionHealth:
    """单采集域的健康度（错误积分 + 冷却）。

    用法（采集器内）：
        health = get_health("ggzy")          # 域级单例
        if health.is_blocked(): return []     # 冷却中直接短路
        try:
            resp = fetch(...)
            health.mark_good()
        except Exception:
            health.mark_bad()
            raise
    """

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.error_score = 0.0
        self.blocked_until = 0.0
        self.block_count = 0
        # 指标（吸收 crawlee Statistics：完成任务可见）
        self.requests_total = 0
        self.requests_failed = 0

    # ---- 判定 ----
    def is_blocked(self) -> bool:
        """冷却中返回 True（并打一条 debug 便于观测）。"""
        if time.monotonic() < self.blocked_until:
            return True
        return False

    # ---- 反馈 ----
    def mark_good(self) -> None:
        """成功：积分回落（不小于 0），计数。"""
        self.requests_total += 1
        self.error_score = max(0.0, self.error_score - _DEFAULTS["error_score_decrement"])

    def mark_bad(self) -> None:
        """失败：积分上升；超阈值进入指数冷却。"""
        self.requests_total += 1
        self.requests_failed += 1
        self.error_score += _DEFAULTS["error_increment"]
        if self.error_score >= _DEFAULTS["max_error_score"]:
            self._block()

    def _block(self) -> None:
        """判死：进入指数冷却，积分归零（等价 crawlee 的 retire+新 session）。"""
        self.block_count += 1
        wait = min(
            _DEFAULTS["cooldown_base_s"] * (2 ** (self.block_count - 1)),
            _DEFAULTS["cooldown_max_s"],
        )
        self.blocked_until = time.monotonic() + wait
        self.error_score = 0.0
        logger.warning(
            "[%s] 错误积分超阈值，冷却 %.0fs（第 %d 次）",
            self.domain, wait, self.block_count,
        )

    # ---- 观测 ----
    def stats(self) -> dict:
        ok = self.requests_total - self.requests_failed
        return {
            "requests_total": self.requests_total,
            "requests_ok": ok,
            "requests_failed": self.requests_failed,
            "error_score": round(self.error_score, 1),
            "blocked": self.is_blocked(),
            "block_count": self.block_count,
        }


#: 域级单例（进程内）
_HEALTHS: dict[str, SessionHealth] = {}


def get_health(domain: str) -> SessionHealth:
    if domain not in _HEALTHS:
        _HEALTHS[domain] = SessionHealth(domain)
    return _HEALTHS[domain]


def all_stats() -> dict[str, dict]:
    """全部域健康度（任务/调度可写入 state 便于观测）。"""
    return {d: h.stats() for d, h in _HEALTHS.items()}
