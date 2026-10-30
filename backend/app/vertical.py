"""industrial.yaml 词表加载器 — 全平台行业配置的唯一入口（SPEC-L 工业领域垂直）。

历史：v1 为 config/vacuum.yaml（真空设备单垂直）；2026-10-31 起 defaults 切到
industrial.yaml（多行业词表，真空词全保留），VACLEAD_CONFIG 仍可覆盖指向任意配置。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml

_CONFIG_PATH = os.environ.get(
    "VACLEAD_CONFIG",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "industrial.yaml")
    ),
)


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---- 词表访问器 ----
def device_strong_terms() -> list[str]:
    return list(load()["device_terms"]["strong"])


# ggzy 名称检索宽词 v1 遗留常量（真空单垂直时代）；现以配置的 core/extended 为准
GGZY_NAME_TERMS = [
    "真空", "真空设备", "真空泵", "真空技术", "真空科技", "真空工程",
    "镀膜设备", "镀膜机", "热处理设备", "冻干设备", "真空仪器", "真空装置",
]


def ggzy_name_terms() -> list[str]:
    """ggzy 企业名检索核心宽词（每日增量用，控制请求成本，SPEC-L 分级）。

    该接口按企业名匹配，细分词（罗茨泵级）召回≈0，品类宽词才有效；
    细粒度意图交给 ccgp 全文检索。
    """
    cfg = load()
    core = cfg.get("ggzy_name_terms_core")
    if core:
        return list(core)
    return list(cfg.get("ggzy_name_terms") or GGZY_NAME_TERMS)


def ggzy_extended_terms() -> list[str]:
    """ggzy 全量扫描扩展宽词（长尾品类；每日增量不使用，控制单日请求量）。"""
    return list(load().get("ggzy_name_terms_extended", []))


def icp_segments() -> dict:
    """ICP 分段权重（SPEC-F F2，opengtm 四段模式：size/industry/digital/pain）。"""
    return load().get("icp_segments", {})


def hiring_keywords() -> list[str]:
    """招聘信号词表（SPEC-F F3：在招设备岗=采购/扩张前兆）。"""
    return list(load().get("hiring_keywords", []))


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
