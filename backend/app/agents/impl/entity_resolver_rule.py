"""entity_resolver 规则后端 —— 复用 normalize 归一引擎（docs/SPEC.md §3.2）。

归一流程：后缀清洗 → 别名表 → rapidfuzz ≥90 合并 → 新建。
真实现见 app/normalize.py（已存在），本类只做 Agent 契约包装。
"""
from __future__ import annotations

from pydantic import BaseModel

from ...normalize import entity_resolver_agent
from ..base import BaseAgent
from ..registry import register_agent


class EntityResolverInput(BaseModel):
    name: str
    context: str | None = None
    source: str = "csv"


class EntityResolverOutput(BaseModel):
    enterprise_id: int
    is_new: bool
    confidence: float


@register_agent("entity_resolver", backends={"rule": None})
class EntityResolverRuleAgent(BaseAgent):
    name = "entity_resolver"
    backend_kind = "rule"
    input_schema = EntityResolverInput
    output_schema = EntityResolverOutput

    async def run(self, data: dict) -> dict:
        payload = dict(data or {})
        # 会话通过输入透传（DB 会话不可 JSON 序列化，record_run 会自动剔除该键）
        session = payload.pop("session", None)
        own = False
        if session is None:
            from ...db import SessionLocal  # 延迟导入，避免测试环境触发全局 ENGINE

            session = SessionLocal()
            own = True
        try:
            result = entity_resolver_agent(payload, session)
            if own:
                session.commit()
            return result
        finally:
            if own:
                session.close()
