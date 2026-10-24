"""邮件预热 ramp 曲线（直译自 warmbly internal/app/warmupramp，纯函数无副作用）。

P2 邮件触达的地基：Target 计算某天该发几封预热邮件，Days 计算"净预热天数"
（扣除冻结时长），Apply 给出保底与确定性日随机。频控排程时强制执行。
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field


@dataclass
class WarmupConfig:
    base: int = 2            # 起始每日预热封数
    increase: int = 1        # 每净天增加
    max_per_day: int = 20    # 每日封顶
    reply_rate: float = 0.15  # 预热回信概率
    freeze_hours_after_spam: int = 72  # 进垃圾箱后冻结爬坡时长


def net_warmup_days(
    *,
    started_at_days_ago: int,
    spam_events: list[float],
    throttled: bool,
    placed_last_48h: bool,
) -> int:
    """净预热天数：日历天数扣除垃圾箱冻结期（多段 union 不叠加）。

    spam_events: 每次进垃圾箱距今天数（float）。
    """
    if started_at_days_ago <= 0:
        return 0
    # 冻结区间合并：每个事件冻结 [event, event + freeze_days)
    freeze_days = 3.0  # 72h
    intervals = sorted(
        (max(0.0, e), min(float(started_at_days_ago), e + freeze_days))
        for e in spam_events
        if 0 <= e <= started_at_days_ago
    )
    frozen = 0.0
    cur_start, cur_end = None, None
    for s, e in intervals:
        if cur_start is None:
            cur_start, cur_end = s, e
        elif s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            frozen += cur_end - cur_start
            cur_start, cur_end = s, e
    if cur_start is not None:
        frozen += cur_end - cur_start
    net = float(started_at_days_ago) - frozen
    # 48h 内有落箱 ×0.75；throttled 状态 ×0.5
    if placed_last_48h:
        net *= 0.75
    if throttled:
        net *= 0.5
    return max(0, int(math.floor(net)))


def target_volume(cfg: WarmupConfig, net_days: int, has_active_campaign: bool) -> int:
    """某天的预热目标：base + net_days*increase，cap 到 max；活跃 campaign 在投时限 5。"""
    t = cfg.base + net_days * cfg.increase
    if has_active_campaign and t > 5:
        t = 5
    return min(t, cfg.max_per_day)


def daily_volume_factor(account_id: str, date_iso: str) -> float:
    """确定性日随机系数 0.75–1.10（15% 概率 0.60）——防"每天一样多"。

    FNV-1a 思路：按 account+date 哈希，同一天重算结果一致（幂等，可离线审计）。
    """
    h = hashlib.sha256(f"{account_id}:{date_iso}".encode()).digest()
    u = h[0] / 255.0
    if u < 0.15:
        return 0.60
    u2 = h[1] / 255.0
    return 0.75 + u2 * 0.35


def apply(
    cfg: WarmupConfig,
    *,
    account_id: str,
    date_iso: str,
    started_at_days_ago: int,
    spam_events: list[float],
    throttled: bool = False,
    placed_last_48h: bool = False,
    has_active_campaign: bool = False,
    available_partners: int = 99,
) -> int:
    """综合：净天数 → 目标 → 日随机 → 不超过可用收件人数；保底 1 封（留心跳数据）。"""
    if started_at_days_ago <= 0:
        return 0
    net = net_warmup_days(
        started_at_days_ago=started_at_days_ago,
        spam_events=spam_events,
        throttled=throttled,
        placed_last_48h=placed_last_48h,
    )
    t = target_volume(cfg, net, has_active_campaign)
    t = int(t * daily_volume_factor(account_id, date_iso))
    t = min(t, available_partners)
    return max(1 if t > 0 else 0, t)  # 一旦开始预热，保底 1 封/天


# ---- 健康五态阈值表（直译自 warmbly warmup/service.go，含 0.75 退出防抖） ----

HEALTH_THRESHOLDS = {
    "spam_rate": {"watch": 0.10, "throttled": 0.20, "min_sample": 20},
    "complaint_rate_30d": {"watch": 0.03, "throttled": 0.10, "blocked": 0.30, "min_sample": 100},
    "warmup_complaint_rate_7d": {"watch": 0.005, "throttled": 0.015, "blocked": 0.03, "min_sample": 50},
    "bounce_rate": {"quarantined": 0.05, "blocked": 0.10, "min_sample": 100},
}
EXIT_FACTOR = 0.75  # 退出阈值 = 进入阈值 × 0.75（防抖）
PENALTY_DAYS = {"watch": 3, "throttled": 7, "quarantined": 30}


@dataclass
class HealthWindow:
    metric: str
    rate: float
    sample_size: int


def classify_health(windows: list[HealthWindow]) -> str:
    """根据各指标窗口判定五态：healthy/watch/throttled/quarantined/blocked。"""
    state = "healthy"
    rank = {"healthy": 0, "watch": 1, "throttled": 2, "quarantined": 3, "blocked": 4}
    for w in windows:
        th = HEALTH_THRESHOLDS.get(w.metric)
        if not th or w.sample_size < th.get("min_sample", 0):
            continue  # 样本不足不评判（学"非 G/M/Y host 只记录不评判"的诚实处理）
        if w.metric == "bounce_rate":
            if w.rate >= th["blocked"]:
                state = max(state, "blocked", key=lambda s: rank[s])
            elif w.rate >= th["quarantined"]:
                state = max(state, "quarantined", key=lambda s: rank[s])
            continue
        if w.rate >= th.get("blocked", 2.0):
            state = max(state, "blocked", key=lambda s: rank[s])
        elif w.rate >= th.get("throttled", 2.0):
            state = max(state, "throttled", key=lambda s: rank[s])
        elif w.rate >= th["watch"]:
            state = max(state, "watch", key=lambda s: rank[s])
    return state


def can_exit(state: str, windows: list[HealthWindow]) -> bool:
    """退出当前惩罚态：所有相关指标降至进入阈值 ×0.75 以下。"""
    for w in windows:
        th = HEALTH_THRESHOLDS.get(w.metric)
        if not th or w.sample_size < th.get("min_sample", 0):
            continue
        enter = th.get(state)
        if enter is not None and w.rate >= enter * EXIT_FACTOR:
            return False
    return True
