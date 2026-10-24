"""归一引擎测试 — 内存 SQLite 自建 session，不依赖全局 ENGINE（docs/SPEC.md §3.2）。"""
from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Enterprise, EnterpriseAlias
from app.normalize import (
    FUZZY_THRESHOLD,
    canonical_name,
    entity_resolver_agent,
    resolve_enterprise,
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


# ---------------- canonical_name ----------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 半角括号后缀：取括号前主体
        ("沃尔真空科技(苏州)有限公司", "沃尔真空科技"),
        # 全角括号后缀
        ("沃尔真空科技（苏州）有限公司", "沃尔真空科技"),
        # 括号在前：括号内地区同样被剔除
        ("（苏州）芯达电子科技有限公司", "芯达电子科技"),
        # 股份有限公司后缀
        ("华芯半导体股份有限公司", "华芯半导体"),
        # 后缀叠加剥离：集团 + 有限责任公司
        ("蓝湖集团有限责任公司", "蓝湖"),
        # 单独"公司"后缀
        ("某某检测公司", "某某检测"),
        # 无后缀无括号，原样返回
        ("真空设备研究所", "真空设备研究所"),
        # 空白清洗
        ("  芯创 半导体 有限公司 ", "芯创半导体"),
        # 空串
        ("", ""),
        ("   ", ""),
    ],
)
def test_canonical_name(raw, expected):
    assert canonical_name(raw) == expected


# ---------------- resolve_enterprise ----------------

def test_resolve_new_enterprise_created_with_full_name(session):
    ent, is_new = resolve_enterprise(session, "东莞贝尔真空设备有限公司", source="csv")
    assert is_new is True
    assert ent.id is not None
    assert ent.name == "东莞贝尔真空设备有限公司"  # 原全名
    assert ent.source == "csv"


def test_resolve_exact_same_name_merges(session):
    ent1, is_new1 = resolve_enterprise(session, "苏州芯创半导体有限公司")
    assert is_new1 is True
    ent2, is_new2 = resolve_enterprise(session, "苏州芯创半导体有限公司")
    assert is_new2 is False
    assert ent2.id == ent1.id
    assert session.query(Enterprise).count() == 1


def test_resolve_alias_hit(session):
    # 通过 resolve 新建时登记了标准化主体名别名 → 短名直接命中别名表
    ent1, _ = resolve_enterprise(session, "苏州芯创半导体有限公司")
    ent2, is_new = resolve_enterprise(session, "苏州芯创半导体")
    assert is_new is False
    assert ent2.id == ent1.id
    assert session.query(Enterprise).count() == 1


def test_resolve_manual_alias_hit(session):
    # 手工维护的别名也指向主企业
    ent = Enterprise(name="华东精密仪器有限公司", source="manual")
    session.add(ent)
    session.flush()
    session.add(EnterpriseAlias(enterprise_id=ent.id, alias="华东精密", source="manual"))
    session.flush()

    hit, is_new = resolve_enterprise(session, "华东精密")
    assert is_new is False
    assert hit.id == ent.id
    assert session.query(Enterprise).count() == 1


def test_resolve_fuzzy_merge_suffix_variant(session):
    # 库内直接插入（无别名记录）→ 迫使解析走 rapidfuzz 分支
    existing = Enterprise(name="XX半导体有限公司", source="manual")
    session.add(existing)
    session.flush()

    ent, is_new = resolve_enterprise(session, "XX半导体股份有限公司")
    assert is_new is False
    assert ent.id == existing.id
    assert session.query(Enterprise).count() == 1
    # 合并时变体写入别名表
    aliases = {
        a.alias
        for a in session.query(EnterpriseAlias).filter_by(enterprise_id=existing.id)
    }
    assert "XX半导体股份有限公司" in aliases


def test_resolve_fuzzy_threshold_not_crossed_by_different_name(session):
    _, _ = resolve_enterprise(session, "苏州芯创半导体有限公司")
    ent2, is_new = resolve_enterprise(session, "杭州宏远电子科技有限公司")
    assert is_new is True
    assert session.query(Enterprise).count() == 2
    # 两个主体名相似度远低于阈值
    from rapidfuzz import fuzz

    score = fuzz.token_sort_ratio(
        canonical_name("苏州芯创半导体有限公司"),
        canonical_name("杭州宏远电子科技有限公司"),
    )
    assert score < FUZZY_THRESHOLD


def test_resolve_bracket_variant_merges(session):
    ent1, _ = resolve_enterprise(session, "南通光启真空(南通)有限公司")
    # 括号地区后缀变体 → 主体名一致 → 合并
    ent2, is_new = resolve_enterprise(session, "南通光启真空有限公司")
    assert is_new is False
    assert ent2.id == ent1.id


def test_resolve_empty_name_raises(session):
    with pytest.raises(ValueError):
        resolve_enterprise(session, "   ")


# ---------------- entity_resolver_agent ----------------

def test_agent_resolver_new_then_hit(session):
    r1 = entity_resolver_agent({"name": "苏州芯创半导体有限公司"}, session)
    assert set(r1) == {"enterprise_id", "is_new", "confidence"}
    assert r1["is_new"] is True
    assert r1["confidence"] == 0.0

    r2 = entity_resolver_agent({"name": "苏州芯创半导体有限公司"}, session)
    assert r2["is_new"] is False
    assert r2["confidence"] == 1.0
    assert r2["enterprise_id"] == r1["enterprise_id"]


def test_agent_resolver_fuzzy_confidence(session):
    existing = Enterprise(name="XX半导体有限公司", source="manual")
    session.add(existing)
    session.flush()
    r = entity_resolver_agent({"name": "XX半导体股份有限公司"}, session)
    assert r["is_new"] is False
    assert r["enterprise_id"] == existing.id
    assert FUZZY_THRESHOLD / 100 <= r["confidence"] <= 1.0


def test_agent_record_run_called_when_available(session, monkeypatch):
    """agents.runs 存在时 record_run 被调用；不存在时静默跳过（不抛错）。"""
    calls: list[dict] = []
    fake = types.ModuleType("app.agents.runs")

    def record_run(**kwargs):
        calls.append(kwargs)

    fake.record_run = record_run
    monkeypatch.setitem(sys.modules, "app.agents.runs", fake)

    r = entity_resolver_agent({"name": "无锡海力真空设备有限公司"}, session)
    assert r["is_new"] is True
    assert calls, "record_run 应被调用"


def test_agent_record_run_missing_module_is_silent(session):
    # app.agents.runs 尚未实现时不应影响解析结果
    r = entity_resolver_agent({"name": "无锡海力真空设备有限公司", "source": "tender"}, session)
    assert r["is_new"] is True
    ent = session.get(Enterprise, r["enterprise_id"])
    assert ent.source == "tender"
