"""icp_judge LLM 后端（P2 预留 stub）。"""
from __future__ import annotations

from ..base import BaseAgent
from ..registry import register_agent
from .icp_judge_rule import IcpJudgeInput, IcpJudgeOutput


@register_agent("icp_judge", backends={"llm": None})
class IcpJudgeLlmAgent(BaseAgent):
    name = "icp_judge"
    input_schema = IcpJudgeInput
    output_schema = IcpJudgeOutput
    backend_kind = "llm"

    async def run(self, data: dict) -> dict:
        raise NotImplementedError("LLM backend 预留，未配置模型")
