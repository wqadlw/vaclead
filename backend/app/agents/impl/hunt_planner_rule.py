"""hunt_planner 规则后端 —— 城市×行业关键词狩猎计划（docs/SPEC.md §3.1）。"""
from __future__ import annotations

from pydantic import BaseModel

from ...vertical import downstream_industries, industry_by_key
from ..base import BaseAgent
from ..registry import register_agent

#: 计划关键词上限（防止全行业展开过大）
_MAX_PLAN_ITEMS = 60


class HuntPlannerInput(BaseModel):
    city: str = ""
    industry_keys: list[str] = []
    keywords_override: list[str] | None = None


class HuntPlannerOutput(BaseModel):
    plan: list[dict] = []


@register_agent("hunt_planner", backends={"rule": None})
class HuntPlannerRuleAgent(BaseAgent):
    name = "hunt_planner"
    backend_kind = "rule"
    input_schema = HuntPlannerInput
    output_schema = HuntPlannerOutput

    async def run(self, data: dict) -> dict:
        payload = data or {}
        # 1) 显式关键词覆盖优先
        if payload.get("keywords_override"):
            plan = [
                {"keyword": str(kw), "grid_cells": 1}
                for kw in dict.fromkeys(str(k) for k in payload["keywords_override"] if k)
            ]
            return {"plan": plan[:_MAX_PLAN_ITEMS]}

        # 2) 行业 keys → 行业关键词（城市×行业关键词，去重保序）
        keys = payload.get("industry_keys") or []
        industries = [industry_by_key(str(k)) for k in keys]
        industries = [ind for ind in industries if ind is not None]
        if not industries:  # 未指定行业 → 全行业展开
            industries = downstream_industries()

        seen: set[str] = set()
        plan: list[dict] = []
        for ind in industries:
            for kw in ind.get("keywords", []):
                if kw and kw not in seen:
                    seen.add(kw)
                    plan.append({"keyword": kw, "grid_cells": 1})
        return {"plan": plan[:_MAX_PLAN_ITEMS]}
