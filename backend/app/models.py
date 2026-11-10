"""vaclead 数据模型 — 全部表定义（契约文件，遵循 docs/SPEC.md §1）。

所有业务表含 tenant_id 预留多租户。时间戳统一存 UTC ISO 字符串。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import (
    REAL,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# SPEC-AMT：标讯成交金额正则——全库唯一权威定义（bulletin/jobs/reports 等
# 处的 _AMOUNT_RE 副本逐步收拢到这里；放 models 防 API 模块循环 import）
AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")


def extract_amount_wan(text: str) -> float | None:
    """SPEC-AMT：从证据句/原文抽成交价（万元）；无金额或 0 占位返回 None。

    0 视为未知（ggzy 官方源常见 0/空占位，按 NULL 处理不污染金额排序）。
    """
    m = AMOUNT_RE.search(text or "")
    if not m:
        return None
    try:
        v = float(m.group(1))
        return v if v > 0 else None
    except ValueError:
        return None


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="sales")  # admin/sales
    display_name: Mapped[str] = mapped_column(Text, default="")
    # SPEC-N 组织化：邮箱/启用（禁用=拒登录）/所属团队；旧库由 init_db 幂等 ALTER 补列
    email: Mapped[str | None] = mapped_column(Text)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Team(Base):
    """团队（SPEC-N N2，EspoCRM Teams 思想）：负责人 leader 可见成员线索。"""

    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    leader_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class AssignmentRule(Base):
    """线索自动分配规则（SPEC-N N3，EspoCRM Assignment Rules 思想）。

    conditions 全空=命中所有；round_robin 用持久 cursor 列（修 EspoCRM 论坛
    并发涌入轮转失效同款问题）；before_flush 钩子统一分配，覆盖全部线索创建入口。
    """

    __tablename__ = "assignment_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # JSON {industry_key?,province?,source?}，全空=命中所有
    target_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"))
    target_user_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    strategy: Mapped[str] = mapped_column(Text, nullable=False, default="fixed")
    # fixed=固定第一人 | round_robin=按序轮转（cursor 持久化）
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


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
    established_at: Mapped[str | None] = mapped_column(Text)  # 成立日期 YYYY-MM-DD（SPEC-M M2a，ggzy estdate 补入库）
    legal_rep: Mapped[str | None] = mapped_column(Text)  # 法定代表人（SPEC-U U1，ggzy legal_rep 补入库）
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON 数组（SPEC-V V2 销售自定义标签）
    poi_id: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[str | None] = mapped_column(Text)  # SPEC-AM AM1 软删除（NULL=正常；回收站 30 天自动清空）
    # SPEC-AS AS2 客户角色（业务锚点：昊志=昊青销售商+系统搭建商）：
    # oem=应用设备制造商（买泵装设备）| end_user=终端用户 | reseller=分销 |
    # integrator=系统工程商 | NULL 未定。与 is_competitor（泵制造商）正交。
    customer_role: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)

    # ---- helpers ----
    def completeness(self) -> int:
        """资料完整度 0-4（SPEC-U U3）：法人/成立日期/注册资本/经营范围四要素计数。

        内部数据质量管理用途（帮销售知道缺什么），非信用评分。
        """
        return sum(
            1
            for v in (self.legal_rep, self.established_at,
                      self.registered_capital_wan, self.business_scope)
            if v
        )

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
        # title 入约束（SPEC-I I1）：与全库 5 处查重口径一致——空 url 的同企业不同项目信号须共存
        UniqueConstraint("url", "enterprise_id", "type", "title", name="uq_signal_dedup"),
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
    # SPEC-AMT 成交金额（万元，NULL=未知）：入库由 _signal_fill_amount_wan 钩子从
    # raw_excerpt 抽"成交价X万元"持久化——标讯列表 SQL ORDER BY 用（此前每行渲染
    # 时正则，无法下推数据库）；旧库存量行经 /signals 惰性回填（api/bulletin）
    amount_wan: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)

    def terms_list(self) -> list[str]:
        try:
            return json.loads(self.matched_terms or "[]")
        except json.JSONDecodeError:
            return []

    def set_terms(self, terms: list[str]) -> None:
        self.matched_terms = json.dumps(terms, ensure_ascii=False)


@event.listens_for(Signal, "before_insert")
def _signal_fill_amount_wan(_mapper, _connection, target: "Signal") -> None:
    """SPEC-AMT：入库即抽金额——映射器级钩子覆盖全部写入点（ggzy/ccgp 采集、
    各省备案通道、CSV 导入、测试直构），杜绝"漏一个采集源缺金额"；
    显式传入 amount_wan 的调用方不覆盖。"""
    if target.amount_wan is None:
        target.amount_wan = extract_amount_wan(target.raw_excerpt)


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
    lifecycle_stage: Mapped[str] = mapped_column(
        Text, nullable=False, default="lead"
    )  # SPEC-AE: lead→following→intent→won→customer_success|at_risk|churned
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)  # SPEC-AE: 0-100
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
    # digest 聚合键（SPEC-F F1，吸收 novu）：同键单条聚合；企业动态用 "ent:{id}"
    digest_key: Mapped[str | None] = mapped_column(Text)
    digested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 1=已折入汇总
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


class Watch(Base):
    """官网变更监控（SPEC-RADAR，借鉴 changedetection.io 的 Watch 模型）：
    盯竞对/重点客户的官网页面——内容 hash 变化即生成 web_change 信号+
    通知（新品发布/中标新闻/招聘页更新都是获客前兆）。"""

    __tablename__ = "watch"
    __table_args__ = (Index("ix_watch_enterprise", "enterprise_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)  # 如 "官网新闻页"
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)  # 首采前为 NULL
    snapshot_head: Mapped[str | None] = mapped_column(Text)  # 变更后正文头部预览
    last_checked_at: Mapped[str | None] = mapped_column(Text)
    last_changed_at: Mapped[str | None] = mapped_column(Text)
    check_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_last: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


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
    # SPEC-J J2 联系人中心：公告法定公开邮箱 + 决策角色推断 + 验证态
    # verify_state: verified=公告直接公开 | inferred=推断 | none=未知
    email: Mapped[str | None] = mapped_column(Text)
    decision_role: Mapped[str | None] = mapped_column(Text)  # decision_maker|handler
    verify_state: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="ggzy")
    evidence_claim: Mapped[str | None] = mapped_column(Text)
    # SPEC-AH AH2：规范化电话列表（["010-68290510",...]）——title 槽位存原文，
    # 拨得通的真号码在这里（GLOB 数字判断分不清电话和项目编号）
    phones_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # SPEC-AN AN1 触达结果（受控枚举）：pending|no_answer|voicemail|invalid|
    # connected|interested|not_interested（连接类与对话类分层）
    call_result: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    called_at: Mapped[str | None] = mapped_column(Text)
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
    cond_has_email_decision: Mapped[int | None] = mapped_column(Integer)  # SPEC-J J2：1=有公告验证邮箱的决策联系人
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
        # 部分唯一索引（SPEC-E E1）：仅未完成行唯一——同标题完成后可重建，
        # 且多行 done=1 不再撞约束（修复 toggle 二次完成 IntegrityError）
        Index(
            "uq_todo_open_dedup",
            "enterprise_id", "title",
            unique=True,
            sqlite_where=text("done = 0"),
        ),
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


class AuditLog(Base):
    """审计日志（SPEC-J J1，espocrm 审计思想）：关键实体写操作全留痕。

    user_id=0 表示系统来源（采集/任务/调度）；insert 不落字段 diff（after 即全量），
    update 落字段级 [before, after]，delete 落 [before, None]。
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_entity", "entity", "entity_id"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # insert/update/delete
    entity: Mapped[str] = mapped_column(Text, nullable=False)  # enterprise/lead/contact
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    changes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class UserApiKey(Base):
    """API 密钥（SPEC-J J1，twenty api-key 思想）：机器对机器访问通道。

    明文只在创建响应里出现一次，库存 sha256；scopes=read 时仅放行 GET/HEAD。
    """

    __tablename__ = "user_api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False, default="")  # 展示用前 12 位
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="read")  # read|rw
    last_used_at: Mapped[str | None] = mapped_column(Text)
    revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class UserTarget(Base):
    """月度目标（SPEC-J J6，espocrm Forecasts 思想）：万元/人/月。"""

    __tablename__ = "user_target"
    __table_args__ = (UniqueConstraint("user_id", "month", name="uq_user_target"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    month: Mapped[str] = mapped_column(Text, nullable=False)  # YYYY-MM
    amount_wan: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class SubscriptionRule(Base):
    """关键词/地区订阅规则（SPEC-J J7）：每日新信号按规则匹配 → 通知。

    与按企业订阅（subscription 表）互补；value=关键词文本或省市名。
    """

    __tablename__ = "subscription_rule"
    __table_args__ = (UniqueConstraint("user_id", "kind", "value", name="uq_sub_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # keyword|region
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class CaseStudy(Base):
    """私有成交案例（SPEC-AZ AZ2）：昊志真实交付记录——比公开招标证据更强的
    提案信任资产。内容为手工录入（真实交付后由销售填写，不虚构）。"""

    __tablename__ = "case_study"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise.id", ondelete="SET NULL")
    )
    industry_key: Mapped[str | None] = mapped_column(Text)  # 行业锚（提案按行业匹配）
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    year: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Product(Base):
    """产品目录（SPEC-J J3，espocrm Quotes 思想）：报价单的行项目来源。"""

    __tablename__ = "product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)  # 型号
    spec: Mapped[str | None] = mapped_column(Text)  # 参数摘要
    price: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)  # 万元
    price_unit: Mapped[str] = mapped_column(Text, nullable=False, default="台")
    # SPEC-AS AS3 产品→应用行业反向匹配（昊青产品线×客户工艺）
    application_industries: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class InstalledUnit(Base):
    """装机台账（SPEC-AS AS1，CMMS asset 模型移植）。

    每台卖出的泵=一条 15-20 年现金流——耗材时钟（密封 12-24 月/滤芯
    2000-4000h）驱动回访待办，装机保有量即代理商的资产负债表。
    """

    __tablename__ = "installed_unit"
    __table_args__ = (Index("ix_iu_enterprise", "enterprise_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    serial: Mapped[str | None] = mapped_column(Text)
    install_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    # active|replaced|retired
    consumable_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # {"seal_due":"YYYY-MM-DD","filter_due":"YYYY-MM-DD"} 耗材时钟
    notes: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Quote(Base):
    """报价单（SPEC-J J3）：商机 intent→won 之间的转化单据。

    total_wan 由 items 汇总派生（服务端重算，不信前端传值）。
    """

    __tablename__ = "quote"
    __table_args__ = (Index("ix_quote_lead", "lead_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    # draft 草稿 | sent 已发送 | won 赢单 | lost 输单
    valid_until: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    total_wan: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    remark: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class QuoteItem(Base):
    """报价单行项：产品快照（价格下单时定格，防目录调价改历史）。"""

    __tablename__ = "quote_item"
    __table_args__ = (Index("ix_quote_item_quote", "quote_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)  # 下单时产品名快照
    spec: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(REAL, nullable=False, default=1.0)
    price: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)  # 万元/单位
    price_unit: Mapped[str] = mapped_column(Text, nullable=False, default="台")


class LeadForm(Base):
    """留资表单（SPEC-J J5，espocrm Lead Capture 思想）：公开渠道捕获进线索。"""

    __tablename__ = "lead_form"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submissions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class LeadCaptureLog(Base):
    """表单提交日志：原始载荷留档（防垃圾审计 + 丢数据可追溯）。"""

    __tablename__ = "lead_capture_log"
    __table_args__ = (Index("ix_capture_form", "form_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(
        ForeignKey("lead_form.id", ondelete="CASCADE"), nullable=False
    )
    company: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    need: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    enterprise_id: Mapped[int | None] = mapped_column(Integer)  # 归一匹配结果
    lead_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


# ---------------------------------------------------------------------------
# SPEC-J J4 邮件序列触达闭环
# ---------------------------------------------------------------------------
class EmailAccount(Base):
    """发信邮箱账户：emailengine 托管或 SMTP 直连，预热状态内嵌（warmbly 模型）。"""

    __tablename__ = "email_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="emailengine")
    # emailengine 走全局 settings_kv 四键；smtp 直连用下列四列
    smtp_host: Mapped[str | None] = mapped_column(Text)
    smtp_port: Mapped[int | None] = mapped_column(Integer)
    smtp_user: Mapped[str | None] = mapped_column(Text)
    smtp_pass: Mapped[str | None] = mapped_column(Text)
    warmup_started_at: Mapped[str | None] = mapped_column(Text)  # ISO；空=成熟账户不设限
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active|paused
    health: Mapped[str] = mapped_column(Text, nullable=False, default="healthy")
    # warmbly 五态 healthy|watch|throttled|quarantined|blocked；后两态预算归零
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Sequence(Base):
    """触达序列：有序步骤模板（listmonk 式变量 {{lead.company}} 等在发送时渲染）。"""

    __tablename__ = "sequence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON [{delay_days,subject,body}]；delay_days=距上一步的天数，首步立即
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Campaign(Base):
    """触达活动：序列 × 发信账户 × 目标筛选（target_filter JSON）。"""

    __tablename__ = "campaign"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_id: Mapped[int | None] = mapped_column(ForeignKey("sequence.id"))
    email_account_id: Mapped[int | None] = mapped_column(ForeignKey("email_account.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    # draft|running|paused|done；只有 running 参与调度
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    track_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # SPEC-AF：打开/点击跟踪开关（1=开；listmonk 隐私实践=按活动可关）
    target_filter: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # JSON {score_min,industry,source}：enqueue 时对在管线索过滤
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    started_at: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class EmailMessage(Base):
    """触达邮件：入队(queued)→发送(sent/failed)→回信(replied)全状态单行。

    UNIQUE(campaign,lead,step) 防重发是硬约束：enqueue 幂等靠它兜底。
    subject/body 入队存模板原文，发送时渲染并回写（留档客户实际收到的内容）。
    """

    __tablename__ = "email_message"
    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_id", "step_idx", name="uq_outreach_dedup"),
        Index("ix_outreach_status_sched", "status", "scheduled_at"),
        Index("ix_outreach_campaign", "campaign_id"),
        Index("ix_outreach_account_day", "account_email", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False
    )
    step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_email: Mapped[str] = mapped_column(Text, nullable=False, default="")
    to_email: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    # queued|sent|failed|bounced|replied
    scheduled_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    sent_at: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)  # 发信通道返回的 Message-ID
    thread_id: Mapped[str | None] = mapped_column(Text)  # 会话归组=首封 message_id
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class OutreachEvent(Base):
    """触达事件流：sent|open|click|reply|bounced|unsubscribed|failed。"""

    __tablename__ = "outreach_event"
    __table_args__ = (Index("ix_oe_message", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("email_message.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Unsubscribe(Base):
    """退订名单（合规红线：触达必带退订链接，退订后所有活动不再发送）。"""

    __tablename__ = "unsubscribe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    lead_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


# ---------------------------------------------------------------------------
# SPEC-P Webhooks 出站（开放集成）
# ---------------------------------------------------------------------------
class Webhook(Base):
    """出站 webhook 订阅（EspoCRM Webhooks 思想）：事件类型 + 目标 URL + 签名密钥。"""

    __tablename__ = "webhook"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    event_types: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    secret: Mapped[str] = mapped_column(Text, nullable=False)  # HMAC-SHA256 签名密钥
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class WebhookDelivery(Base):
    """投递队列/日志：随业务事务原子入队，flush worker 异步投递（指数退避）。"""

    __tablename__ = "webhook_delivery"
    __table_args__ = (Index("ix_wd_status", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(
        ForeignKey("webhook.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # pending|ok|failed（failed=重试耗尽终态）
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class EnterpriseNote(Base):
    """企业备注（SPEC-V V1）：销售自由文本工作笔记，区别于客观事实流（信号时间线）。

    可见性跟随企业（情报页可达即可见，同 contacts 语义）；删除限本人或 admin。
    """

    __tablename__ = "enterprise_note"
    __table_args__ = (Index("ix_note_enterprise", "enterprise_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class SavedView(Base):
    """团队共享视图（SPEC-AC AC3）：把筛选/列配置存服务端，团队统一管线口径。

    user_ids 空数组=全员可见；非空=仅名单内可见。owner 可删/改，他人只读应用。
    """

    __tablename__ = "saved_view"
    __table_args__ = (
        Index("ix_saved_view_page", "page"),
        UniqueConstraint("page", "name", "owner_id", name="uq_saved_view"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page: Mapped[str] = mapped_column(Text, nullable=False)  # enterprises/leads
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    user_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    filters: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    hidden_cols: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    dense: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    col_aggs: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class Contract(Base):
    """合同（SPEC-AD）：赢单→签约→回款→续约的后销售链路。

    状态机：draft→active→completed|terminated
    从 won Quote 一键生成（复制金额与行项目）。
    """

    __tablename__ = "contract"
    __table_args__ = (Index("ix_contract_lead", "lead_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False
    )
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    amount_wan: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    # draft→active→completed|terminated
    sign_date: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(Text)
    end_date: Mapped[str | None] = mapped_column(Text)
    renewal_date: Mapped[str | None] = mapped_column(Text)  # 自动= end_date - 30d
    payment_terms: Mapped[str | None] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)


class PaymentMilestone(Base):
    """回款分期（SPEC-AD AD2）：合同额按比例分期，逐笔跟踪回款。

    典型分期：预付款 30% → 发货款 40% → 验收款 20% → 质保金 10%（中国设备业惯例）。
    """

    __tablename__ = "payment_milestone"
    __table_args__ = (Index("ix_pm_contract", "contract_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contract.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stage: Mapped[str] = mapped_column(Text, nullable=False, default="payment")
    # prepayment|delivery|acceptance|warranty|payment
    ratio: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)  # 百分比
    amount_wan: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    due_date: Mapped[str | None] = mapped_column(Text)
    trigger_desc: Mapped[str | None] = mapped_column(Text)  # 触发条件（如"验收合格后 7 日"）
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # pending→paid|overdue
    paid_date: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utcnow)
