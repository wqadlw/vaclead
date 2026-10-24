"""query_translator LLM 后端（P2：自然语言 → 筛选条件）。"""
from __future__ import annotations

from ..base import BaseAgent
from ..registry import register_agent
from .query_translator_rule import QueryTranslatorInput, QueryTranslatorOutput


@register_agent("query_translator", backends={"llm": None})
class QueryTranslatorLlmAgent(BaseAgent):
    name = "query_translator"
    input_schema = QueryTranslatorInput
    output_schema = QueryTranslatorOutput
    backend_kind = "llm"

    async def run(self, data: dict) -> dict:
        raise NotImplementedError("LLM backend 预留，未配置模型")
