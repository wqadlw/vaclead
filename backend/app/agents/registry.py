"""Agent 注册表 + 后端切换（docs/SPEC.md §3）。

- ``@register_agent(name, backends={"rule": Cls})`` 装饰器：backends 值为 None 时
  表示"被装饰的类本身"（impl/ 下每对文件的用法）；也可显式传类。
- ``get_agent(name, backend=None)`` 工厂：backend 为 None 时读 settings_kv 表
  key='agent_backends' 的 JSON 覆盖（如 {"signal_parser":"llm"}），读不到用 "rule"。
  未注册的名字/后端抛 KeyError。
"""
from __future__ import annotations

import importlib
import json
from typing import Callable

from .base import BaseAgent

#: name -> {"rule": Cls, "llm": Cls}
AGENTS: dict[str, dict[str, type[BaseAgent]]] = {}

#: impl/ 下的实现模块（懒加载，避免 import 环）
_IMPL_MODULES: tuple[str, ...] = (
    "hunt_planner_rule",
    "hunt_planner_llm",
    "entity_resolver_rule",
    "entity_resolver_llm",
    "signal_parser_rule",
    "signal_parser_llm",
    "icp_judge_rule",
    "icp_judge_llm",
    "lead_qualify_rule",
    "lead_qualify_llm",
    "query_translator_rule",
    "query_translator_llm",
)

_LOADED = False


def _ensure_impls_loaded() -> None:
    """懒加载 impl 模块以触发 @register_agent 注册（幂等）。"""
    global _LOADED
    if _LOADED:
        return
    for mod in _IMPL_MODULES:
        importlib.import_module(f".impl.{mod}", __package__)
    _LOADED = True


def register_agent(name: str, backends: dict[str, type[BaseAgent] | None] | None = None):
    """类装饰器：把实现类注册到 AGENTS[name]。

    用法（impl/ 下成对文件）::

        @register_agent("signal_parser", backends={"rule": None})
        class SignalParserRuleAgent(BaseAgent): ...

        @register_agent("signal_parser", backends={"llm": None})
        class SignalParserLlmAgent(BaseAgent): ...

    backends 值传 None 代表被装饰的类；也支持显式传类
    ``register_agent("x", backends={"rule": RuleCls})``。不传 backends 时按
    ``cls.backend_kind`` 注册。
    """
    def deco(cls: type[BaseAgent]) -> type[BaseAgent]:
        reg = AGENTS.setdefault(name, {})
        if backends:
            for kind, target in backends.items():
                reg[kind] = cls if target is None else target
        else:
            reg[getattr(cls, "backend_kind", "rule")] = cls
        return cls

    return deco


def _settings_json() -> dict:
    """读取 settings_kv['agent_backends'] 的 JSON；任何失败返回 {}（回退 rule）。"""
    try:
        from ..db import SessionLocal
        from ..models import SettingsKV

        with SessionLocal() as s:
            row = s.query(SettingsKV).filter(SettingsKV.key == "agent_backends").first()
            if row is not None and row.value:
                data = json.loads(row.value)
                return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — 读不到配置就回退默认后端
        pass
    return {}


def _configured_backend(name: str) -> str:
    mapping = _settings_json()
    backend = mapping.get(name) if isinstance(mapping, dict) else None
    return backend if backend in ("rule", "llm") else "rule"


def get_agent(name: str, backend: str | None = None) -> BaseAgent:
    """Agent 工厂。

    - backend 显式传入时直接使用；
    - backend=None 时读 settings_kv('agent_backends') 覆盖，缺省 "rule"；
    - 名字或后端未注册抛 KeyError。
    """
    _ensure_impls_loaded()
    reg = AGENTS.get(name)
    if not reg:
        raise KeyError(f"agent not registered: {name}")
    if backend is None:
        backend = _configured_backend(name)
    cls = reg.get(backend)
    if cls is None:
        raise KeyError(f"backend '{backend}' not registered for agent '{name}'")
    return cls(backend=backend)


def registered_agents() -> list[str]:
    """当前已注册的 Agent 名单（触发懒加载）。"""
    _ensure_impls_loaded()
    return sorted(AGENTS)
