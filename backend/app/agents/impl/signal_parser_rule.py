"""signal_parser 规则后端 —— 设备词表匹配（docs/SPEC.md §1.4 / §3.3）。

计分（config/vacuum.yaml signal_weights）：
- 标题命中强词 = tender_title_hit（70）
- 正文命中强词 = tender_body_hit（50）
- 标题命中弱词 = weak_discount × 强词标题分（0.5×70=35）；弱词仅标题有效
- excerpt = 命中处 ±60 字
"""
from __future__ import annotations

from pydantic import BaseModel

from ...vertical import device_strong_terms, device_weak_terms, signal_weights
from ..base import BaseAgent
from ..registry import register_agent

#: 摘录取词半径（命中处 ±60 字）
EXCERPT_RADIUS = 60
#: raw_excerpt 上限（docs/SPEC.md §1.4 ≤200 字）
EXCERPT_MAX = 200


def _excerpt(text: str, term: str, radius: int = EXCERPT_RADIUS) -> str:
    """返回 term 命中处 ±radius 字的摘录（找不到 term 时取开头窗口）。"""
    idx = text.find(term)
    if idx < 0:
        return text[: radius * 2 + len(term)]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(term) + radius)
    return text[start:end]


class SignalParserInput(BaseModel):
    title: str = ""
    content: str = ""
    pub_date: str | None = None
    url: str | None = None
    enterprise_name: str | None = None


class SignalParserOutput(BaseModel):
    signals: list[dict] = []


@register_agent("signal_parser", backends={"rule": None})
class SignalParserRuleAgent(BaseAgent):
    name = "signal_parser"
    backend_kind = "rule"
    input_schema = SignalParserInput
    output_schema = SignalParserOutput

    async def run(self, data: dict) -> dict:
        payload = data or {}
        title = str(payload.get("title") or "")
        content = str(payload.get("content") or "")

        weights = signal_weights()
        title_hit = float(weights.get("tender_title_hit", 70))
        body_hit = float(weights.get("tender_body_hit", 50))
        weak_discount = float(weights.get("weak_discount", 0.5))

        strong = device_strong_terms()
        weak = device_weak_terms()
        title_strong = [t for t in strong if t in title]
        body_strong = [t for t in strong if t in content]
        title_weak = [t for t in weak if t in title]

        # 优先级：标题强词 > 正文强词 > 标题弱词；弱词正文命中不计分
        hit_part, base_score, focus_term, hit_text = "", 0.0, "", ""
        if title_strong:
            hit_part, base_score, focus_term, hit_text = "title", title_hit, title_strong[0], title
        elif body_strong:
            hit_part, base_score, focus_term, hit_text = "body", body_hit, body_strong[0], content
        elif title_weak:
            hit_part = "title"
            base_score = round(title_hit * weak_discount, 2)  # 弱词减半：0.5×70=35
            focus_term, hit_text = title_weak[0], title

        if not hit_part:
            return {"signals": []}  # 无命中 → base_score 0，不产生信号

        matched: list[str] = []
        for term in title_strong + body_strong + title_weak:
            if term not in matched:
                matched.append(term)

        excerpt = _excerpt(hit_text, focus_term)[:EXCERPT_MAX]
        return {
            "signals": [
                {
                    "enterprise_name": str(payload.get("enterprise_name") or ""),
                    "matched_terms": matched,
                    "hit_part": hit_part,
                    "excerpt": excerpt,
                    "base_score": base_score,
                }
            ]
        }
