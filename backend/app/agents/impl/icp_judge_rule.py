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


# ---------------------------------------------------------------------------
# ICP 分段评分（SPEC-F F2，吸收 opengtm qualify 四段模式）：
# size/industry/digital/pain 各段独立 (score, reasons)，
# reason 带"段名:"前缀供前端分组；段分不改变总分（总分仍走原可解释规则）。
# ---------------------------------------------------------------------------
def _segment_size(ent_name: str, scope: str, capital, signal_active: bool) -> tuple[int, list[dict]]:
    from ...vertical import icp_segments

    seg = icp_segments().get("size", {})
    score, reasons = 0, []
    if capital is not None:
        if capital >= 1000:
            score += seg.get("capital_gte_1000w", 12)
            reasons.append({"rule": "规模:注册资本≥1000万", "points": seg.get("capital_gte_1000w", 12)})
        elif capital >= 100:
            score += seg.get("capital_100w_1000w", 7)
            reasons.append({"rule": "规模:注册资本100-1000万", "points": seg.get("capital_100w_1000w", 7)})
    if signal_active:
        score += seg.get("signal_active_90d", 5)
        reasons.append({"rule": "规模:近90天有采购信号", "points": seg.get("signal_active_90d", 5)})
    return min(score, seg.get("max", 20)), reasons


def _segment_industry(industry_key: str | None) -> tuple[int, list[dict]]:
    from ...vertical import icp_segments, downstream_industries

    seg = icp_segments().get("industry", {})
    score, reasons = 0, []
    tier_map = {ind["key"]: ind["tier"] for ind in downstream_industries()}
    tier = tier_map.get(industry_key or "")
    if tier == 1:
        score += seg.get("tier1", 40)
        reasons.append({"rule": "行业:一线下游(半导体/镀膜/科研)", "points": seg.get("tier1", 40)})
    elif tier == 2:
        score += seg.get("tier2", 30)
        reasons.append({"rule": "行业:二线下游(光伏/锂电/冻干)", "points": seg.get("tier2", 30)})
    elif tier == 3:
        score += seg.get("generic", 10)
        reasons.append({"rule": "行业:泛制造(热处理)", "points": seg.get("generic", 10)})
    if industry_key == "research_edu":
        score += seg.get("research_edu_bonus", 5)
        reasons.append({"rule": "行业:科研教育加成(政采主力)", "points": seg.get("research_edu_bonus", 5)})
    return min(score, seg.get("max", 45)), reasons


_TECH_TERMS = ("研发", "技术", "智能", "自动化", "半导体", "精密", "新材料", "新能源")


def _segment_digital(scope: str) -> tuple[int, list[dict]]:
    from ...vertical import icp_segments

    seg = icp_segments().get("digital", {})
    score, reasons = 0, []
    if any(t in scope for t in _TECH_TERMS):
        score += seg.get("tech_terms", 10)
        reasons.append({"rule": "数字化:经营范围含技术词", "points": seg.get("tech_terms", 10)})
    return min(score, seg.get("max", 15)), reasons


def _segment_pain(scope: str, strong_terms: list[str], has_tender: bool) -> tuple[int, list[dict]]:
    from ...vertical import icp_segments

    seg = icp_segments().get("pain", {})
    score, reasons = 0, []
    if any(t in scope for t in strong_terms):
        score += seg.get("device_usage", 10)
        reasons.append({"rule": "意图:经营范围含设备使用词", "points": seg.get("device_usage", 10)})
    if has_tender:
        score += seg.get("tender_intent", 15)
        reasons.append({"rule": "意图:有招标/中标记录", "points": seg.get("tender_intent", 15)})
    return min(score, seg.get("max", 20)), reasons


def judge_segments(name: str, business_scope: str, registered_capital_wan=None,
                   industry_key=None, signal_active=False, has_tender=False) -> dict:
    """四段独立评分（情报页分组展示用；总分仍由 judge_enterprise 计算）。"""
    scope = business_scope or ""
    from ...vertical import device_strong_terms

    strong = device_strong_terms()
    size_s, size_r = _segment_size(name, scope, registered_capital_wan, signal_active)
    ind_s, ind_r = _segment_industry(industry_key)
    dig_s, dig_r = _segment_digital(scope)
    pain_s, pain_r = _segment_pain(scope, strong, has_tender)
    return {
        "segments": {
            "size": {"score": size_s, "max": 20, "reasons": size_r},
            "industry": {"score": ind_s, "max": 45, "reasons": ind_r},
            "digital": {"score": dig_s, "max": 15, "reasons": dig_r},
            "pain": {"score": pain_s, "max": 20, "reasons": pain_r},
        },
        "total": size_s + ind_s + dig_s + pain_s,
    }
