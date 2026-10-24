"""hunt_planner LLM 后端 —— v1 stub（SPEC §0：v1 无 LLM，双后端可切换）。"""
from __future__ import annotations

from ..base import BaseAgent
from ..registry import register_agent
from .hunt_planner_rule import HuntPlannerInput, HuntPlannerOutput


@register_agent("hunt_planner", backends={"llm": None})
class HuntPlannerLlmAgent(BaseAgent):
    name = "hunt_planner"
    backend_kind = "llm"
    input_schema = HuntPlannerInput
    output_schema = HuntPlannerOutput

    async def run(self, data: dict) -> dict:
        raise NotImplementedError("hunt_planner llm 后端未实现（v1 无 LLM，默认用 rule 后端）")
