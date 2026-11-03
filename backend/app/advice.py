"""跟进时机行动建议（SPEC-AF AF2，销售时机研究吸收）。

规则全部可解释（why 字段给依据），无 LLM。研究依据：
- InsideSales：新线索 5 分钟内响应资格率 ×21，平均 B2B 响应 42 小时
- Cirrus Insight：周四/周三 16-17 点最佳外呼窗
- 业界共识：5+ touches 才成交，多数销售 1-2 次就放弃
- 行为信号（打开/点击/新信号）触发即时跟进
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import (
    EmailMessage,
    Followup,
    Lead,
    OutreachEvent,
    Signal,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_lead_advice(session: Session, lead: Lead) -> list[dict]:
    """单条线索的行动建议列表（按优先级排序，可空=暂时不需要动）。"""
    now = _now()
    cutoff_h24 = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_h48 = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_d7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    out: list[dict] = []

    if lead.status in ("new", "following"):
        fu_count = (
            session.query(Followup).filter(Followup.lead_id == lead.id).count()
        )
        since = lead.claimed_at or lead.created_at
        if fu_count == 0 and since and since < cutoff_h48:
            out.append({
                "code": "first_touch",
                "title": "首联黄金窗",
                "why": "新线索 48 小时内未首联——研究显示 5 分钟内响应资格率 ×21，每晚一天转化率衰减 8 倍",
            })

    if lead.status in ("new", "following", "intent"):
        last_sig = (
            session.query(Signal)
            .filter(Signal.enterprise_id == lead.enterprise_id,
                    Signal.created_at >= cutoff_h24)
            .order_by(Signal.created_at.desc())
            .first()
        )
        if last_sig is not None:
            out.append({
                "code": "hot_signal",
                "title": f"趁热跟进：新信号「{(last_sig.title or '')[:36]}」",
                "why": "24 小时内有新信号——行为信号触发即时跟进是成交最强前置",
            })

        # 已打开未回复 → 今日 16-17 点外呼窗
        last_sent = (
            session.query(EmailMessage)
            .filter(EmailMessage.lead_id == lead.id,
                    EmailMessage.status.in_(["sent", "replied"]))
            .order_by(EmailMessage.id.desc())
            .first()
        )
        if last_sent is not None and last_sent.status == "sent":
            opened = (
                session.query(OutreachEvent)
                .filter(OutreachEvent.message_id == last_sent.id,
                        OutreachEvent.type == "open")
                .first()
                is not None
            )
            replied_ev = (
                session.query(OutreachEvent)
                .filter(OutreachEvent.message_id == last_sent.id,
                        OutreachEvent.type == "reply")
                .first()
            )
            if opened and replied_ev is None and last_sent.sent_at and last_sent.sent_at >= cutoff_d7:
                out.append({
                    "code": "opened_no_reply",
                    "title": "已打开未回复——今天 16-17 点电话跟进",
                    "why": "7 天内有打开无回复：内容被看到但未行动；Cirrus 研究周四/周三 16-17 点为最佳外呼窗",
                })

        # 5+ touches 无回复 → 换渠道
        fu_total = (
            session.query(Followup).filter(Followup.lead_id == lead.id).count()
        )
        mails = (
            session.query(EmailMessage)
            .filter(EmailMessage.lead_id == lead.id,
                    EmailMessage.status.in_(["sent", "replied"]))
            .count()
        )
        replied = (
            session.query(EmailMessage)
            .filter(EmailMessage.lead_id == lead.id, EmailMessage.status == "replied")
            .count()
        )
        if replied == 0 and fu_total + mails >= 5:
            out.append({
                "code": "switch_channel",
                "title": "触达 5+ 次无回复——换渠道或暂缓",
                "why": f"已跟进 {fu_total} 次+邮件 {mails} 封均无回复；研究显示 5+ touches 才成交，但同渠道疲劳后应换人/换渠道",
            })

    if lead.lifecycle_stage == "at_risk":
        out.append({
            "code": "at_risk_save",
            "title": f"流失挽回（健康分 {lead.health_score}）",
            "why": "客户成功期健康分跌破 30 已自动降级——回访确认使用状况，挽回成本远低于新获客",
        })

    return out
