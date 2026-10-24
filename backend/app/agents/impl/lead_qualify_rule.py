"""lead_qualify 规则实现：综合分 = ICP×0.4 + 意图×0.6 → S/A/B 分级。"""
from __future__ import annotations

from pydantic import BaseModel

from ...vertical import lead_grading
from ..base import BaseAgent
from ..registry import register_agent


class LeadQualifyInput(BaseModel):
    icp_score: float = 0.0
    intent_score: float = 0.0


class LeadQualifyOutput(BaseModel):
    composite: float
    grade: str


def qualify(icp_score: float, intent_score: float) -> dict:
    g = lead_grading()
    composite = icp_score * float(g.get("icp_weight", 0.4)) + intent_score * float(
        g.get("intent_weight", 0.6)
    )
    s_min = float(g.get("grade_s_min", 80))
    a_min = float(g.get("grade_a_min", 60))
    b_min = float(g.get("grade_b_min", 40))
    grade = "S" if composite >= s_min else "A" if composite >= a_min else "B" if composite >= b_min else "-"
    return {"composite": round(composite, 1), "grade": grade}


@register_agent("lead_qualify", backends={"rule": None})
class LeadQualifyRuleAgent(BaseAgent):
    name = "lead_qualify"
    input_schema = LeadQualifyInput
    output_schema = LeadQualifyOutput
    backend_kind = "rule"

    async def run(self, data: dict) -> dict:
        return qualify(float(data.get("icp_score", 0)), float(data.get("intent_score", 0)))
