"""P2 LLM schema 契约测试（schemas.py）：受控枚举/理由禁套话/词表校验/错误词表。"""
from __future__ import annotations

import os
import tempfile

import pytest
from pydantic import ValidationError

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-schema-"), "t.db")
)

from app.agents.schemas import (  # noqa: E402
    ErrorType,
    HALTING_ERROR_TYPES,
    ParsedSignal,
    QualificationDecision,
)


def test_qualification_accepts_valid():
    d = QualificationDecision(qualified=True, reason="经营范围含晶圆制造，需配套真空泵", confidence="high")
    assert d.qualified and d.confidence == "high"


def test_qualification_rejects_cliche_reason():
    with pytest.raises(ValidationError):
        QualificationDecision(qualified=True, reason="这家公司非常匹配", confidence="low")


def test_qualification_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        QualificationDecision(qualified=False, reason="经营范围为贸易", confidence="very_high")


def test_parsed_signal_terms_must_be_in_vocab():
    ok = ParsedSignal(
        enterprise_name="苏州某半导体", matched_terms=["真空泵"], hit_part="title",
        evidence_claim="公告要求采购真空泵2台", base_score=70, project_stage="tender",
    )
    assert ok.matched_terms == ["真空泵"]
    with pytest.raises(ValidationError):
        ParsedSignal(matched_terms=["自创词"], evidence_claim="x")


def test_parsed_signal_stage_enum():
    with pytest.raises(ValidationError):
        ParsedSignal(matched_terms=["冻干机"], project_stage="unknown2")


def test_error_types_stable():
    """稳态词表：可增不可改——断言现有值不被悄悄改名。"""
    assert ErrorType.NO_CREDENTIAL == "no_credential"
    assert ErrorType.PROVIDER_QUOTA == "provider_quota"
    assert ErrorType.CANCELLED == "cancelled"
    assert ErrorType.NO_CREDENTIAL in HALTING_ERROR_TYPES
    assert ErrorType.PROVIDER_QUOTA not in HALTING_ERROR_TYPES