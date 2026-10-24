"""query_translator 规则实现（v1 不支持自然语言，返回 unsupported）。"""
from __future__ import annotations

from pydantic import BaseModel

from ..base import BaseAgent
from ..registry import register_agent


class QueryTranslatorInput(BaseModel):
    natural_text: str = ""


class QueryTranslatorOutput(BaseModel):
    supported: bool
    filter_params: dict


@register_agent("query_translator", backends={"rule": None})
class QueryTranslatorRuleAgent(BaseAgent):
    name = "query_translator"
    input_schema = QueryTranslatorInput
    output_schema = QueryTranslatorOutput
    backend_kind = "rule"

    async def run(self, data: dict) -> dict:
        return {"supported": False, "filter_params": {}}
