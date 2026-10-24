"""vaclead 数据模型 — 全部表定义（契约文件，遵循 docs/SPEC.md §1）。

所有业务表含 tenant_id 预留多租户。时间戳统一存 UTC ISO 字符串。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    REAL,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="sales")  # admin/sales
    display_name: Mapped[str] = mapped_column(Text, default="")
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Enterprise(Base):
    __tablename__ = "enterprise"
    __table_args__ = (
        Index("ix_enterprise_industry", "industry_key"),
        Index("ix_enterprise_region", "province", "city", "district"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    uscc: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_batch_id: Mapped[str | None] = mapped_column(Text)  # 导入/采集批次追溯
    region_code: Mapped[str | None] = mapped_column(Text)
    province: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    industry_key: Mapped[str | None] = mapped_column(Text)
    industry_name: Mapped[str | None] = mapped_column(Text)
    business_scope: Mapped[str | None] = mapped_column(Text)
    registered_capital_wan: Mapped[float | None] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    lat: Mapped[float | None] = mapped_column(REAL)
    lng: Mapped[float | None] = mapped_column(REAL)
    icp_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    icp_reasons: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    intent_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    is_competitor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    poi_id: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)

    # ---- helpers ----
    def reasons_list(self) -> list[dict]:
        try:
            return json.loads(self.icp_reasons or "[]")
        except json.JSONDecodeError:
            return []

    def set_reasons(self, reasons: list[dict]) -> None:
        self.icp_reasons = json.dumps(reasons, ensure_ascii=False)


class EnterpriseAlias(Base):
    __tablename__ = "enterprise_alias"
    __table_args__ = (UniqueConstraint("enterprise_id", "alias", name="uq_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="auto")


class Signal(Base):
    __tablename__ = "signal"
    __table_args__ = (
        Index("ix_signal_enterprise", "enterprise_id"),
        Index("ix_signal_pubdate", "pub_date"),
        UniqueConstraint("url", "enterprise_id", "type", name="uq_signal_dedup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False, default="tender")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pub_date: Mapped[str | None] = mapped_column(Text)
    matched_terms: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    hit_part: Mapped[str] = mapped_column(Text, nullable=False, default="body")  # title/body
    raw_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)

    def terms_list(self) -> list[str]:
        try:
            return json.loads(self.matched_terms or "[]")
        except json.JSONDecodeError:
            return []

    def set_terms(self, terms: list[str]) -> None:
        self.matched_terms = json.dumps(terms, ensure_ascii=False)


class Lead(Base):
    __tablename__ = "lead"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "tenant_id", name="uq_lead_enterprise"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    # new -> following -> intent -> won|lost ; following/intent --回收--> pool
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    grade: Mapped[str | None] = mapped_column(Text)  # S/A/B
    lost_reason: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[str | None] = mapped_column(Text)
    last_followed_at: Mapped[str | None] = mapped_column(Text)
    # 跟进冗余（借鉴 WukongCRM CrmLeads）：列表页直接可读，不必 join followup
    next_action: Mapped[str | None] = mapped_column(Text)
    next_time: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


# 合法状态迁移表（API 层校验用）
LEAD_TRANSITIONS: dict[str, set[str]] = {
    "new": {"following", "lost"},
    "following": {"intent", "lost", "pool"},
    "intent": {"won", "lost", "pool", "following"},
    "won": set(),
    "lost": set(),
    "pool": {"following"},
}
LEAD_LOST_REASONS = {"no_need", "competitor", "unreachable", "budget", "other"}


class Followup(Base):
    __tablename__ = "followup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    next_time: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Subscription(Base):
    __tablename__ = "subscription"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "user_id", name="uq_subscription"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (Index("ix_agent_run_name", "agent_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    backend: Mapped[str] = mapped_column(Text, nullable=False, default="rule")
    input_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    decision_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class TaskCheckpoint(Base):
    __tablename__ = "task_checkpoint"

    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # poi / tender
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class SettingsKV(Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PoolSetting(Base):
    """公海规则（借鉴 WukongCRM CrmCustomerPool 的规则对象化）。"""

    __tablename__ = "pool_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="默认公海")
    recycle_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)  # 超期未跟进回收
    receive_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 每人持有上限，0=不限
    remind_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # 回收前提醒天数
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
