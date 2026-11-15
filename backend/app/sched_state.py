"""调度器心跳共享状态（SPEC-GUARD，2026-11-15 生产事故根治）。

main.py lifespan 写（哨兵 job 每 60s beat + 守护线程自愈），
api/tasks.py 读（/api/tasks/recent 暴露 scheduler_alive 给前端）——
独立小模块避免 main↔api 循环 import。
"""
from __future__ import annotations

import time

#: 最近一次哨兵 job 心跳（time.time() 秒）；0=尚未心跳。
LAST_BEAT: float = 0.0

#: 心跳超过该秒数视为调度器死亡（哨兵 interval=60s，3 倍容忍）。
STALE_AFTER_S = 180


def beat() -> None:
    """哨兵 job 每分钟调用——记录调度器活跃时刻。"""
    global LAST_BEAT
    LAST_BEAT = time.time()


def alive() -> bool:
    """调度器是否存活（心跳新鲜）。未启动（LAST_BEAT=0）返回 False。"""
    if LAST_BEAT <= 0:
        return False
    return (time.time() - LAST_BEAT) < STALE_AFTER_S
