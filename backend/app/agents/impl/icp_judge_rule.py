"""icp_judge 规则实现：企业档案 → ICP 评分 + 命中理由 + 同行判定。

与 app.api.imports.apply_icp 兜底逻辑保持同一套规则（vacuum.yaml icp_weights）。
"""
from __future__ import annotations

from pydantic import BaseModel

from ...vertical import (
    device_strong_terms,
    detect_industry,
    icp_weights,
    manufacturing_terms,
    qualification_terms,
)
from ..base import BaseAgent
from ..registry import register_agent


class IcpJudgeInput(BaseModel):
    name: str = ""
    business_scope: str = ""
    registered_capital_wan: float | None = None
    industry_key: str | None = None
    industry_name: str | None = None


class IcpJudgeOutput(BaseModel):
    icp_score: float
    icp_reasons: list[dict]
    is_competitor: int


def judge_enterprise(
    name: str,
    business_scope: str,
    registered_capital_wan: float | None = None,
    industry_key: str | None = None,
    industry_name: str | None = None,
) -> dict:
    w = icp_weights()
    scope = business_scope or ""
    text_all = f"{name} {scope} {industry_name or ''}"
    reasons: list[dict] = []
    score = 0.0

    ind = detect_industry(text_all)
    if ind is not None:
        pts = w.get(f"industry_tier{ind['tier']}", 0)
        score += pts
        reasons.append({"rule": f"行业命中:{ind['name']}(tier{ind['tier']})", "points": pts})

    strong = device_strong_terms()
    equipment_markers = ("设备制造", "设备生产", "设备销售", "真空泵", "真空机组")
    is_competitor = ("真空" in name) and ("设备" in name or any(m in scope for m in equipment_markers))
    if not is_competitor and any(t in scope for t in strong):
        pts = w.get("device_usage_terms", 0)
        score += pts
        reasons.append({"rule": "经营范围含设备使用词", "points": pts})

    cap = registered_capital_wan
    if cap is not None and cap >= 1000:
        pts = w.get("capital_gte_1000w", 0)
        score += pts
        reasons.append({"rule": "注册资本≥1000万", "points": pts})
    elif cap is not None and cap >= 100:
        pts = w.get("capital_100w_1000w", 0)
        score += pts
        reasons.append({"rule": "注册资本100-1000万", "points": pts})

    if any(t in scope for t in manufacturing_terms()):
        pts = w.get("manufacturing_terms", 0)
        score += pts
        reasons.append({"rule": "经营范围含生产/制造/加工", "points": pts})

    if any(t in text_all for t in qualification_terms()):
        pts = w.get("qualification", 0)
        score += pts
        reasons.append({"rule": "专精特新/高企标识", "points": pts})

    return {
        "icp_score": min(round(score, 1), float(w.get("max_score", 100))),
        "icp_reasons": reasons,
        "is_competitor": 1 if is_competitor else 0,
    }


@register_agent("icp_judge", backends={"rule": None})
class IcpJudgeRuleAgent(BaseAgent):
    name = "icp_judge"
    input_schema = IcpJudgeInput
    output_schema = IcpJudgeOutput
    backend_kind = "rule"

    async def run(self, data: dict) -> dict:
        return judge_enterprise(
            name=data.get("name", ""),
            business_scope=data.get("business_scope", ""),
            registered_capital_wan=data.get("registered_capital_wan"),
            industry_key=data.get("industry_key"),
            industry_name=data.get("industry_name"),
        )
