"""vacuum.yaml 词表加载器 — 全平台行业配置的唯一入口。"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml

_CONFIG_PATH = os.environ.get(
    "VACLEAD_CONFIG",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "vacuum.yaml")
    ),
)


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---- 词表访问器 ----
def device_strong_terms() -> list[str]:
    return list(load()["device_terms"]["strong"])


# ggzy 名称检索专用宽词（该接口按企业名匹配，细分词如"罗茨泵"召回≈0；
# 品类宽词覆盖名称含"真空"的主体，细粒度意图交给 ccgp 全文检索）
GGZY_NAME_TERMS = [
    "真空", "真空设备", "真空泵", "真空技术", "真空科技", "真空工程",
    "镀膜设备", "镀膜机", "热处理设备", "冻干设备", "真空仪器", "真空装置",
]


def ggzy_name_terms() -> list[str]:
    return list(GGZY_NAME_TERMS)


def device_weak_terms() -> list[str]:
    return list(load()["device_terms"]["weak"])


def downstream_industries() -> list[dict]:
    return load()["downstream_industries"]


def industry_by_key(key: str) -> dict | None:
    for ind in downstream_industries():
        if ind["key"] == key:
            return ind
    return None


def manufacture_or_sale_terms() -> list[str]:
    return list(load()["manufacture_or_sale"])


def manufacturing_terms() -> list[str]:
    return list(load()["manufacturing_terms"])


def qualification_terms() -> list[str]:
    return list(load()["qualification_terms"])


def icp_weights() -> dict:
    return load()["icp_weights"]


def signal_weights() -> dict:
    return load()["signal_weights"]


def lead_grading() -> dict:
    return load()["lead_grading"]


def poi_cities() -> list[str]:
    return list(load()["poi_cities"])


def recycle_days() -> int:
    return int(load()["recycle_days"])


def profile_templates() -> list[dict]:
    return load().get("profile_templates", [])


def detect_industry(text: str) -> dict | None:
    """按下游行业关键词命中检测文本所属行业（tier 小者优先，长词优先）。"""
    best: dict | None = None
    for ind in downstream_industries():
        if any(kw in text for kw in ind["keywords"]):
            if best is None or ind["tier"] < best["tier"]:
                best = ind
    return best
