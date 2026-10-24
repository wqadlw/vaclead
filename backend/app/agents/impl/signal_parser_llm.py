"""signal_parser LLM 后端 —— v1 stub（SPEC §0）。"""
from __future__ import annotations

from ..base import BaseAgent
from ..registry import register_agent
from .signal_parser_rule import SignalParserInput, SignalParserOutput


@register_agent("signal_parser", backends={"llm": None})
class SignalParserLlmAgent(BaseAgent):
    name = "signal_parser"
    backend_kind = "llm"
    input_schema = SignalParserInput
    output_schema = SignalParserOutput

    async def run(self, data: dict) -> dict:
        raise NotImplementedError("signal_parser llm 后端未实现（v1 无 LLM，默认用 rule 后端）")
