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
    # 证据位（学 AI_Find_Customer evidence：claim+source_url 配对），P2 联系人模块的展示基础
    evidence_claim: Mapped[str | None] = mapped_column(Text)  # "该公告要求采购真空镀膜机2套"
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
    # 商机三要素（吸收 twenty opportunity：amount/closeDate/probability + stage）
    amount_wan: Mapped[float | None] = mapped_column(REAL)  # 商机金额（万元）
    probability: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 赢率 0-100
    expected_close: Mapped[str | None] = mapped_column(Text)  # 预计成交日 YYYY-MM-DD
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
# 阶段默认赢率（twenty pipeline stage 模式：阶段推进即更新赢率）
LEAD_STAGE_PROBABILITY: dict[str, int] = {
    "new": 10, "following": 40, "intent": 70, "won": 100, "lost": 0, "pool": 20,
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


class Notification(Base):
    """站内通知（SPEC-C F1）：订阅企业产生新信号时触达关注者，幂等可重扫。"""

    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("ref_type", "ref_id", "user_id", name="uq_notification_dedup"),
        Index("ix_notification_user", "user_id", "read"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="signal")  # signal
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ref_type: Mapped[str] = mapped_column(Text, nullable=False, default="signal")
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False)
    read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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


class LLMCall(Base):
    """LLM 调用明细（学自 AI_Find_Customer cost_tracker，改为逐条落库可聚合）。

    P2 接入 GLM 后由唯一 LLM client 埋点；聚合靠 SQL GROUP BY（按任务/Agent/天）。
    """

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_task", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(Text)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    backend: Mapped[str] = mapped_column(Text, nullable=False, default="llm")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ReviewQueue(Base):
    """归一复核队列（SPEC-B §4）：疑似重复企业等待人工 merge/reject。

    MDM match/survivorship/stewardship 模式中的 stewardship 环节——
    80-89 分的模糊命中不做静默合并，宁多一条复核不误并一档。
    """

    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("candidate_id", "target_id", name="uq_review_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # candidate 可为空：merge 后候选企业被删，复核记录保留（candidate_id 置 NULL）作审计痕迹
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise.id", ondelete="SET NULL"), nullable=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="fuzzy_name")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EnterpriseStat(Base):
    """企业中标画像（SPEC-B §5）：全部由库内 tender_win 信号聚合，可溯源。"""

    __tablename__ = "enterprise_stat"

    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), primary_key=True
    )
    bid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bid_amount_total_wan: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    bid_amount_max_wan: Mapped[float | None] = mapped_column(REAL)
    last_win_date: Mapped[str | None] = mapped_column(Text)
    top_buyer: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class SyncWatermark(Base):
    """定时任务水位（SPEC-B §6）：每次调度的最后运行时间/状态/统计，失败不静默。"""

    __tablename__ = "sync_watermark"

    job_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_run_at: Mapped[str | None] = mapped_column(Text)
    last_status: Mapped[str | None] = mapped_column(Text)  # ok | failed | partial
    stats_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class Contact(Base):
    """关键联系人（SPEC-D）：法人/采购联系人/代理机构，全部带证据溯源，不发明数据。"""

    __tablename__ = "contact"
    __table_args__ = (
        Index("ix_contact_enterprise", "enterprise_id"),
        UniqueConstraint("enterprise_id", "name", "role", name="uq_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="legal_rep")
    # legal_rep 法定代表人 | buyer_unit 采购人单位 | tender_agency 代理机构
    title: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ggzy")
    evidence_claim: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class AutomationRule(Base):
    """自动化规则（吸收 twenty workflow 精髓：声明式触发→动作，简化为单表规则）。

    触发固定 signal_created（采集/导入产生新信号后扫描）；
    条件：信号/企业字段三元组；动作：notify/create_lead/tag_competitor/bump_probability。
    workflow_run 精髓由 run_count + last_run_at + last_result 承载。
    """

    __tablename__ = "automation_rule"
    __table_args__ = (Index("ix_rule_enabled", "enabled"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 条件（对信号及关联企业判断；全部满足=走 actions，否则走 else_actions）
    cond_signal_type: Mapped[str | None] = mapped_column(Text)  # tender_win|tender_buy|tender
    cond_industry_key: Mapped[str | None] = mapped_column(Text)
    cond_source: Mapped[str | None] = mapped_column(Text)
    cond_min_score: Mapped[float | None] = mapped_column(REAL)
    cond_has_contact: Mapped[int | None] = mapped_column(Integer)  # 1=有联系人电话 0=无 None=不限
    cond_min_intent: Mapped[float | None] = mapped_column(REAL)   # 意图分下限
    # 动作：action_type 保留单动作兼容（v1）；动作链存 action_params.actions
    # （JSON 数组，顺序执行）；else_actions 为条件不满足时的备选链
    action_type: Mapped[str] = mapped_column(Text, nullable=False, default="notify")
    action_params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[str | None] = mapped_column(Text)
    last_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkflowRun(Base):
    """规则运行明细（吸收 twenty workflow-run：每条动作执行一行，可排障）。"""

    __tablename__ = "workflow_run"
    __table_args__ = (Index("ix_wfr_rule", "rule_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("automation_rule.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Todo(Base):
    """销售待办（吸收 twenty task：手动+规则自动生成，到期提醒）。"""

    __tablename__ = "todo"
    __table_args__ = (
        Index("ix_todo_open", "done", "due_date"),
        UniqueConstraint("enterprise_id", "title", "done", name="uq_todo_dedup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    enterprise_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE")
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("lead.id", ondelete="CASCADE"))
    due_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")  # manual|automation
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
