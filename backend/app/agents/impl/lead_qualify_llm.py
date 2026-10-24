"""lead_qualify LLM 后端（P2 预留 stub）。"""
from __future__ import annotations

from ..base import BaseAgent
from ..registry import register_agent
from .lead_qualify_rule import LeadQualifyInput, LeadQualifyOutput


@register_agent("lead_qualify", backends={"llm": None})
class LeadQualifyLlmAgent(BaseAgent):
    name = "lead_qualify"
    input_schema = LeadQualifyInput
    output_schema = LeadQualifyOutput
    backend_kind = "llm"

    async def run(self, data: dict) -> dict:
        raise NotImplementedError("LLM backend 预留，未配置模型")
