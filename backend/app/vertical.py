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


def brand_terms() -> list[str]:
    """品牌词表（SPEC-BIM B1，照 device_strong_terms 模式）：真空泵/压缩机
    品牌中英对照——brand_extract 装机抽取与跨品牌维修面的品牌锚定词库。"""
    return list(load()["brand_terms"])


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


#: sdgp 专属扩展词（SPEC-SDGP 词表扩容 2026-11-15，山东大本营全行业截获）：
#: 真空品类词之外，按 SEGMENTATION 买家侧细分补齐——山东双时段 cron 每词×
#: 两栏目×2 页，40 词≈160 请求/轮（1.5s 间隔 ≈4 分钟），配额可承受。
SDGP_EXTRA_TERMS = [
    "冻干", "脱水蔬菜", "真空包装", "镀铝膜", "吸塑", "厚片",
    "热处理", "真空淬火", "晶圆", "封装测试", "光伏", "动力电池",
    "药业", "生物科技", "精细化工", "石墨碳素", "分析测试", "表面处理",
]


def sdgp_terms() -> list[str]:
    """山东政采标题检索词表 = ggzy 品类宽词 ∪ 山东大本营行业细分（去重保序）。

    标题检索与浙江同款（BS 轮教训：项目名/标题库用品类宽词）——但山东是
    大本营，扩 SEGMENTATION 买家侧细分全行业截获，边际成本仅请求量。"""
    out: list[str] = []
    for t in [*ggzy_name_terms(), *SDGP_EXTRA_TERMS]:
        if t and t not in out:
            out.append(t)
    return out


def icp_segments() -> dict:
    """ICP 分段权重（SPEC-F F2，opengtm 四段模式：size/industry/digital/pain）。"""
    return load().get("icp_segments", {})


def hiring_keywords() -> list[str]:
    """招聘信号词表（SPEC-F F3：在招设备岗=采购/扩张前兆）。"""
    return list(load().get("hiring_keywords", []))


def device_weak_terms() -> list[str]:
    return list(load()["device_terms"]["weak"])


# ---- 公共机构排除（2026-11-14 用户拍板："学校请出推荐"先例扩面）----
# 科研教育（2026-11-08 拍板）+ 医疗卫生（医院/诊所/体检=服务机构，非设备买方）
# + 检验检测事业单位（"XX检验检测中心/计量所"形态，名称不含"公司"）。
# 处置与学校同款：分类保留可查（企业列表/地图/标讯不受影响），
# 仅从所有"先打给谁"类推荐面隐去（推荐/拨名单/补数清单/情报雷达）。
NON_BUYER_INDUSTRIES = frozenset({"research_edu", "medical_health", "lab_instrument"})
# 2026-11-15 用户拍板："分析/实验室不需要"——检测/实验室（含民营第三方）
# 整体出局；"造泵装机器不需要"——OEM 设备商出局（NON_CUSTOMER_ROLES）；
# "液环泵不需要"——电力/造纸/烟草行业随之出局（SEGMENTATION.md v3.1）。

# 政采买家源（2026-11-14 用户拍板："我们不做政采买家"）——采购人建档
# （ggzy_buyer/ccgp_buyer/sdgp_buyer）=机关/事业单位/学校/医院主体，整体
# 出局。处置与学校同款：数据保留（竞对雷达"卖给了谁"的证据主体+装机地图
# 素材），退出全部客户工作面（推荐/拨名单/补数清单/联系人中心/情报雷达）。
NON_CUSTOMER_SOURCES = frozenset({"ggzy_buyer", "ccgp_buyer", "sdgp_buyer"})

# OEM 设备商（买泵装进自家设备的制造商）出局——2026-11-15 用户拍板
# "造泵装机器不需要"。reseller/integrator（渠道合作面）暂不动。
NON_CUSTOMER_ROLES = frozenset({"oem"})

# 名称形态兜底词：行业字段未分类但名称是公共机构形态（不带"公司"才生效，
# 民营企业名带"公司"一律保留）。词表校准闭环第五轮补充：检验所/质检所/
# 计量所类 ccgp_buyer 建档时行业未打中（"检验所"不在 lab_instrument 词表）。
PUB_INSTITUTION_NAME_WORDS = (
    "检验所", "检测中心", "检验中心", "计量所", "监督检验",
    "研究院", "研究所", "中学", "大学", "学院",
)


def is_non_buyer(
    industry_key: str | None,
    name: str | None = None,
    source: str | None = None,
    customer_role: str | None = None,
) -> bool:
    """行业级+名称级+来源级+角色级非客户判定（推荐面排除用，非删除）。

    source（2026-11-14）：政采买家建档源出局；customer_role（2026-11-15）：
    OEM 设备商出局——CUSTOMER_PROFILE.md 拍板沿革。"""
    if source and source in NON_CUSTOMER_SOURCES:
        return True
    if customer_role and customer_role in NON_CUSTOMER_ROLES:
        return True
    if industry_key in NON_BUYER_INDUSTRIES:
        return True
    if name and "公司" not in name:
        if any(w in name for w in PUB_INSTITUTION_NAME_WORDS):
            return True
    return False


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
