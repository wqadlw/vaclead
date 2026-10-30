"""SPEC-L 回归：工业领域垂直扩展的词表合约。

词表是全平台评分/采集/打标的地基——本文件锁定其规模、兼容性与互斥性。
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-l-"), "t.db")
)

from app import vertical


def test_strong_terms_scale_and_vacuum_preserved():
    """强词规模 ≥120 且 v1 真空 34 词全部保留（存量数据/规则不断档）。"""
    strong = set(vertical.device_strong_terms())
    assert len(strong) >= 120, f"强词规模不足：{len(strong)}"
    vacuum_v1 = {
        "真空泵", "真空机组", "罗茨泵", "分子泵", "旋片泵", "螺杆真空泵",
        "爪式真空泵", "涡旋真空泵", "扩散泵", "真空镀膜机", "真空镀膜设备",
        "磁控溅射", "蒸发镀膜", "PVD镀膜", "PVD设备", "CVD设备", "冻干机",
        "真空冷冻干燥机", "冷冻干燥设备", "真空干燥箱", "真空干燥设备",
        "真空烧结炉", "真空淬火炉", "真空退火炉", "真空热处理炉",
        "真空热处理设备", "真空腔体", "真空检漏", "真空包装机", "真空获得设备",
        "真空阀门", "超高真空", "高真空设备", "真空系统",
    }
    missing = vacuum_v1 - strong
    assert not missing, f"v1 真空词丢失：{missing}"


def test_industries_count_and_mutex():
    """类目 ≥18 且关键词全局互斥（一个企业名只归一个类目）。"""
    cats = vertical.downstream_industries()
    assert len(cats) >= 18, f"类目数不足：{len(cats)}"
    keys = [c["key"] for c in cats]
    assert len(keys) == len(set(keys)), "类目 key 重复"
    seen: dict[str, str] = {}
    for c in cats:
        for kw in c["keywords"]:
            assert kw not in seen, f"关键词「{kw}」同时属于 {seen.get(kw)} 与 {c['key']}"
            seen[kw] = c["key"]


def test_ggzy_terms_tiered():
    """宽词分级：core ≤22（每日增量成本受控）且含真空词（存量增量不断档）；
    extended 非空供全量扫描。"""
    core = vertical.ggzy_name_terms()
    extended = vertical.ggzy_extended_terms()
    assert 1 <= len(core) <= 22, f"core 宽词数失控：{len(core)}"
    assert "真空" in core, "真空宽词必须留在 core（存量增量池不断档）"
    assert len(extended) >= 20
    assert not (set(core) & set(extended)), "core/extended 宽词不应重叠"


def test_new_industries_detected():
    """新类目企业名识别回归（买家侧关键词）。"""
    cases = {
        "东莞市XX塑胶科技有限公司": "plastic_rubber",
        "华测检测技术股份有限公司": "lab_instrument",
        "苏州市立医院": "medical_health",
        "XX环境治理有限公司": "environmental",
        "XX乳业股份有限公司": "freeze_dried",
        "上海XX机电安装工程有限公司": "hvac_building",
        "苏州XX精密机械制造有限公司": "machining",
        "北京药物治疗研究中心": "pharma_biotech",  # 不含"研究院/研究所"，落到制药类目
    }
    for name, expected in cases.items():
        r = vertical.detect_industry(name)
        got = r["key"] if r else None
        assert got == expected, f"{name}: 期望 {expected}，实际 {got}"


def test_signal_parser_still_scores_vacuum_terms():
    """词表切换后信号引擎对真空词仍正常打分（存量链路回归，同 test_agents 范式）。"""
    from app.agents import registry  # noqa: PLC0415

    agent = registry.get_agent("signal_parser", backend="rule")
    result = agent.run_logged({"title": "采购真空泵机组项目公告", "content": "常规内容"})
    signals = result.get("signals", [])
    assert signals and "真空泵" in signals[0]["matched_terms"]
