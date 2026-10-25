"""后台任务生命周期包装器（SPEC-E E2：从 tasks.py 四个 worker 提取的同构样板）。

审计确认的重复模式（tasks.py 原 531-577 / 753-799 / 1117-1147 逐字重复）：
- 尾部钩子序列：meili 同步 → 自动化规则扫播 → 订阅通知扫播（各自独立 try/except）
- collector_health 指标落账
- done / TaskCancelled / Exception 三分支收尾
- threading.Thread(daemon=True) 启动

行为对齐约定：checkpoint 字段、SSE 事件序列与原实现逐项一致（E3 测试锁住）。
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import TaskCheckpoint, utcnow
from .tasks import TaskCancelled, _broadcast, _checkpoint, _log  # noqa: PLC2701

logger = logging.getLogger("vaclead.api.task_runtime")


def signal_exists(session: Session, enterprise_id: int, url: str, title: str) -> bool:
    """信号查重三元组（审计点：tasks.py 5 处 + jobs.py 2 处重复的统一收口）。"""
    from ..models import Signal

    return (
        session.query(Signal)
        .filter(
            Signal.enterprise_id == enterprise_id,
            Signal.url == url,
            Signal.title == title,
        )
        .first()
        is not None
    )


def tail_hooks(task_id: str, *, with_rules: bool = True) -> None:
    """任务尾部钩子序列（各钩子独立容错，失败不影响主结果）。

    meili 同步 →（可选）自动化规则扫播 → 订阅通知扫播。
    """
    try:
        from .. import search as meili

        with SessionLocal() as s:
            meili.sync_all(s)
    except Exception:  # noqa: BLE001
        logger.warning("任务尾 Meili 同步失败（task=%s）", task_id, exc_info=True)
    if with_rules:
        try:
            from ..jobs import run_automation_rules

            with SessionLocal() as s:
                run_automation_rules(s)
        except Exception:  # noqa: BLE001
            logger.warning("任务尾规则扫播失败（task=%s）", task_id, exc_info=True)
    try:
        from ..jobs import generate_notifications

        with SessionLocal() as s:
            generate_notifications(s)
    except Exception:  # noqa: BLE001
        logger.warning("任务尾通知扫播失败（task=%s）", task_id, exc_info=True)


def health_into_totals(totals: dict) -> None:
    """collector_health 指标并入 totals（吸收 crawlee Statistics）。"""
    try:
        from ..collectors.resilience import all_stats

        if all_stats():
            totals["collector_health"] = all_stats()
    except Exception:  # noqa: BLE001
        pass


def finish(task_id: str, totals: dict) -> None:
    """done 收尾：钩子 → 健康度 → checkpoint(done) → broadcast → log。"""
    tail_hooks(task_id)
    health_into_totals(totals)
    _checkpoint(task_id, status="done", progress=100,
                state_json=json.dumps(totals, ensure_ascii=False))
    _broadcast(task_id, {"type": "done", "summary": totals})
    _log(task_id, f"任务完成：{totals}")


def lifecycle(task_id: str, worker: Callable[[], dict], *, label: str) -> None:
    """统一生命周期：worker 抛 TaskCancelled → cancelled；其他异常 → failed；正常 → finish。

    worker 返回 totals dict（完成态落账用）。
    """
    try:
        totals = worker()
        finish(task_id, totals)
    except TaskCancelled:
        with SessionLocal() as s:
            cp = s.get(TaskCheckpoint, task_id)
            if cp is not None:
                cp.status = "cancelled"
                cp.updated_at = utcnow()
                s.commit()
        _broadcast(task_id, {"type": "cancelled"})
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底
        logger.exception("任务 %s(%s) 失败", label, task_id)
        _checkpoint(task_id, status="failed",
                    state_json=json.dumps({"error": str(exc)}, ensure_ascii=False))
        _broadcast(task_id, {"type": "error", "log": f"任务失败：{exc}"})


def spawn(task_id: str, worker: Callable[[], dict], *, label: str) -> None:
    """启动后台线程并套生命周期包装。"""
    threading.Thread(
        target=lifecycle, args=(task_id, worker), kwargs={"label": label}, daemon=True
    ).start()
