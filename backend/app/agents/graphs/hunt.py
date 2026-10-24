"""狩猎图（HuntGraph）：plan → collect → resolve → parse → score → notify 循环。

优先使用 LangGraph StateGraph；导入失败时降级为纯 Python 循环状态机。
对外统一入口：run_hunt(state, session=None) -> dict。
"""
from __future__ import annotations

from typing import Any, TypedDict

from ..registry import get_agent
from ..tools import TOOL_REGISTRY


class HuntState(TypedDict, total=False):
    city: str
    industry_keys: list[str]
    keywords: list[str]
    candidates: list[dict]
    rounds: int
    max_rounds: int
    min_new: int
    target: int
    signals: list[dict]
    enterprise_ids: list[int]
    errors: list[str]


DEFAULTS: dict[str, Any] = {
    "city": "",
    "industry_keys": [],
    "keywords": [],
    "candidates": [],
    "rounds": 0,
    "max_rounds": 3,
    "min_new": 2,
    "target": 50,
    "signals": [],
    "enterprise_ids": [],
    "errors": [],
}


def _plan(state: HuntState) -> dict:
    agent = get_agent("hunt_planner", backend="rule")
    result = agent.run_logged(
        {"city": state.get("city", ""), "industry_keys": state.get("industry_keys", [])}
    )
    return {"keywords": [p.get("keyword", "") for p in result.get("plan", []) if p.get("keyword")]}


def _collect(state: HuntState) -> dict:
    if not TOOL_REGISTRY.has("fetch_amap_poi"):
        return {"errors": state.get("errors", []) + ["collect 工具未注入，跳过"]}
    result = TOOL_REGISTRY.call("fetch_amap_poi", {"city": state.get("city", ""), "keywords": state.get("keywords", [])})
    if isinstance(result, dict) and result.get("error"):
        return {"errors": state.get("errors", []) + [f"collect: {result['error']}"]}
    pois = result if isinstance(result, list) else (result or {}).get("items", [])
    return {"candidates": state.get("candidates", []) + list(pois)}


def _resolve(state: HuntState) -> dict:
    agent = get_agent("entity_resolver", backend="rule")
    ids: list[int] = list(state.get("enterprise_ids", []))
    for cand in state.get("candidates", []):
        result = agent.run_logged({"name": cand.get("name", ""), "context": "poi"})
        eid = result.get("enterprise_id")
        if eid and eid not in ids:
            ids.append(eid)
    return {"enterprise_ids": ids}


def _parse(state: HuntState) -> dict:
    agent = get_agent("signal_parser", backend="rule")
    signals = list(state.get("signals", []))
    for cand in state.get("candidates", []):
        result = agent.run_logged({"title": cand.get("name", ""), "content": cand.get("address", "")})
        signals.extend(result.get("signals", []))
    return {"signals": signals}


def _score(state: HuntState) -> dict:
    if not TOOL_REGISTRY.has("recompute_scores"):
        return {}
    TOOL_REGISTRY.call("recompute_scores", {"enterprise_ids": state.get("enterprise_ids", [])})
    return {}


def _should_continue(state: HuntState) -> str:
    if len(state.get("enterprise_ids", [])) >= int(state.get("target", 50)):
        return "end"
    if int(state.get("rounds", 0)) + 1 >= int(state.get("max_rounds", 3)):
        return "end"
    return "plan"


def run_hunt(state: HuntState, session=None) -> dict:
    """纯 Python 降级实现（与 LangGraph 图同构，保证无依赖可用）。"""
    merged: HuntState = {**DEFAULTS, **state}  # type: ignore[typeddict-item]
    try:
        return _run_langgraph(merged, session)
    except ImportError:
        pass
    while True:
        merged.update(_plan(merged))
        merged.update(_collect(merged))
        merged.update(_resolve(merged))
        merged.update(_parse(merged))
        merged.update(_score(merged))
        merged["rounds"] = int(merged.get("rounds", 0)) + 1
        if _should_continue(merged) == "end":
            break
    return dict(merged)


def _run_langgraph(state: HuntState, session=None) -> dict:
    from langgraph.graph import END, StateGraph

    builder = StateGraph(HuntState)
    builder.add_node("plan", _plan)
    builder.add_node("collect", _collect)
    builder.add_node("resolve", _resolve)
    builder.add_node("parse", _parse)
    builder.add_node("score", _score)
    builder.set_entry_point("plan")
    builder.add_edge("plan", "collect")
    builder.add_edge("collect", "resolve")
    builder.add_edge("resolve", "parse")
    builder.add_edge("parse", "score")
    builder.add_conditional_edges(
        "score",
        _should_continue,
        {"plan": "plan", "end": END},
    )
    graph = builder.compile()
    final = graph.invoke(dict(state))
    return dict(final)
