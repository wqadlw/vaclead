"""定时任务纯函数库（SPEC-B §6）：画像聚合 / 每日增量 / SQLite 备份。

全部函数可直接被测试调用；APScheduler（main.py）只负责按时触发并把
结果写 sync_watermark 水位表（失败不静默）。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Enterprise, EnterpriseStat, Signal, SyncWatermark, utcnow

logger = logging.getLogger("vaclead.jobs")

_AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")


# ---------------------------------------------------------------------------
# B2 企业中标画像
# ---------------------------------------------------------------------------
def extract_amount_wan(text: str) -> float | None:
    """从证据句/原文抽取成交价（万元）；无金额返回 None。"""
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    try:
        v = float(m.group(1))
        return v if v > 0 else None
    except ValueError:
        return None


def enrich_stats(session: Session, enterprise_id: int) -> dict:
    """聚合单个企业的中标画像（全来源 tender_win 信号，金额回抽自证据句/摘录）。"""
    ent = session.get(Enterprise, enterprise_id)
    if ent is None:
        return {}
    wins = (
        session.query(Signal)
        .filter(Signal.enterprise_id == enterprise_id, Signal.type == "tender_win")
        .order_by(Signal.pub_date.desc(), Signal.id.desc())
        .all()
    )
    amounts: list[float] = []
    last_win_date: str | None = None
    for w in wins:
        if last_win_date is None and w.pub_date:
            last_win_date = w.pub_date
        amt = extract_amount_wan(w.evidence_claim or "") or extract_amount_wan(
            w.raw_excerpt or ""
        )
        if amt is not None:
            amounts.append(amt)
    stat = session.get(EnterpriseStat, enterprise_id)
    if stat is None:
        stat = EnterpriseStat(enterprise_id=enterprise_id)
        session.add(stat)
    stat.bid_count = len(wins)
    stat.bid_amount_total_wan = round(sum(amounts), 2)
    stat.bid_amount_max_wan = max(amounts) if amounts else None
    stat.last_win_date = last_win_date
    # 头部采购人：从 tender_buy 侧反向找——本企业中标的信号证据里「采购人：X」出现最多者
    buyers: dict[str, int] = {}
    for w in wins:
        m = re.search(r"采购人[：:]\s*([^\s，,。；;]+)", w.evidence_claim or "")
        if m:
            buyers[m.group(1)] = buyers.get(m.group(1), 0) + 1
    stat.top_buyer = max(buyers, key=buyers.get) if buyers else None  # type: ignore[arg-type]
    stat.updated_at = utcnow()
    session.commit()
    return {
        "bid_count": stat.bid_count,
        "bid_amount_total_wan": stat.bid_amount_total_wan,
        "bid_amount_max_wan": stat.bid_amount_max_wan,
        "last_win_date": stat.last_win_date,
        "top_buyer": stat.top_buyer,
    }


def refresh_all_stats(session: Session) -> dict:
    """全量刷新有中标信号的企业画像。"""
    ids = session.scalars(
        select(Signal.enterprise_id).where(Signal.type == "tender_win").distinct()
    ).all()
    for eid in ids:
        enrich_stats(session, int(eid))
    return {"enriched": len(ids)}


# ---------------------------------------------------------------------------
# B3 备份
# ---------------------------------------------------------------------------
def backup_sqlite(backup_dir: str | Path, keep: int = 7) -> dict:
    """SQLite backup API（WAL 安全）滚动备份，保留最近 keep 份。

    返回 {path, size_bytes}。备份文件可用 PRAGMA integrity_check 验证。
    """
    from .db import ENGINE

    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # 含微秒：同一秒内多次备份不互相覆盖
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = dest_dir / f"vaclead_{stamp}.db"
    raw_src = ENGINE.raw_connection().driver_connection  # sqlite3.Connection
    raw_dst_path = str(dest)
    import sqlite3

    src_conn = raw_src if isinstance(raw_src, sqlite3.Connection) else raw_src.connection
    dst = sqlite3.connect(raw_dst_path)
    try:
        src_conn.backup(dst)  # 官方在线备份 API：WAL 下一致快照
        dst.commit()
    finally:
        dst.close()
    # 滚动保留
    backups = sorted(dest_dir.glob("vaclead_*.db"))
    removed = 0
    for old in backups[:-keep]:
        old.unlink()
        removed += 1
    return {"path": str(dest), "size_bytes": dest.stat().st_size, "removed": removed}


# ---------------------------------------------------------------------------
# B3 每日增量
# ---------------------------------------------------------------------------
def run_daily_incremental() -> dict:
    """每日增量：ggzy 宽词近 7 天 + ccgp 强词近 7 天（同步小批次，失败记水位）。

    采集器自带限速与去重，重复词安全；时长可控（分钟级）。
    """
    from .collectors.ccgp import search_ccgp_all
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .vertical import device_strong_terms, ggzy_name_terms
    from .api.imports import extract_enterprise_name
    from .models import Signal

    stats = {"ggzy_new": 0, "ccgp_signals": 0}
    # --- ggzy 宽词增量 ---
    from .collectors.ggzy import list_bids, normalize_bid_record, normalize_entity_record, search_all_entities

    for kw in ggzy_name_terms():
        for raw in search_all_entities(kw, max_pages=1, size=200):
            rec = normalize_entity_record(raw)
            if rec is None:
                continue
            with SessionLocal() as s:
                ent, is_new = resolve_enterprise(
                    s, rec["name"], source="ggzy", uscc=rec["uscc"]
                )
                if is_new:
                    stats["ggzy_new"] += 1
                if rec["uscc"] and rec["bid_count"] > 0:
                    result = list_bids(rec["uscc"], page=1, size=20)
                    for b in result.get("records") or []:
                        nb = normalize_bid_record(b)
                        if nb is None:
                            continue
                        exists = (
                            s.query(Signal)
                            .filter(
                                Signal.enterprise_id == ent.id,
                                Signal.url == "",
                                Signal.title == nb["project_name"],
                                Signal.type == "tender_win",
                            )
                            .first()
                        )
                        if exists is None:
                            s.add(Signal(
                                enterprise_id=ent.id,
                                type="tender_win",
                                title=nb["project_name"],
                                url="",
                                pub_date=nb["pub_date"],
                                matched_terms="[]",
                                hit_part="title",
                                raw_excerpt=f"{nb['trade_type'] or '中标'}｜成交价{nb['bid_price_wan']}万元"
                                if nb["bid_price_wan"] is not None
                                else (nb["trade_type"] or "中标"),
                                evidence_claim=f"该主体在公共资源交易平台中标「{nb['project_name']}」",
                                base_score=50.0,
                            ))
                s.commit()

    # --- ccgp 强词增量（近 7 天，只扫第一页）---
    from .vertical import device_strong_terms as _dst

    for kw in _dst()[:12]:
        for item in search_ccgp_all(kw, days=7, max_pages=1):
            if not any(
                t in item["title"] or t in item["summary"]
                for t in _dst()
            ):
                continue
            buyer = item.get("buyer") or extract_enterprise_name(item["title"], item["summary"])
            if not buyer or len(buyer) < 4:
                continue
            with SessionLocal() as s:
                ent, _ = resolve_enterprise(s, buyer, source="ccgp_buyer")
                exists = (
                    s.query(Signal)
                    .filter(
                        Signal.enterprise_id == ent.id,
                        Signal.url == item["url"],
                        Signal.title == item["title"],
                    )
                    .first()
                )
                if exists is None:
                    s.add(Signal(
                        enterprise_id=ent.id,
                        type="tender",
                        title=item["title"],
                        url=item["url"],
                        pub_date=item["pub_date"],
                        matched_terms="[]",
                        hit_part="title",
                        raw_excerpt=item["summary"][:200],
                        evidence_claim=f"政采网{item['notice_type'] or '公告'}：该单位就「{item['title'][:40]}」发布公告",
                        base_score=50.0,
                    ))
                    stats["ccgp_signals"] += 1
                s.commit()

    # 意图/画像/检索投影收尾
    from .api.imports import recompute_intent

    with SessionLocal() as s:
        ids = s.scalars(select(Enterprise.id)).all()
        for eid in ids:
            recompute_intent(s, int(eid))
        s.commit()
    with SessionLocal() as s:
        refresh_all_stats(s)
    try:
        from . import search as meili
        from .db import SessionLocal as _SL

        with _SL() as s:
            meili.sync_all(s)
    except Exception:  # noqa: BLE001 — 检索层失败不影响增量
        logger.warning("增量后 Meili 同步失败", exc_info=True)
    return stats


# ---------------------------------------------------------------------------
# 水位
# ---------------------------------------------------------------------------
def mark_watermark(session: Session, job_name: str, status: str, stats: dict) -> None:
    """写任务水位（失败不静默：last_status 落 failed + stats 带错误）。"""
    row = session.get(SyncWatermark, job_name)
    if row is None:
        row = SyncWatermark(job_name=job_name)
        session.add(row)
    row.last_run_at = datetime.now(timezone.utc).isoformat()
    row.last_status = status
    row.stats_json = json.dumps(stats, ensure_ascii=False)
    session.commit()


# ---------------------------------------------------------------------------
# SPEC-C F1 订阅监控 → 通知
# ---------------------------------------------------------------------------
_KIND_CN = {"tender_win": "中标动态", "tender_buy": "公开采购", "tender": "政采公告"}


def generate_notifications(session: Session) -> dict:
    """为所有订阅企业的新信号生成站内通知（幂等：唯一约束去重，可重扫）。"""
    from .models import Notification, Subscription

    subs = session.query(Subscription).all()
    if not subs:
        return {"notifications": 0, "subscriptions": 0}
    by_ent: dict[int, list[int]] = {}
    for sub in subs:
        by_ent.setdefault(sub.enterprise_id, []).append(sub.user_id)
    created = 0
    for ent_id, user_ids in by_ent.items():
        ent = session.get(Enterprise, ent_id)
        if ent is None:
            continue
        signals = (
            session.query(Signal)
            .filter(Signal.enterprise_id == ent_id)
            .order_by(Signal.id.desc())
            .limit(20)
            .all()
        )
        from .notify.channels import notify_dispatch

        for sig in signals:
            for uid in user_ids:
                results = notify_dispatch(
                    session, uid,
                    kind="signal",
                    title=f"【{_KIND_CN.get(sig.type, '新动态')}】{ent.name}",
                    body=(sig.evidence_claim or sig.title or "")[:120],
                    ref_type="signal",
                    ref_id=sig.id,
                )
                # in_app 渠道 dedup=已存在，ok=新增（SPEC-E E1-A4：统一渠道层）
                if results.get("in_app") == "ok":
                    created += 1
    session.commit()
    return {"notifications": created, "subscriptions": len(subs)}


# ---------------------------------------------------------------------------
# 自动化规则引擎（吸收 twenty workflow：触发→条件→动作，简化为信号事件扫描）
# ---------------------------------------------------------------------------
def _rule_matches(rule, signal: Signal, ent: Enterprise, session: Session | None = None) -> bool:
    """条件判定：经典四元组 + has_contact（联系人电话）/ min_intent（意图分）。"""
    if rule.cond_signal_type and signal.type != rule.cond_signal_type:
        return False
    if rule.cond_industry_key and (ent.industry_key or "") != rule.cond_industry_key:
        return False
    if rule.cond_source and (ent.source or "") != rule.cond_source:
        return False
    if rule.cond_min_score is not None and signal.base_score < rule.cond_min_score:
        return False
    if rule.cond_min_intent is not None and (ent.intent_score or 0) < rule.cond_min_intent:
        return False
    if rule.cond_has_contact is not None and session is not None:
        from .models import Contact

        has = (
            session.query(Contact.enterprise_id)
            .filter(Contact.enterprise_id == ent.id)
            .first()
            is not None
        )
        if bool(rule.cond_has_contact) != has:
            return False
    return True


def _rule_actions(rule) -> tuple[list[str], list[str]]:
    """取动作链与备选链（v1 单动作自动包成链；存 action_params.actions/else_actions）。"""
    try:
        params = json.loads(rule.action_params or "{}")
    except ValueError:
        params = {}
    actions = params.get("actions") or [rule.action_type]
    else_actions = params.get("else_actions") or []
    return [a for a in actions if a], [a for a in else_actions if a]


def _do_action(action: str, rule, signal: Signal, ent: Enterprise, session: Session) -> str:
    """执行单个动作，返回结果描述（失败抛异常由调用方记录）。"""
    from .models import Lead, Todo

    if action == "notify":
        # 统一渠道层入口（吸收 Novu：按用户偏好路由，in_app 保底）
        from .notify.channels import notify_dispatch

        results = notify_dispatch(
            session, 1,
            kind="automation",
            title=f"【{rule.name}】{ent.name}",
            body=(signal.evidence_claim or signal.title or "")[:120],
            ref_type="signal",
            ref_id=signal.id,
        )
        return "notify:" + ",".join(f"{k}={v}" for k, v in results.items())

    if action == "create_lead":
        existing = session.query(Lead).filter(Lead.enterprise_id == ent.id).first()
        if existing is not None:
            return "lead-exists"
        if ent.is_competitor:
            return "lead-skipped-competitor"
        session.add(Lead(
            enterprise_id=ent.id,
            status="new",
            probability=10,
            tenant_id=ent.tenant_id,
        ))
        session.flush()  # autoflush=False：立即落库让同批后续查重可见
        return "lead-created"

    if action == "tag_competitor":
        ent.is_competitor = 1
        return "tagged"

    if action == "bump_probability":
        leads = session.query(Lead).filter(Lead.enterprise_id == ent.id).all()
        for ld in leads:
            ld.probability = min(int(ld.probability) + 10, 100)
        return f"bumped-{len(leads)}"

    if action == "set_grade":
        composite = (ent.icp_score or 0) * 0.4 + (ent.intent_score or 0) * 0.6
        grade = "S" if composite >= 80 else "A" if composite >= 60 else "B" if composite >= 40 else None
        for ld in session.query(Lead).filter(Lead.enterprise_id == ent.id).all():
            ld.grade = grade
        return f"grade-{grade or 'none'}"

    if action == "create_todo":
        title = f"跟进 {ent.name}：{signal.title[:30]}"
        dup = (
            session.query(Todo)
            .filter(Todo.enterprise_id == ent.id, Todo.title == title, Todo.done == 0)
            .first()
        )
        if dup is not None:
            return "todo-exists"
        import datetime as _dt

        due = (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
        session.add(Todo(
            title=title,
            enterprise_id=ent.id,
            due_date=due,
            source="automation",
        ))
        session.flush()
        return "todo-created"

    raise ValueError(f"未知动作: {action}")


def run_automation_rules(session: Session) -> dict:
    """扫描 enabled 规则，对未处理信号执行动作（幂等：水位记录在 action_params）。

    水位：action_params JSON 的 _last_signal_id 记录每规则处理到的信号 id，
    重跑只处理增量——workflow-run 的运行记录语义由此承载。
    """
    from .models import AutomationRule

    rules = session.query(AutomationRule).filter(AutomationRule.enabled == 1).all()
    if not rules:
        return {"rules": 0, "matched": 0, "actions": 0}
    matched = 0
    actions = 0
    for rule in rules:
        try:
            params = json.loads(rule.action_params or "{}")
        except ValueError:
            params = {}
        last_signal_id = int(params.get("_last_signal_id", 0))
        new_signals = (
            session.query(Signal)
            .filter(Signal.id > last_signal_id)
            .order_by(Signal.id.asc())
            .limit(200)
            .all()
        )
        last_result: str | None = None
        actions_chain, else_chain = _rule_actions(rule)
        for sig in new_signals:
            ent = session.get(Enterprise, sig.enterprise_id)
            if ent is None:
                params["_last_signal_id"] = sig.id
                continue
            hit = _rule_matches(rule, sig, ent, session)
            chain = actions_chain if hit else else_chain
            if not chain:
                # 命中无动作且无备选链：仅推进水位（保持旧行为兼容）
                params["_last_signal_id"] = sig.id
                continue
            if hit:
                matched += 1
            # 动作链顺序执行：每动作一行 WorkflowRun，单动作失败不阻断后续动作
            from .models import WorkflowRun

            for action in chain:
                ok = 1
                try:
                    last_result = _do_action(action, rule, sig, ent, session)
                    actions += 1
                except Exception as exc:  # noqa: BLE001
                    last_result = f"error: {exc}"
                    ok = 0
                    logger.warning("规则 %s 动作 %s 失败: %s", rule.name, action, exc)
                session.add(WorkflowRun(
                    rule_id=rule.id,
                    signal_id=sig.id,
                    enterprise_id=sig.enterprise_id,
                    action_type=action,
                    result=last_result or "",
                    ok=ok,
                ))
            params["_last_signal_id"] = sig.id
        rule.action_params = json.dumps(params, ensure_ascii=False)
        rule.run_count += matched
        rule.last_run_at = datetime.now(timezone.utc).isoformat()
        if last_result is not None:
            rule.last_result = last_result
        session.commit()
    return {"rules": len(rules), "matched": matched, "actions": actions}
