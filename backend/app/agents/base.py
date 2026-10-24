"""BaseAgent 抽象基类（docs/SPEC.md §3）。

子类契约：
- ``name``: Agent 唯一名（对应 registry 键）
- ``input_schema`` / ``output_schema``: Pydantic 模型类（MCP 风格 schema 描述）
- ``backend_kind``: 该实现类所属后端（"rule" / "llm"）
- ``run(data) -> dict``: 异步业务逻辑
- ``run_logged(data, session, task_id)``: 同步包装——计时 → run → record_run 落 AgentRun
"""
from __future__ import annotations

import abc
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

from .runs import record_run


def run_coroutine(coro):
    """在同步上下文执行 coroutine。

    - 当前线程无事件循环 → asyncio.run；
    - 已在事件循环内（如 FastAPI handler）→ 在独立线程的新循环中执行并等待结果。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class BaseAgent(abc.ABC):
    # ---- 子类必须覆写的类属性 ----
    name: str = "base"
    input_schema: type[BaseModel]  # noqa: PLW0212 占位声明，子类赋具体模型
    output_schema: type[BaseModel]
    backend_kind: str = "rule"  # 该实现类所属后端；实例的 self.backend 由构造参数决定

    def __init__(self, backend: Literal["rule", "llm"] = "rule") -> None:
        self.backend = backend

    @abc.abstractmethod
    async def run(self, data: dict) -> dict:
        """执行业务逻辑，输入输出均为 dict（对应 input/output_schema）。"""
        raise NotImplementedError

    # ---- 日志摘要（子类可覆写） ----
    def summarize(self, data: dict, result: dict) -> str:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
        return f"{self.name}[{self.backend}] {text}"[:200]

    # ---- 同步包装：计时 → run → record_run ----
    def run_logged(
        self,
        data: dict,
        session=None,
        task_id: str | None = None,
    ) -> dict:
        """同步执行 run() 并经 runs.record_run 写入 agent_run 日志。

        日志写入失败不影响返回结果（except 吞掉）；run() 抛出的业务异常原样传播。
        """
        t0 = time.perf_counter()
        result = run_coroutine(self.run(data))
        duration_ms = int((time.perf_counter() - t0) * 1000)
        try:
            record_run(
                agent_name=self.name,
                backend=self.backend,
                input=data,
                output=result,
                summary=self.summarize(data, result),
                duration_ms=duration_ms,
                task_id=task_id,
                session=session,
            )
        except Exception:  # noqa: BLE001 — 日志失败不影响 Agent 结果
            pass
        return result
