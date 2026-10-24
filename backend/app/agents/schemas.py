"""P2 LLM 后端的输出 Schema（对标 OpenOutreach QualificationDecision）。

LLM_PROMPTS.md 是提示词契约；本模块是结构化输出的强制层——
"schema enforcement over prose guidance"：受控字段用 Literal 拒绝非法值，
而不是靠 prompt 提醒。Pydantic 校验失败即重试/回退规则后端。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class QualificationDecision(BaseModel):
    """icp_judge LLM 后端输出：是否值得进入候选名单 + 一句理由。"""

    qualified: bool
    reason: str = Field(max_length=60)  # 一句中文 ≤40 字（留余量）
    confidence: Literal["high", "medium", "low"] = "medium"

    @field_validator("reason")
    @classmethod
    def _reason_not_cliche(cls, v: str) -> str:
        banned = ("这家公司", "非常匹配", "强烈推荐", "strong fit", "this prospect")
        if any(b in v.lower() for b in banned):
            raise ValueError("reason 禁用套话开头，需说具体事实")
        return v.strip()


class ParsedSignal(BaseModel):
    """signal_parser LLM 后端输出：一条公告 → 结构化意图信号。"""

    enterprise_name: str = ""
    matched_terms: list[str] = Field(default_factory=list, max_length=10)
    hit_part: Literal["title", "body"] = "title"
    evidence_claim: str = Field(default="", max_length=80)  # 一句中文 ≤50 字
    base_score: float = 0.0
    project_stage: Literal["inquiry", "tender", "award", "unknown"] = "unknown"

    @field_validator("matched_terms")
    @classmethod
    def _terms_in_vocab(cls, v: list[str]) -> list[str]:
        """matched_terms 只能从设备词表选择（契约层兜底，调用方再校验一遍）。"""
        if not v:
            return v
        from ..vertical import device_strong_terms, device_weak_terms

        vocab = set(device_strong_terms()) | set(device_weak_terms())
        kept = [t for t in v if t in vocab]
        if len(kept) != len(v):
            raise ValueError("matched_terms 含词表外词汇，禁止发明新词")
        return kept


# ---- 稳态错误词汇表（学 OpenOutreach ErrorType：可增不可改） ----

class ErrorType:
    """SSE 事件与 API 错误的稳定 type 值。前端/Agent 按 type 分支，人读 message。"""

    NO_CREDENTIAL = "no_credential"          # 缺 Key（如高德/知了）
    PROVIDER_AUTH = "provider_auth"          # 第三方鉴权失败
    PROVIDER_QUOTA = "provider_quota"        # 第三方配额耗尽
    BAD_CONFIG = "bad_config"                # 配置非法
    GOAL_UNREACHED = "goal_unreached"        # 目标未达成（部分结果有效）
    CANCELLED = "cancelled"                  # 用户取消
    INTERNAL = "internal"                    # 未归类内部错误


HALTING_ERROR_TYPES = {ErrorType.NO_CREDENTIAL, ErrorType.PROVIDER_AUTH, ErrorType.BAD_CONFIG}
"""这些错误终止任务并保留已产出结果；其余可重试。"""
