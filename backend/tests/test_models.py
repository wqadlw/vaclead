"""数据模型测试：建表 / LEAD_TRANSITIONS / reasons helpers / Signal 唯一约束。

内存 SQLite 自建 engine，不依赖全局 ENGINE（docs/SPEC.md §1、§7）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint, create_engine, inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    LEAD_LOST_REASONS,
    LEAD_TRANSITIONS,
    Base,
    Enterprise,
    Signal,
)

EXPECTED_TABLES = {
    "user",
    "enterprise",
    "enterprise_alias",
    "signal",
    "lead",
    "followup",
    "subscription",
    "agent_run",
    "task_checkpoint",
    "settings_kv",
}


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


# ---------------- 建表 ----------------

def test_create_all_tables(engine):
    names = set(sa_inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= names, f"缺少表: {EXPECTED_TABLES - names}"


def test_signal_dedup_unique_constraint_columns(engine):
    uq_cols = {
        tuple(col.name for col in constr.columns)
        for constr in Base.metadata.tables["signal"].constraints
        if isinstance(constr, UniqueConstraint)
    }
    # SPEC-I I1：title 入约束（与全库查重口径一致），空 url 不同项目信号须共存
    assert ("url", "enterprise_id", "type", "title") in uq_cols


# ---------------- LEAD_TRANSITIONS 常量 ----------------

def test_lead_transitions_contract():
    assert LEAD_TRANSITIONS == {
        "new": {"following", "lost"},
        "following": {"intent", "lost", "pool"},
        "intent": {"won", "lost", "pool", "following"},
        "won": set(),
        "lost": set(),
        "pool": {"following"},
    }


def test_lead_transitions_statuses_self_consistent():
    # 任何迁移目标都必须是状态机中已有的状态
    all_status = set(LEAD_TRANSITIONS)
    for src, targets in LEAD_TRANSITIONS.items():
        assert targets <= all_status, f"{src} 指向未知状态: {targets - all_status}"
    # 终态无出边
    assert LEAD_TRANSITIONS["won"] == set()
    assert LEAD_TRANSITIONS["lost"] == set()


def test_lead_lost_reasons_contract():
    assert LEAD_LOST_REASONS == {
        "no_need",
        "competitor",
        "unreachable",
        "budget",
        "other",
    }


# ---------------- Enterprise reasons helpers ----------------

def test_enterprise_reasons_roundtrip():
    ent = Enterprise(name="理由测试企业")
    ent.set_reasons([{"rule": "行业命中:半导体(tier1)", "points": 40}])
    assert ent.reasons_list() == [{"rule": "行业命中:半导体(tier1)", "points": 40}]
    # JSON 存储为数组文本
    assert ent.icp_reasons.startswith("[")


def test_enterprise_reasons_default_and_invalid():
    ent = Enterprise(name="默认理由企业")
    assert ent.reasons_list() == []  # 默认 "[]"
    ent.icp_reasons = None
    assert ent.reasons_list() == []
    ent.icp_reasons = "not-a-json"
    assert ent.reasons_list() == []  # 非法 JSON 容错为空


def test_enterprise_defaults_persisted(session):
    ent = Enterprise(name="默认值企业", source="poi")
    session.add(ent)
    session.commit()
    loaded = session.query(Enterprise).filter_by(name="默认值企业").one()
    assert loaded.icp_score == 0.0
    assert loaded.intent_score == 0.0
    assert loaded.is_competitor == 0
    assert loaded.status == "active"
    assert loaded.tenant_id == 1
    assert loaded.icp_reasons == "[]"


# ---------------- Signal 唯一约束 ----------------

def test_signal_unique_constraint_blocks_duplicate(session):
    ent = Enterprise(name="信号去重企业")
    session.add(ent)
    session.commit()

    s1 = Signal(
        enterprise_id=ent.id,
        type="tender",
        title="真空泵采购公告",
        url="http://tender.example/1",
        pub_date="2026-10-01",
        base_score=70.0,
    )
    session.add(s1)
    session.commit()

    # 真·同键四元组 (url, enterprise_id, type, title) 再插 → 违反 uq_signal_dedup
    # （SPEC-I I1 约束已含 title：仅同 url 不同 title 须共存，见 test_spec_i）
    s2 = Signal(
        enterprise_id=ent.id,
        type="tender",
        title="真空泵采购公告",
        url="http://tender.example/1",
        base_score=70.0,
    )
    session.add(s2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_signal_unique_constraint_allows_different_type_or_url(session):
    ent = Enterprise(name="信号并存企业")
    session.add(ent)
    session.commit()

    session.add(
        Signal(enterprise_id=ent.id, type="tender", title="a", url="http://x/1")
    )
    # 同 url 不同 type → 允许（eia 预留类型）
    session.add(
        Signal(enterprise_id=ent.id, type="eia", title="b", url="http://x/1")
    )
    # 同 type 不同 url → 允许
    session.add(
        Signal(enterprise_id=ent.id, type="tender", title="c", url="http://x/2")
    )
    session.commit()
    assert session.query(Signal).count() == 3


def test_signal_terms_helpers():
    sig = Signal(title="t", url="u")
    assert sig.terms_list() == []
    sig.set_terms(["真空泵", "分子泵"])
    assert sig.terms_list() == ["真空泵", "分子泵"]
    sig.matched_terms = "{bad json"
    assert sig.terms_list() == []
