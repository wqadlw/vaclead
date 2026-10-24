"""ToolRegistry —— MCP 风格工具注册表（docs/SPEC.md §3）。

- ``@tool(name, description, input_schema)`` 装饰器：把函数注册为工具真实现；
- ``TOOL_REGISTRY.register(name, description, input_schema)``：占位注册（无实现）；
- ``register_function(name, fn)``：外部（API 层/采集器）注入真实现，fn=None 重置回占位；
- ``call(name, payload)``：未注入实现返回 {"error": "not_implemented"}；
- ``list_tools()``：MCP 风格 [{name, description, inputSchema}]。

预注册 9 个工具均为占位：search_enterprises / resolve_entity / upsert_signal /
recompute_scores / create_lead / fetch_amap_poi / fetch_tender_csv /
save_checkpoint / notify_todo。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from .base import run_coroutine


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: type[BaseModel]
    fn: Callable[[dict], Any] | None = None


class ToolRegistry:
    """工具注册表（进程内单例 TOOL_REGISTRY）。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: type[BaseModel],
        fn: Callable[[dict], Any] | None = None,
    ) -> None:
        """注册（或覆盖）一个工具定义；fn=None 即占位注册。"""
        self._tools[name] = ToolSpec(
            name=name, description=description, input_schema=input_schema, fn=fn
        )

    def register_function(self, name: str, fn: Callable[[dict], Any] | None) -> None:
        """注入真实现；fn=None 重置回占位。工具名未注册抛 KeyError。"""
        spec = self._tools[name]  # KeyError: unknown tool
        spec.fn = fn

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, payload: dict | None = None) -> Any:
        """调用工具。未注册 → {"error":"unknown_tool"}；占位未注入 →
        {"error":"not_implemented"}。注入的实现支持同步/异步函数。"""
        spec = self._tools.get(name)
        if spec is None:
            return {"error": "unknown_tool", "detail": name}
        if spec.fn is None:
            return {"error": "not_implemented"}
        result = spec.fn(payload or {})
        if inspect.isawaitable(result):
            result = run_coroutine(result)
        return result

    def list_tools(self) -> list[dict]:
        """MCP 风格工具清单。"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "inputSchema": s.input_schema.model_json_schema(),
            }
            for s in self._tools.values()
        ]


#: 全局注册表
TOOL_REGISTRY = ToolRegistry()


def tool(name: str, description: str, input_schema: type[BaseModel]):
    """装饰器：把被装饰函数注册为工具的真实现。"""

    def deco(fn: Callable[[dict], Any]) -> Callable[[dict], Any]:
        TOOL_REGISTRY.register(name, description, input_schema, fn=fn)
        return fn

    return deco


# --------------------------------------------------------------------------
# 预注册工具的输入 schema（MCP 风格描述，真实现由外部注入）
# --------------------------------------------------------------------------
class SearchEnterprisesInput(BaseModel):
    q: str | None = None
    industry_key: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    capital_min: float | None = None
    capital_max: float | None = None
    signal_days: int | None = None
    score_min: float | None = None
    grade: str | None = None
    is_competitor: bool = False
    page: int = 1
    page_size: int = 20


class ResolveEntityInput(BaseModel):
    name: str
    context: str | None = None
    source: str = "csv"


class UpsertSignalInput(BaseModel):
    enterprise_id: int
    type: str = "tender"
    title: str
    url: str = ""
    pub_date: str | None = None
    matched_terms: list[str] = []
    hit_part: str = "body"
    raw_excerpt: str = ""
    base_score: float = 0.0


class RecomputeScoresInput(BaseModel):
    enterprise_id: int


class CreateLeadInput(BaseModel):
    enterprise_id: int
    owner_id: int | None = None


class FetchAmapPoiInput(BaseModel):
    city: str
    keywords: list[str] = []
    amap_key: str | None = None


class FetchTenderCsvInput(BaseModel):
    days: int = 30


class SaveCheckpointInput(BaseModel):
    task_id: str
    kind: str = "poi"
    status: str = "running"
    state: dict = {}
    progress: int = 0


class NotifyTodoInput(BaseModel):
    user_id: int
    lead_id: int | None = None
    action: str = ""


_PREDEFINED: tuple[tuple[str, str, type[BaseModel]], ...] = (
    (
        "search_enterprises",
        "FTS5 + facet 过滤查询企业库，返回分页企业列表",
        SearchEnterprisesInput,
    ),
    (
        "resolve_entity",
        "企业名归一解析：后缀清洗→别名表→rapidfuzz≥90 合并，返回 enterprise_id/is_new/confidence",
        ResolveEntityInput,
    ),
    (
        "upsert_signal",
        "按 (url, enterprise_id, type) 幂等写入意图信号",
        UpsertSignalInput,
    ),
    (
        "recompute_scores",
        "重算企业 ICP 分与意图时间衰减分",
        RecomputeScoresInput,
    ),
    ("create_lead", "为企业创建/领取线索（撞单保护）", CreateLeadInput),
    (
        "fetch_amap_poi",
        "高德 POI 采集：按城市×关键词抓取候选企业 {name,address,location,type_code}",
        FetchAmapPoiInput,
    ),
    (
        "fetch_tender_csv",
        "拉取近 N 天知了标讯列表，产出 {title,content,url,pub_date}",
        FetchTenderCsvInput,
    ),
    (
        "save_checkpoint",
        "任务断点落 task_checkpoint 表（JSON state + progress）",
        SaveCheckpointInput,
    ),
    ("notify_todo", "推送待办提醒给指定用户", NotifyTodoInput),
)


def _register_predefined() -> None:
    for name, desc, schema in _PREDEFINED:
        TOOL_REGISTRY.register(name, desc, schema, fn=None)


_register_predefined()
