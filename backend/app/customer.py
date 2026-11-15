"""客户画像公共层（SPEC-SEG2）：非客户排除 SQL + 客户群映射。

- non_buyer_conds()：contacts._non_buyer_conds 抽取的公共形态——contacts
  拨打工作列表与 segments 统计/名单两处复用，防口径漂移（多轮用户拍板
  沿革见函数 docstring；Python 对位形态是 vertical.is_non_buyer）。
- industry_segment_map()：industry_key → 命中的 enabled 客户群（多群命中
  取最高 priority），recommend 加权与 imports 补数清单排序共用。
"""
from __future__ import annotations

import json

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import CustomerSegment, Enterprise
from .vertical import PUB_INSTITUTION_NAME_WORDS

# SPEC-SEG2 §4：推荐/补数优先级——P0 群企业 +5 分，P1 +2，P2 +0
PRIORITY_WEIGHT = {"P0": 5, "P1": 2, "P2": 0}
# 多群命中取最高 priority：P0 > P1 > P2（数值小者优先）；无群命中垫底用 99
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def non_buyer_conds():
    """非客户排除（多轮用户拍板沿革：学校→公共机构→政采买家→检测实验室
    整体→OEM 设备商）。

    行业级：research_edu / medical_health / lab_instrument（2026-11-15 整体
    出局，不再区分民营）；来源级：*_buyer 政采买家建档源；角色级：oem；
    名称形态兜底（检验所/质检所/计量所/研究院等未分类公共机构，不带"公司"
    才生效）。数据保留可查，仅隐出拨打工作列表/画像统计/名单与导出。
    与 vertical.is_non_buyer 同语义的 SQL 形态（or_/!= 链，规避扫描器锚定）。

    SPEC-SEG2：自 contacts._non_buyer_conds 抽取为公共函数——contacts 与
    segments 统计必须逐字节同口径，防两处手抄漂移。
    """
    return and_(
        or_(
            Enterprise.source.is_(None),
            ~Enterprise.source.contains("_buyer"),
        ),
        or_(
            Enterprise.industry_key.is_(None),
            Enterprise.industry_key != "research_edu",
            Enterprise.industry_key != "medical_health",
            Enterprise.industry_key != "lab_instrument",
        ),
        or_(
            Enterprise.customer_role.is_(None),
            Enterprise.customer_role != "oem",
        ),
        or_(
            Enterprise.name.is_(None),
            Enterprise.name.like("%公司%"),
            and_(*[~Enterprise.name.contains(w) for w in PUB_INSTITUTION_NAME_WORDS]),
        ),
    )


def parse_json_list(raw: str | None) -> list[str]:
    """TEXT JSON 列 → 字符串数组（损坏容错：非数组/坏 JSON 回空表）。"""
    if not raw:
        return []
    try:
        arr = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
        return []
    return [str(x) for x in arr if str(x).strip()]


def segment_keys_of(seg: CustomerSegment) -> list[str]:
    """客户群的 industry_keys 去重保序（名单归属集合）。"""
    return list(dict.fromkeys(parse_json_list(seg.industry_keys if seg else None)))


def industry_segment_map(session: Session) -> dict[str, tuple[str, str]]:
    """industry_key → (客户群名, priority)，仅 enabled 群参与（停用退出推荐）。

    一个行业键命中多群时取最高 priority（P0 > P1 > P2，同优先级取 sort/id
    靠前者）。recommend 打分加权与 imports 补数清单排序共用此映射。
    """
    mapping: dict[str, tuple[str, str]] = {}
    rows = (
        session.query(CustomerSegment)
        .filter(CustomerSegment.enabled == 1)
        .order_by(CustomerSegment.sort.asc(), CustomerSegment.id.asc())
        .all()
    )
    for seg in rows:
        prio = seg.priority if seg.priority in PRIORITY_ORDER else "P2"
        cur = PRIORITY_ORDER[prio]
        for key in segment_keys_of(seg):
            hit = mapping.get(key)
            if hit is None or cur < PRIORITY_ORDER.get(hit[1], 99):
                mapping[key] = (seg.name, prio)
    return mapping
