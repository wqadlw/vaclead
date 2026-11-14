"""Source 注册表（SPEC-MULTI S1，keelead SourceManager 模式）。

唯一权威登记处：企业库 Enterprise.source 的全部取值（含同源变体）都在
SOURCES 注册——标签/分类/SLA/通道归并全部由注册表派生，新增一个来源
只改这里，不再散布 12 处枚举点。

约定：
- key = 企业库实际落库的主 source 值（"主 source 值"原则）；
- aliases = 同一采集通道的其他落库值（如 ggzy_buyer），筛选/归并时折回主 key；
- label_cn 沿用各消费方现网文案（信息中心来源矩阵输出逐字节一致红线）；
- sla_job 与 quality.py 的新鲜度仪表盘 job_name 对齐（补齐 proposal_gd/hb/js
  三省漏登记——此前永远不上仪表盘）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDef:
    """单个数据源定义（不可变，注册即冻结）。"""

    key: str                      # 主 source 值，如 "ggzy"
    label_cn: str                 # 信息中心/徽章中文，如 "公共资源交易"
    category: str                 # tender | proposal | poi | import | enrich
    aliases: tuple[str, ...] = ()     # 同源变体（企业库实际落库的关联 source 值）
    sla_job: str | None = None        # freshness 仪表盘登记的 job_name
    sla_label: str | None = None      # 仪表盘展示名（缺省用 label_cn）
    sla_hours: float | None = None    # 预期间隔上限（小时）
    cron_desc: str = ""               # 定时巡采说明（设置页展示）


#: 唯一注册表：所有来源必须在此登记（顺序即展示顺序的次序键，ggzy 在前
#: 保证 hourly_tender 的 SLA 文案取自 ggzy 的登记）。
SOURCES: dict[str, SourceDef] = {
    d.key: d
    for d in (
        SourceDef("ggzy", "公共资源交易", "tender", aliases=("ggzy_buyer",),
                  sla_job="hourly_tender", sla_label="政采抓取", sla_hours=2),
        SourceDef("ccgp", "政采网公告", "tender", aliases=("ccgp_buyer",),
                  sla_job="hourly_tender", sla_hours=2),
        SourceDef("tender", "招标导入", "tender",
                  sla_job="hourly_tender", sla_hours=2),
        SourceDef("poi", "高德 POI 采集", "poi"),
        SourceDef("proposal_gd", "广东备案", "proposal",
                  sla_job="proposal_gd", sla_label="广东备案采集", sla_hours=26),
        SourceDef("proposal_hb", "湖北备案", "proposal",
                  sla_job="proposal_hb", sla_label="湖北备案采集", sla_hours=26),
        SourceDef("proposal_js", "江苏备案", "proposal",
                  sla_job="proposal_js", sla_label="江苏备案采集", sla_hours=26),
        SourceDef("proposal_zj", "浙江备案", "proposal",
                  sla_job="proposal_zj", sla_label="浙江备案采集", sla_hours=26),
        SourceDef("csv", "手工导入", "import"),
        SourceDef("gongshang", "工商补数", "enrich",
                  sla_job="enrich_pipeline", sla_label="工商补数", sla_hours=24 * 7),
        SourceDef("manual", "手工录入", "import"),
        SourceDef("riskbird", "风鸟搬运", "import"),
    )
}

#: 同源变体在数据矩阵/批次台账里的独立展示名（沿用原 SOURCE_CN 文案；
#: 未登记的别名回退所属主 key 的 label_cn）。
ALIAS_LABELS: dict[str, str] = {
    "ggzy_buyer": "公采买家",
    "ccgp_buyer": "政采买家",
}


def source_label(key: str) -> str:
    """source 值 → 中文标签；未注册回退 key 本身（现状行为）。"""
    d = SOURCES.get(key)
    if d is not None:
        return d.label_cn
    if key in ALIAS_LABELS:
        return ALIAS_LABELS[key]
    return key


def channel_variants(key: str) -> tuple[str, ...]:
    """通道 → 归并的全部 source 值（主 key 在前；别名反查同通道）。

    enterprises 筛选（source=ggzy 命中 ggzy+ggzy_buyer）与分析归并
    （ggzy_buyer → 主 key）共用同一口径。
    """
    d = SOURCES.get(key)
    if d is not None:
        return (d.key, *d.aliases)
    for d in SOURCES.values():
        if key in d.aliases:
            return (d.key, *d.aliases)
    return (key,)


def channel_badge(key: str) -> str:
    """拟建雷达四通道徽章（SPEC-BR/BS）：proposal_<prov> 精确派生 prov，
    未匹配回退 key 前 2 字母或 key 本身（废除旧 js 兜底）。"""
    if key.startswith("proposal_"):
        return key[len("proposal_"):]
    return key[:2] or key


def source_disabled_keys(session) -> set[str]:
    """SettingsKV source_disabled（JSON array）→ 停用集合（缺行=全启用）。"""
    import json as _json

    from .models import SettingsKV

    row = session.get(SettingsKV, "source_disabled")
    if row is None or not row.value:
        return set()
    try:
        arr = _json.loads(row.value)
    except ValueError:
        return set()
    return {str(k) for k in arr} if isinstance(arr, list) else set()


def is_source_disabled(key: str, session) -> bool:
    """key 或其所属通道任一成员被停用即 True（停用主 key 连同变体一起停）。"""
    disabled = source_disabled_keys(session)
    if not disabled:
        return False
    return any(v in disabled for v in channel_variants(key))


def any_source_disabled(key: str) -> bool:
    """sweep 函数入口便捷判断：自开短会话查停用态（SQLite 单写者友好）。"""
    from .db import SessionLocal

    with SessionLocal() as s:
        return is_source_disabled(key, s)
