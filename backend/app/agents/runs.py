"""agent_run 运行日志记录助手（docs/SPEC.md §1.8 / §3）。

record_run() 是所有 Agent 落日志的唯一入口；session 为空时自建
app.db.SessionLocal，JSON 序列化失败时降级 str()。
"""
from __future__ import annotations

import json
from typing import Any

from ..models import AgentRun


def _to_json(data: Any) -> str:
    """序列化为 JSON 字符串；dict 顶层的 'session' 键（DB 会话对象）先剔除，
    序列化失败时降级 str()。"""
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "session"}
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(data)


def record_run(
    agent_name: str,
    backend: str,
    input: Any,
    output: Any,
    summary: str,
    duration_ms: int,
    task_id: str | None = None,
    session=None,
) -> AgentRun:
    """写入一条 AgentRun 记录并提交。

    - session 为空时用 app.db.SessionLocal 自建（用完即关）；
    - input/output 中不可 JSON 化的内容自动降级 str()；
    - 返回已入库的 AgentRun 行。
    """
    own = False
    if session is None:
        from ..db import SessionLocal  # 延迟导入：避免 import 期触发全局 ENGINE

        session = SessionLocal()
        own = True
    try:
        row = AgentRun(
            agent_name=str(agent_name),
            backend=str(backend or "rule"),
            input_json=_to_json(input),
            output_json=_to_json(output),
            decision_summary=str(summary or "")[:500],
            duration_ms=int(duration_ms or 0),
            task_id=task_id,
        )
        session.add(row)
        session.commit()
        return row
    finally:
        if own:
            session.close()
