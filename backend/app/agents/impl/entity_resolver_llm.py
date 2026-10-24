"""entity_resolver LLM 后端 —— v1 stub（SPEC §0）。"""
from __future__ import annotations

from ..base import BaseAgent
from ..registry import register_agent
from .entity_resolver_rule import EntityResolverInput, EntityResolverOutput


@register_agent("entity_resolver", backends={"llm": None})
class EntityResolverLlmAgent(BaseAgent):
    name = "entity_resolver"
    backend_kind = "llm"
    input_schema = EntityResolverInput
    output_schema = EntityResolverOutput

    async def run(self, data: dict) -> dict:
        raise NotImplementedError("entity_resolver llm 后端未实现（v1 无 LLM，默认用 rule 后端）")
