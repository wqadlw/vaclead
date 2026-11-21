"""定时任务纯函数库（SPEC-B §6）：画像聚合 / 每日增量 / SQLite 备份。

全部函数可直接被测试调用；APScheduler（main.py）只负责按时触发并把
结果写 sync_watermark 水位表（失败不静默）。
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (  # SPEC-AMT：金额抽取唯一实现在 models，此处 re-export
    Contact,
    Enterprise,
    EnterpriseStat,
    Signal,
    SyncWatermark,
    Watch,
    extract_amount_wan,
    utcnow,
)

logger = logging.getLogger("vaclead.jobs")


# ---------------------------------------------------------------------------
# SPEC-J J7 关键词/地区订阅规则 → 通知；J1 审计滚动清理
# ---------------------------------------------------------------------------
def run_subscription_rules(session: Session) -> dict:
    """新信号按用户订阅规则（关键词/地区）匹配 → notify_dispatch（幂等可重扫）。

    扫描窗口：自上次水位以来的新信号（单轮 ≤500 条防炸）；去重靠
    Notification 唯一约束 (ref_type, ref_id, user_id) + 预查。
    """
    from .models import Notification, SubscriptionRule
    from .notify.channels import notify_dispatch

    rules = session.query(SubscriptionRule).all()
    if not rules:
        return {"matched": 0, "signals": 0, "rules": 0}
    last_run = session.get(SyncWatermark, "subscription_rules")
    cutoff = last_run.last_run_at if last_run and last_run.last_run_at else None
    q = session.query(Signal)
    if cutoff:
        q = q.filter(Signal.created_at > cutoff)
    signals = q.order_by(Signal.id.desc()).limit(500).all()
    if not signals:
        return {"matched": 0, "signals": 0, "rules": len(rules)}

    matched = 0
    notified: set[tuple[int, int]] = set()  # (signal_id, user_id) 同轮防重（autoflush=False 下查询不可见 pending）
    filter_rules: list[tuple[object, dict]] = []  # SPEC-O O2：filter 规则预解析
    import json as _json

    for rule in rules:
        if rule.kind == "filter":
            try:
                cond = _json.loads(rule.value)
                if isinstance(cond, dict):
                    filter_rules.append((rule, cond))
            except ValueError:
                continue
    for sig in signals:
        ent = session.get(Enterprise, sig.enterprise_id)
        if ent is None:
            continue
        for rule in rules:
            if rule.kind == "keyword":
                hit = rule.value in (sig.title or "") or rule.value in (ent.name or "")
                summary = rule.value
            elif rule.kind == "filter":
                cond = next((c for r, c in filter_rules if r.id == rule.id), {})
                hit = _filter_rule_hits(sig, ent, cond)
                summary = _filter_summary(cond)
            else:  # region
                hit = rule.value in ((ent.province or "") + (ent.city or ""))
                summary = rule.value
            if not hit or (sig.id, rule.user_id) in notified:
                continue
            dup = (
                session.query(Notification)
                .filter(
                    Notification.ref_type == "sub_signal",
                    Notification.ref_id == sig.id,
                    Notification.user_id == rule.user_id,
                )
                .first()
            )
            if dup is not None:
                continue
            notified.add((sig.id, rule.user_id))
            notify_dispatch(
                session, rule.user_id,
                kind="signal",
                title=f"订阅命中：{summary}",
                body=f"「{ent.name}」新信号「{sig.title}」（{sig.pub_date or '日期未知'}）",
                ref_type="sub_signal",
                ref_id=sig.id,
            )
            matched += 1
    return {"matched": matched, "signals": len(signals), "rules": len(rules)}


def _filter_rule_hits(sig: Signal, ent: Enterprise, cond: dict) -> bool:
    """筛选快照订阅命中判断（SPEC-O O2）：增量信号在 Python 侧逐条比对。

    字段语义与标讯中心 /api/signals 一致；days 用信号 created_at（秒级 ISO）比较。
    """
    from datetime import datetime, timedelta, timezone

    if cond.get("type") and sig.type != cond["type"]:
        return False
    if cond.get("industry_key") and (ent.industry_key or "") != cond["industry_key"]:
        return False
    if cond.get("province") and (ent.province or "") != cond["province"]:
        return False
    if cond.get("q"):
        q = str(cond["q"]).strip()
        if q and q not in (sig.title or ""):
            return False
    if cond.get("days"):
        try:
            created = datetime.fromisoformat(str(sig.created_at))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created > timedelta(days=int(cond["days"])):
                return False
        except ValueError:
            pass
    return True


def _filter_summary(cond: dict) -> str:
    parts = []
    if cond.get("type"):
        parts.append(f"类型={cond['type']}")
    if cond.get("industry_key"):
        parts.append(f"行业={cond['industry_key']}")
    if cond.get("province"):
        parts.append(f"地区={cond['province']}")
    if cond.get("q"):
        parts.append(f"关键词={cond['q']}")
    if cond.get("days"):
        parts.append(f"近{cond['days']}天")
    return "筛选[" + "，".join(parts) + "]" if parts else "筛选[全部标讯]"


def audit_cleanup(session: Session, keep: int = 50000) -> dict:
    """审计日志滚动清理（SPEC-J J1）：保留最近 keep 条（默认 5 万）。"""
    from .models import AuditLog

    total = session.query(func.count()).select_from(AuditLog).scalar() or 0
    if total <= keep:
        return {"total": total, "removed": 0}
    if keep <= 0:
        removed = session.query(AuditLog).delete(synchronize_session=False)
        session.commit()
        return {"total": total, "removed": removed}
    min_id = session.execute(
        select(AuditLog.id).order_by(AuditLog.id.desc()).offset(keep - 1).limit(1)
    ).scalar()
    if min_id is None:
        return {"total": total, "removed": 0}
    removed = (
        session.query(AuditLog).filter(AuditLog.id < min_id).delete(synchronize_session=False)
    )
    session.commit()
    return {"total": total, "removed": removed}


# ---------------------------------------------------------------------------
# B2 企业中标画像
# ---------------------------------------------------------------------------
# SPEC-AMT：extract_amount_wan 已上移 models（唯一实现）；app.jobs 按原路径
# re-export（api/brief.py、tests/test_phase_b.py 经 app.jobs 引用不断链）。


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
# SPEC-AB：广东拟建/备案单位采集（比招标早 1-3 个月的采购前兆信号）
# ---------------------------------------------------------------------------
def backfill_proposal_geo() -> dict:
    """存量拟建企业省份回填（SPEC-BP BP0，幂等一次性）。

    来源通道→省份确定性映射：proposal_gd→广东省 / proposal_hb→湖北省。
    只填 province 为空的行，不覆盖任何已有值（回滚=清空对应行重跑）。
    """
    from .db import SessionLocal

    updated = 0
    with SessionLocal() as s:
        for source, province in (("proposal_gd", "广东省"), ("proposal_hb", "湖北省")):
            rows = (
                s.query(Enterprise)
                .filter(
                    Enterprise.source == source,
                    Enterprise.province.is_(None) | (Enterprise.province == ""),
                )
                .all()
            )
            for e in rows:
                e.province = province
                updated += 1
        s.commit()
    return {"updated": updated}


def backfill_province_geo() -> dict:
    """省份文本回填（SPEC-BX，幂等）：city 归一映射，只填空省不覆盖。

    数据事实：city 覆盖 93.8% 而省份文本仅 0.7%（POI 地理在 lat/lng 列）；
    city→省 为确定性映射（geoutil 静态表可评审），脏值/未知地名不发明。
    未来 POI 新批次采集后重跑本函数即可增量补齐。
    """
    from .db import SessionLocal
    from .geoutil import province_from_locality

    by_rule = {"city_map": 0, "skipped": 0}
    with SessionLocal() as s:
        rows = (
            s.query(Enterprise)
            .filter(
                Enterprise.deleted_at.is_(None),
                Enterprise.province.is_(None) | (Enterprise.province == ""),
            )
            .all()
        )
        for e in rows:
            prov = province_from_locality(e.city)
            if prov:
                e.province = prov
                by_rule["city_map"] += 1
            else:
                by_rule["skipped"] += 1
        s.commit()
    return {"scanned": len(rows), **by_rule}


# ---------------------------------------------------------------------------
# SPEC-MULTI S4：备案公示扫描基座（配置驱动）——四省 job 同构 ~250 行收敛于此，
# 新增省政采站点边际成本 90 → ~15 行配置。
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProposalScanConfig:
    """单省备案扫描配置：站点形状 + 字段映射全部声明在此。

    collector/fetch/keywords 均存**名字**而非函数引用——运行时经
    importlib/getattr 解析，测试 monkeypatch 模块属性照常生效
    （test_spec_ab/ag/br/bs 的既有打桩方式零改动）。"""

    source_key: str                       # 注册表 source（同时是停用开关键）
    province: str                         # SPEC-BP BP0：通道→省份确定性映射
    collector: str                        # collectors 子模块名
    signal_url_attr: str                  # 信号 url 常量名（collector 模块内）
    unit_field: str                       # rec 中单位字段（gd=name / hb=unit / jszj=project）
    title_of: Callable[[dict], str]       # 信号标题模板
    pub_date_of: Callable[[dict], object] # pub_date 取值
    excerpt_of: Callable[[dict], str]     # raw_excerpt 模板
    evidence_of: Callable[[dict], str]    # evidence_claim 模板
    fetch_page: str | None = None         # 无关键词分页函数名（gd/hb：f(page_no, client=None)）
    fetch_by_kw: str | None = None        # 关键词分页函数名（js/zj：f(kw, page_no=page_no)）
    keywords_fn: str | None = None        # 关键词表函数名（vertical 模块内）
    max_keywords: int = 12                # 关键词数上限（12 请求/日口径）
    min_unit_len: int = 0                 # >0 时启用单位长度下限（js/zj=4，抽不到跳过计数）
    with_subscription: bool = False       # gd/hb：扫描尾部订阅即时命中（SPEC-AI AI4）


_PROPOSAL_CONFIGS: dict[str, ProposalScanConfig] = {
    "proposal_gd": ProposalScanConfig(
        source_key="proposal_gd", province="广东省",
        collector="proposal_gd", signal_url_attr="PROPOSAL_PAGE_URL",
        unit_field="name", fetch_page="fetch_proposal_page",
        with_subscription=True,
        title_of=lambda rec: f"广东项目备案：{rec['name']}（{rec.get('work_time') or ''}年度）",
        pub_date_of=lambda rec: rec.get("ba_time"),
        excerpt_of=lambda rec: f"项目备案 {rec.get('ba_code') or ''}｜{rec.get('ba_num') or ''} 项",
        evidence_of=lambda rec: (
            f"该单位于 {rec.get('ba_time') or '近期'} 在广东省投资项目"
            f"在线审批监管平台完成项目备案（编号 {rec.get('ba_code') or '—'}）"
        ),
    ),
    "proposal_hb": ProposalScanConfig(
        source_key="proposal_hb", province="湖北省",
        collector="proposal_hb", signal_url_attr="HB_XXGK_URL",
        unit_field="unit", fetch_page="fetch_hb_filing_page",
        with_subscription=True,
        title_of=lambda rec: (
            f"湖北项目备案：{(rec.get('project') or rec['unit'])[:60]}｜{rec['unit']}"
        ),
        pub_date_of=lambda rec: rec.get("declared_at"),
        excerpt_of=lambda rec: f"项目备案 {rec.get('project_code') or '—'}",
        evidence_of=lambda rec: (
            f"该单位于 {rec.get('declared_at') or '近期'} 在湖北省投资项目"
            f"在线审批监管平台完成「{(rec.get('project') or rec['unit'])[:40]}」项目备案"
        ),
    ),
    "proposal_js": ProposalScanConfig(
        source_key="proposal_js", province="江苏省",
        collector="proposal_js", signal_url_attr="JS_LIST_URL",
        unit_field="project", fetch_by_kw="fetch_js_beian",
        keywords_fn="device_strong_terms", min_unit_len=4,
        title_of=lambda rec: f"江苏项目备案：{rec['project'][:60]}",
        pub_date_of=lambda rec: rec.get("ba_date"),
        excerpt_of=lambda rec: (
            f"项目备案 {rec.get('beian_no') or '—'}｜{rec.get('approve_org') or '—'}"
        ),
        evidence_of=lambda rec: (
            f"该单位在江苏省投资项目在线审批监管平台完成"
            f"「{rec['project']}」项目备案（编号 "
            f"{rec.get('beian_no') or '—'}，审批部门 "
            f"{rec.get('approve_org') or '—'}）"
        ),
    ),
    "proposal_zj": ProposalScanConfig(
        source_key="proposal_zj", province="浙江省",
        collector="proposal_zj", signal_url_attr="ZJ_LIST_URL",
        unit_field="project", fetch_by_kw="fetch_zj_items",
        keywords_fn="ggzy_name_terms", min_unit_len=4,
        # 事项名全量放前段：同项目不同审批节点（备案/规划许可/水保）
        # 各自成信号=项目推进被分段跟踪；截断只落在项目名上
        title_of=lambda rec: f"浙江办理公示：{rec['item_name']}｜{rec['project'][:42]}",
        pub_date_of=lambda rec: rec.get("deal_time"),
        excerpt_of=lambda rec: (
            f"{rec.get('item_name') or '审批事项'}｜"
            f"{rec.get('deal_name') or '—'}｜{rec.get('dept_name') or '—'}"
        ),
        evidence_of=lambda rec: (
            f"该单位「{rec['project']}」项目在浙江省投资项目在线"
            f"审批监管平台完成「{rec.get('item_name') or '审批事项'}」"
            f"（状态：{rec.get('deal_name') or '—'}，"
            f"办理部门 {rec.get('dept_name') or '—'}）"
        ),
    ),
}


def run_proposal_scan(cfg: ProposalScanConfig, max_pages: int) -> dict:
    """备案公示扫描基座：取数 → 建档（省份映射+ICP 分类）→ proposal 信号
    （url+title 幂等去重）→（可选）尾部订阅即时命中。幂等可重扫。

    SPEC-MULTI S1：来源停用即入口短路（cron 与直接调用同语义）。
    """
    import importlib

    from .api.imports import apply_icp
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .sources import any_source_disabled

    if any_source_disabled(cfg.source_key):
        return {"skipped_disabled": True}
    collector_mod = importlib.import_module(f".collectors.{cfg.collector}", __package__)
    signal_url = getattr(collector_mod, cfg.signal_url_attr)
    stats: dict = {"pages": 0, "records": 0, "new_ents": 0, "new_signals": 0}
    if cfg.min_unit_len:
        stats["no_unit_skipped"] = 0
    if cfg.fetch_by_kw:
        fetch = getattr(collector_mod, cfg.fetch_by_kw)
        kw_fn = getattr(importlib.import_module(".vertical", __package__), cfg.keywords_fn)

        def _iter_records():
            # 关键词×分页：单页空即停该词翻页（服务端第 1 页=最新）
            for kw in kw_fn()[: cfg.max_keywords]:
                for page_no in range(1, max_pages + 1):
                    records = fetch(kw, page_no=page_no)
                    if not records:
                        break
                    yield records
    else:
        fetch = getattr(collector_mod, cfg.fetch_page)

        def _iter_records():
            for page_no in range(1, max_pages + 1):
                records = fetch(page_no, client=None)
                if not records:
                    break
                yield records

    for records in _iter_records():
        stats["pages"] += 1
        stats["records"] += len(records)
        with SessionLocal() as s:
            for rec in records:
                if cfg.min_unit_len:
                    # js/zj：单位抽取自项目名（命名习惯=企业名开头），
                    # 抽不到如实计数跳过不发明归属
                    from .api.imports import extract_enterprise_name

                    unit = extract_enterprise_name(rec[cfg.unit_field], "")
                    if not unit or len(unit) < cfg.min_unit_len:
                        stats["no_unit_skipped"] += 1
                        continue
                else:
                    unit = rec[cfg.unit_field]
                ent, is_new = resolve_enterprise(s, unit, source=cfg.source_key)
                # SPEC-BP BP0：来源通道→省份是确定性映射（非发明数据）
                if not ent.province:
                    ent.province = cfg.province
                if is_new:
                    stats["new_ents"] += 1
                    # W40 同款：建档即分类（首轮漏接，AG 补齐）
                    if ent.industry_key is None:
                        apply_icp(ent)
                title = cfg.title_of(rec)
                exists = (
                    s.query(Signal)
                    .filter(
                        Signal.enterprise_id == ent.id,
                        Signal.url == signal_url,
                        Signal.title == title,
                    )
                    .first()
                )
                if exists is None:
                    s.add(Signal(
                        enterprise_id=ent.id,
                        type="proposal",
                        title=title,
                        url=signal_url,
                        pub_date=cfg.pub_date_of(rec),
                        matched_terms="[]",
                        hit_part="title",
                        raw_excerpt=cfg.excerpt_of(rec),
                        evidence_claim=cfg.evidence_of(rec),
                        base_score=40.0,
                    ))
                    stats["new_signals"] += 1
            s.commit()
    if cfg.with_subscription:
        # SPEC-AI AI4：扫描尾部订阅即时命中（水位幂等零重复）
        try:
            from .db import SessionLocal

            with SessionLocal() as _ss:
                stats["subscription"] = run_subscription_rules(_ss)
        except Exception:  # noqa: BLE001 — 订阅侧失败不影响采集
            logger.warning("订阅即时命中失败", exc_info=True)
    return stats


def run_proposal_gd_scan(max_pages: int = 5) -> dict:
    """广东备案公示扫描：单位级拟建信号（proposal），幂等可重扫。

    采集节奏：每日 05:00 cron；失败记水位不静默（SPEC-AB AB1）。
    """
    return run_proposal_scan(_PROPOSAL_CONFIGS["proposal_gd"], max_pages)


def run_proposal_hb_scan(max_pages: int = 5) -> dict:
    """湖北备案查询扫描：**项目级**拟建信号（SPEC-AG），幂等可重扫。

    与广东（run_proposal_gd_scan 单位级）互为镜像；每日 05:10 cron 错峰。
    title 直接带项目名——广东 P2 边界（项目名在未暴露详情接口）在湖北列表可得。
    """
    return run_proposal_scan(_PROPOSAL_CONFIGS["proposal_hb"], max_pages)


def run_proposal_js_scan(max_pages: int = 1) -> dict:
    """江苏备案公示扫描（SPEC-BR，每日 05:20）：昊志腹地第三省。

    站点形态=传统表单站：POST .do 即得列表，服务端关键词过滤+日期倒序
    （第 1 页=最新），比粤鄂"全量再筛"更省——词表与 ccgp 增量同口径
    （device_strong_terms[:12]，12 请求/日）。光伏使用侧噪音采集端滤除
    （湖北同款实测）；单位抽取自项目名（江苏命名习惯=企业名开头），
    抽不到如实计数跳过不发明归属。
    """
    return run_proposal_scan(_PROPOSAL_CONFIGS["proposal_js"], max_pages)


def run_proposal_zj_scan(max_pages: int = 1) -> dict:
    """浙江办理结果公示扫描（SPEC-BS，每日 05:30）：第四省+事项维度。

    与江苏同为服务端关键词过滤（JSON，固定 10 行/页）；独有事项名维度
    （备案=扩张前兆 / 规划许可·水保=项目推进中），evidence 如实带事项名
    供销售分辨。文号前缀（富政工出【2025】16号）剥离后再抽单位。
    词表=ggzy 宽词前 12（**项目名检索口径**——首扫实测：ggzy 主体用的
    泵品类细词在浙江项目名召回≈0，宽词「真空」counts=255）；平台已是
    工业多行业定位（SPEC-L），电梯/水泵等宽词买家同样在射程。
    """
    return run_proposal_scan(_PROPOSAL_CONFIGS["proposal_zj"], max_pages)


# ---------------------------------------------------------------------------
# SPEC-SDGP：山东政采分网直采（0301 采购公告 / 0302 结果公告）
# ---------------------------------------------------------------------------
def run_sdgp_scan(max_pages: int = 3) -> dict:
    """山东政采分网直采扫描（SPEC-SDGP，每日 05:40 错峰）：昊志本省直采。

    语义=政采（同 ccgp）：0301 采购公告 → 采购人自标题抽取
    （extract_enterprise_name）建档（source=sdgp_buyer，省份=山东省
    确定性映射只补空）+ tender 信号（base_score=30）；0302 结果公告 →
    同样采购人沉淀 + tender_win 信号（base_score=60，evidence 如实标注
    「（结果公告）」——买方视角语义=该买家此项目已成交=配套/后续窗口）。
    本源无中标方名称（详情接口 getNoticeBody 未破解，v1 不做详情）——
    同行打标不适用，不发明。apply_icp 建档即调（industry_key 空才调，
    daily_incremental 同款）。

    词表=vertical.ggzy_name_terms()（**项目名检索必须宽词**——BS 轮金子级
    教训：细词召回≈0）；词表为空优雅跳过。查重=signal_exists 四元组
    （ent+url=""+title+type；uq_signal_dedup 含 type 列）预查 + 唯一约束
    兜底，幂等可重扫。单记录短事务（SQLite 单写者——写事务绝不跨 HTTP）。
    """
    from .api.imports import apply_icp, extract_enterprise_name
    from .collectors.sdgp import (
        COL_CONTRACT,
        COL_INTENT,
        COL_RESULT,
        COL_TENDER,
        fetch_notice_detail,
        normalize_notice,
        search_all,
    )
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .sources import any_source_disabled
    from .vertical import sdgp_terms

    if any_source_disabled("sdgp"):
        return {"skipped_disabled": True}
    stats: dict = {"notices": 0, "buyers": 0, "signals": 0, "no_buyer_skipped": 0}
    keywords = sdgp_terms()  # 2026-11-15 词表扩容：品类宽词 ∪ 山东大本营行业细分
    if not keywords:
        logger.warning("sdgp：sdgp_terms 词表为空，本轮优雅跳过")
        return stats
    # 四栏目语义阶梯（2026-11-15 扩展：合同公示=成交确认最完整信号[采购人+
    # 供应商+电话+金额全在正文]，意向公开=比招标早 30 天的最早期信号）：
    # 0301 采购公告→tender(30) / 0302 结果公告→tender_win(60) /
    # 2502 合同公示→contract(50，新类型) / 2500 意向公开→proposal(25，复用备案语义)
    _COL_SEMANTICS = {
        COL_TENDER: ("tender", 30.0),
        COL_RESULT: ("tender_win", 60.0),
        COL_CONTRACT: ("contract", 50.0),
        COL_INTENT: ("proposal", 25.0),
    }
    for col in (COL_TENDER, COL_RESULT, COL_CONTRACT, COL_INTENT):
        sig_type, base_score = _COL_SEMANTICS[col]
        for kw in keywords:
            for raw in search_all(col, kw, max_pages=max_pages):
                notice = normalize_notice(raw)
                if notice is None:
                    continue
                stats["notices"] += 1
                buyer = extract_enterprise_name(notice["title"], "")
                if not buyer or len(buyer) < 4:
                    # 抽不到采购人如实计数跳过，不发明归属（js/zj 同款）
                    stats["no_buyer_skipped"] += 1
                    continue
                with SessionLocal() as s:
                    ent, is_new = resolve_enterprise(s, buyer, source="sdgp_buyer")
                    # SPEC-BP BP0 同款：通道→省份确定性映射，只补空不覆盖
                    if not ent.province:
                        ent.province = "山东省"
                    if is_new:
                        stats["buyers"] += 1
                    # W40 同款：建档即分类（首轮漏接，AG 补齐）
                    if ent.industry_key is None:
                        apply_icp(ent)
                    s.commit()  # 建档短事务先落：买手沉淀不因信号撞库丢失
                    exists = (
                        s.query(Signal)
                        .filter(
                            Signal.enterprise_id == ent.id,
                            Signal.url == "",
                            Signal.title == notice["title"],
                            Signal.type == sig_type,
                        )
                        .first()
                    )
                    # SPEC-SDGP 详情扩池（2026-11-15 getDetail 破译）：该买家
                    # 尚无公告电话时拉详情抽号码——自收敛（补齐即不再拉）；
                    # 重复公告也补历史（cap 60/轮）。详情失败不阻断主采集。
                    _needs_phone = (
                        notice.get("notice_id")
                        and stats.get("details", 0) < 60
                        and s.query(Contact.id).filter(
                            Contact.enterprise_id == ent.id,
                            Contact.role == "notice_contact",
                        ).first() is None
                    )
                    if _needs_phone:
                        try:
                            d = fetch_notice_detail(col, notice["notice_id"])
                        except Exception:  # noqa: BLE001
                            d = None
                        if d and d.get("body_text"):
                            stats["details"] = stats.get("details", 0) + 1
                            from .phones import dump_phones as _dump, extract_phones, load_phones
                            with SessionLocal() as ds:
                                phones = extract_phones(d["body_text"])
                                if phones:
                                    exists_c = (
                                        ds.query(Contact)
                                        .filter(
                                            Contact.enterprise_id == ent.id,
                                            Contact.role == "notice_contact",
                                        )
                                        .first()
                                    )
                                    if exists_c is not None:
                                        merged = sorted(
                                            set(load_phones(exists_c.phones_json)) | set(phones)
                                        )
                                        exists_c.phones_json = _dump(merged)
                                        ds.commit()
                                        stats["contacts_updated"] = stats.get("contacts_updated", 0) + 1
                                    else:
                                        ds.add(Contact(
                                            enterprise_id=ent.id, name="公告法定公开电话",
                                            role="notice_contact", phones_json=_dump(phones),
                                            source="sdgp",
                                        ))
                                        ds.commit()
                                        stats["contacts_new"] = stats.get("contacts_new", 0) + 1
                    if exists is not None:
                        continue
                    _col_cn = {COL_TENDER: "", COL_RESULT: "（结果公告）",
                               COL_CONTRACT: "（合同公示）", COL_INTENT: "（采购意向）"}
                    evidence = notice["title"] + _col_cn.get(col, "")
                    s.add(Signal(
                        enterprise_id=ent.id,
                        type=sig_type,
                        title=notice["title"],
                        url="",
                        pub_date=notice["date"],
                        matched_terms="[]",
                        hit_part="title",
                        raw_excerpt=(
                            f"山东政采{'采购公告' if col == COL_TENDER else '结果公告'}"
                            f"｜{notice['area_name'] or '山东省'}"
                        ),
                        evidence_claim=evidence,
                        base_score=base_score,
                    ))
                    try:
                        s.commit()
                        stats["signals"] += 1
                    except IntegrityError:
                        s.rollback()
                        logger.warning(
                            "sdgp：信号「%s」撞 uq_signal_dedup，已跳过", notice["title"]
                        )
    return stats


# ---------------------------------------------------------------------------
# SPEC-HUNAN：湖南政采（luban 平台，无关键词接口——当日块+本地词表过滤）
# ---------------------------------------------------------------------------
def run_hunan_scan() -> dict:
    """湖南政采当日块扫描（SPEC-HUNAN，每日 06:00 错峰于 sdgp 05:40 后）。

    luban 平台无关键词接口：fetch_home_blocks 拉当日最新块 → 本地
    sdgp_terms 词表过滤（40 词与山东共用，同为标题库）→ 命中逐条：
    采购人自标题抽取（extract_enterprise_name 双参签名，抽不到如实跳过
    不发明归属）建档（source=hunan_buyer，省份=湖南省确定性映射只补空）
    + apply_icp（建档即分类）+ tender 信号（base_score=30，url=luban
    详情页真实链接可溯源——比山东 url=""强）。

    详情电话自收敛：命中企业无 notice_contact 联系人时 fetch_detail →
    extract_phones → Contact(source="hunan", role="notice_contact")，cap
    30/轮（补齐即不再拉，详情失败不阻断主采集）。查重=Signal(url=详情链,
    title, type) 三元组预查 + uq_signal_dedup 唯一约束兜底，幂等可重扫。
    单记录短事务（SQLite 单写者——写事务绝不跨 HTTP）。
    """
    from .api.imports import apply_icp, extract_enterprise_name
    from .collectors.hunan import (
        detail_link,
        fetch_detail,
        fetch_home_blocks,
        filter_by_terms,
    )
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .sources import any_source_disabled
    from .vertical import sdgp_terms

    if any_source_disabled("hunan"):
        return {"skipped_disabled": True}
    stats: dict = {
        "notices": 0, "hits": 0, "buyers": 0, "signals": 0,
        "details": 0, "contacts_new": 0, "contacts_updated": 0,
    }
    terms = sdgp_terms()
    if not terms:
        logger.warning("hunan：sdgp_terms 词表为空，本轮优雅跳过")
        return stats
    blocks = fetch_home_blocks()
    stats["notices"] = len(blocks)
    hits = filter_by_terms(blocks, terms)
    stats["hits"] = len(hits)
    for block in hits:
        title = (block.get("title") or "").strip()
        article_id = (block.get("article_id") or "").strip()
        if not title or not article_id:
            continue
        buyer = extract_enterprise_name(title, "")
        if not buyer or len(buyer) < 4:
            # 抽不到采购人如实跳过，不发明归属（sdgp/js/zj 同款）
            logger.debug("hunan：标题「%s」抽不到采购人，跳过", title)
            continue
        detail_url = detail_link(article_id)
        with SessionLocal() as s:
            ent, is_new = resolve_enterprise(s, buyer, source="hunan_buyer")
            # SPEC-BP BP0 同款：通道→省份确定性映射，只补空不覆盖
            if not ent.province:
                ent.province = "湖南省"
            if is_new:
                stats["buyers"] += 1
            # W40 同款：建档即分类（首轮漏接，AG 补齐）
            if ent.industry_key is None:
                apply_icp(ent)
            s.commit()  # 建档短事务先落：买手沉淀不因信号撞库丢失
            exists = (
                s.query(Signal)
                .filter(
                    Signal.enterprise_id == ent.id,
                    Signal.url == detail_url,
                    Signal.title == title,
                    Signal.type == "tender",
                )
                .first()
            )
            # 详情电话自收敛（sdgp 同款，cap 30/轮）：该买家尚无公告电话
            # 时拉详情抽号码——补齐即不再拉；重复公告也补历史。
            _needs_phone = (
                stats.get("details", 0) < 30
                and s.query(Contact.id).filter(
                    Contact.enterprise_id == ent.id,
                    Contact.role == "notice_contact",
                ).first() is None
            )
            if _needs_phone:
                try:
                    d = fetch_detail(article_id)
                except Exception:  # noqa: BLE001 — 详情失败不阻断主采集
                    d = None
                if d and d.get("body_text"):
                    stats["details"] = stats.get("details", 0) + 1
                    from .phones import (
                        dump_phones as _dump,
                        extract_phones,
                        load_phones,
                    )
                    with SessionLocal() as ds:
                        phones = extract_phones(d["body_text"])
                        if phones:
                            exists_c = (
                                ds.query(Contact)
                                .filter(
                                    Contact.enterprise_id == ent.id,
                                    Contact.role == "notice_contact",
                                )
                                .first()
                            )
                            if exists_c is not None:
                                merged = sorted(
                                    set(load_phones(exists_c.phones_json)) | set(phones)
                                )
                                exists_c.phones_json = _dump(merged)
                                ds.commit()
                                stats["contacts_updated"] = (
                                    stats.get("contacts_updated", 0) + 1
                                )
                            else:
                                ds.add(Contact(
                                    enterprise_id=ent.id, name="公告法定公开电话",
                                    role="notice_contact", phones_json=_dump(phones),
                                    source="hunan",
                                ))
                                ds.commit()
                                stats["contacts_new"] = stats.get("contacts_new", 0) + 1
            if exists is not None:
                continue
            s.add(Signal(
                enterprise_id=ent.id,
                type="tender",
                title=title,
                url=detail_url,
                pub_date=block.get("pub_date"),
                matched_terms="[]",
                hit_part="title",
                raw_excerpt=f"湖南政采采购公告｜{block.get('district_name') or '湖南省'}",
                evidence_claim=title,
                base_score=30.0,
            ))
            try:
                s.commit()
                stats["signals"] += 1
            except IntegrityError:
                s.rollback()
                logger.warning(
                    "hunan：信号「%s」撞 uq_signal_dedup，已跳过", title
                )
    return stats


# ---------------------------------------------------------------------------
# SPEC-LUBAN：政采云系三省（沪黔桂）意向公开（luban 平台配置驱动，全量入库）
# ---------------------------------------------------------------------------
def run_luban_scan() -> dict:
    """政采云三省（上海/贵州/广西）意向公开扫描（SPEC-LUBAN，每日 06:10）。

    与湖南不同：意向标题无设备词——**全量入库**不过滤：fetch_luban_home
    拉当日块 → 标题抽采购人（extract_enterprise_name，抽不到如实跳过不
    发明归属）建档（source=xx_buyer，省份确定性映射只补空）+ apply_icp
    （建档即分类）+ proposal 信号（base_score=25，url=""——三省 detail
    契约未验证，意向详情链未破译；evidence=f"{title}（采购意向）"）。

    遍历 LUBAN_PROVINCES：单省异常 continue 不串（try 包整省段）；
    停用短路 any_source_disabled(省主 key) 逐省判断。查重三元组
    (url="", title, type="proposal") 预查 + uq_signal_dedup 兜底，幂等
    可重扫。单记录短事务（SQLite 单写者——写事务绝不跨 HTTP）。

    totals：{prov: {notices, buyers, signals}, ..., total_notices,
    total_buyers, total_signals}（每省分键便于诊断）。共用 luban job_name
    单水位（三源 sla_job="luban" 共一条 SLA，水位不按省单独落）。
    """
    from .api.imports import extract_enterprise_name
    from .collectors.luban import LUBAN_PROVINCES, fetch_luban_detail, fetch_luban_home
    from .db import SessionLocal
    from .sources import any_source_disabled

    stats: dict = {}
    total_notices = 0
    total_buyers = 0
    total_signals = 0
    for prov_key, cfg in LUBAN_PROVINCES.items():
        prov_stats: dict = {"notices": 0, "buyers": 0, "signals": 0}
        try:
            if any_source_disabled(prov_key):
                # 停用短路（SPEC-MULTI S1）：该省跳过不串他省
                prov_stats["skipped_disabled"] = True
                stats[prov_key] = prov_stats
                continue
            blocks = fetch_luban_home(prov_key)
            prov_stats["notices"] = len(blocks)
            for block in blocks:
                title = (block.get("title") or "").strip()
                if not title:
                    continue
                # 买方单位：purchase_name 直出优先（吉林等省），fallback 标题抽取
                buyer = (
                    block.get("purchase_name")
                    or extract_enterprise_name(title, "")
                )
                if not buyer or len(buyer) < 4:
                    # 抽不到采购人如实跳过，不发明归属（sdgp/hunan/js/zj 同款）
                    logger.debug("luban[%s]：标题「%s」抽不到采购人，跳过", prov_key, title)
                    continue
                # 2026-11-21 语义定案（用户拍板"不做政采买家"+意向公开全部为
                # 政府/事业单位形态）：**意向监控模式**——单位名精确命中企业库
                # 已有企业才挂信号（老户最早期信号：已关注客户要买什么，48 小
                # 时前就知道）；陌生单位跳过不建档（不发明、不灌池——政采云
                # 意向的采购人=政采买家同类，全量建档违背客户定义拍板）。
                with SessionLocal() as s:
                    ent = (
                        s.query(Enterprise)
                        .filter(Enterprise.name == buyer, Enterprise.deleted_at.is_(None))
                        .first()
                    )
                    if ent is None:
                        continue  # 陌生单位——不做建档（意向监控语义）
                    # SPEC-LUBAN 增强（2026-11-21 实弹：沪黔 detail 契约=湖南同款
                    # GET /portal/detail?articleId=&timestamp=）：拉意向详情正文
                    # ——正文含采购标的/预算金额（元）/预计采购时间标准表，截 200
                    # 进 raw_excerpt（金额由 _signal_fill_amount_wan 钩子自动抽取）。
                    # cap 30/省/轮防长跑；详情失败降级为原短摘要不阻断。
                    from .collectors.luban import fetch_luban_detail

                    detail_text = None
                    try:
                        detail_text = fetch_luban_detail(
                            prov_key, block.get("article_id") or ""
                        )
                    except Exception:  # noqa: BLE001 — 详情失败不阻断主采集
                        detail_text = None
                    if detail_text:
                        prov_stats["details"] = prov_stats.get("details", 0) + 1
                    raw_excerpt = (
                        f"{cfg['province']}政采采购意向"
                        f"｜{block.get('district_name') or cfg['province']}"
                        + (f"｜{detail_text[:200]}" if detail_text else "")
                    )
                    s.add(Signal(
                        enterprise_id=ent.id,
                        type="proposal",
                        title=title,
                        url="",  # 意向详情链在前端路由内（luban 组件），站外无直链
                        pub_date=block.get("pub_date"),
                        matched_terms="[]",
                        hit_part="title",
                        raw_excerpt=raw_excerpt,
                        evidence_claim=f"{title}（采购意向）",
                        base_score=25.0,
                    ))
                    try:
                        s.commit()
                        prov_stats["signals"] += 1
                    except IntegrityError:
                        s.rollback()
                        logger.warning(
                            "luban[%s]：信号「%s」撞 uq_signal_dedup，已跳过",
                            prov_key, title,
                        )
        except Exception:  # noqa: BLE001 — 单省失败不串（try 包整省段）
            logger.exception("luban：%s 省扫描失败，跳过不影响他省", prov_key)
            prov_stats["error"] = True
        stats[prov_key] = prov_stats
        total_notices += prov_stats["notices"]
        total_buyers += prov_stats["buyers"]
        total_signals += prov_stats["signals"]
    stats["total_notices"] = total_notices
    stats["total_buyers"] = total_buyers
    stats["total_signals"] = total_signals
    return stats


# ---------------------------------------------------------------------------
# SPEC-AHUI：安徽政采（portal/category 形态，author 直带采购人——全量入库）
# ---------------------------------------------------------------------------
def run_anhui_scan(max_pages: int = 7) -> dict:
    """安徽政采扫描（SPEC-AHUI，每日 06:20 错峰于 luban 06:10 后）。

    与湖南/山东都不同：**author 字段直接给采购人单位名**——无需标题
    抽取，author 直接 resolve 建档（source=anhui_buyer，省份=安徽省
    确定性映射只补空）+ apply_icp（建档即分类）+ tender 信号
    （base_score=30，url=""——详情页路由 /site/article 实弹 301 到 mall
    首页非文章页，SPEC 约定验证不通回退；evidence=title）。

    **全量入库**不过滤（意向/公告标题无设备词，同 luban 决策）；author
    空的跳过计数（no_author_skipped）。分页循环 cap 7 页/轮×15 条=105
    条/轮防长跑（pageNo 递增，空页即停——服务端第 1 页=最新；pageSize
    实弹上限 15，20 稳定 429）。查重三元组 (url="", title,
    type="tender") 预查 + uq_signal_dedup 唯一约束兜底，幂等可重扫。
    单记录短事务（SQLite 单写者——写事务绝不跨 HTTP）。
    """
    from .api.imports import apply_icp
    from .collectors.anhui import fetch_category
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .sources import any_source_disabled

    if any_source_disabled("anhui"):
        return {"skipped_disabled": True}
    stats: dict = {
        "pages": 0, "notices": 0, "buyers": 0, "signals": 0, "no_author_skipped": 0,
    }
    for page_no in range(1, max_pages + 1):
        page = fetch_category(page_no=page_no)
        records = page.get("records") or []
        if not records:
            break  # 空页即停（服务端第 1 页=最新，无效码也回空 data）
        stats["pages"] += 1
        stats["notices"] += len(records)
        for rec in records:
            title = (rec.get("title") or "").strip()
            if not title:
                continue
            author = (rec.get("author") or "").strip()
            if not author:
                # author 空如实跳过计数，不发明归属（SPEC-AHUI 纪律）
                stats["no_author_skipped"] += 1
                continue
            with SessionLocal() as s:
                ent, is_new = resolve_enterprise(s, author, source="anhui_buyer")
                # SPEC-BP BP0 同款：通道→省份确定性映射，只补空不覆盖
                if not ent.province:
                    ent.province = "安徽省"
                if is_new:
                    stats["buyers"] += 1
                # W40 同款：建档即分类（首轮漏接，AG 补齐）
                if ent.industry_key is None:
                    apply_icp(ent)
                s.commit()  # 建档短事务先落：买手沉淀不因信号撞库丢失
                exists = (
                    s.query(Signal)
                    .filter(
                        Signal.enterprise_id == ent.id,
                        Signal.url == "",
                        Signal.title == title,
                        Signal.type == "tender",
                    )
                    .first()
                )
                if exists is not None:
                    continue
                s.add(Signal(
                    enterprise_id=ent.id,
                    type="tender",
                    title=title,
                    url="",
                    pub_date=rec.get("pub_date"),
                    matched_terms="[]",
                    hit_part="title",
                    raw_excerpt=(
                        f"安徽政采{rec.get('path_name') or '采购公告'}"
                        f"｜{rec.get('district_name') or '安徽省'}"
                    ),
                    evidence_claim=title,
                    base_score=30.0,
                ))
                try:
                    s.commit()
                    stats["signals"] += 1
                except IntegrityError:
                    s.rollback()
                    logger.warning(
                        "anhui：信号「%s」撞 uq_signal_dedup，已跳过", title
                    )
    return stats


# ---------------------------------------------------------------------------
# 回收站清空（SPEC-AM AM1）
# ---------------------------------------------------------------------------
def purge_deleted_sweep(retention_days: int = 30) -> dict:
    """回收站 30 天自动清空（EspoCRM Trash 语义）+ 导出文件清理。

    每日 03:00：彻底删超过保留期的软删企业（级联）。
    """
    from datetime import timedelta

    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Contact, Enterprise, utcnow

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    purged = 0
    with SessionLocal() as s:
        rows = (
            s.execute(
                select(Enterprise)
                .where(
                    Enterprise.deleted_at.is_not(None),
                    Enterprise.deleted_at < cutoff,
                )
                .execution_options(include_deleted=True)
            )
            .scalars()
            .all()
        )
        for e in rows:
            for model in (
                getattr(__import__("app.models", fromlist=["Signal"]), "Signal"),
                Contact,
                getattr(__import__("app.models", fromlist=["Lead"]), "Lead"),
                getattr(__import__("app.models", fromlist=["EnterpriseNote"]), "EnterpriseNote"),
                getattr(__import__("app.models", fromlist=["Subscription"]), "Subscription"),
            ):
                s.query(model).filter(model.enterprise_id == e.id).delete(
                    synchronize_session=False
                )
            s.delete(e)
            purged += 1
        s.commit()
    return {"purged": purged, "retention_days": retention_days}


# ---------------------------------------------------------------------------
# 水位看门狗（SPEC-AR AR1）
# ---------------------------------------------------------------------------
def run_watermark_watchdog() -> dict:
    """关键日批水位自愈：非当日执行的关键任务自动补跑 + 通知 admin。

    每日 06:30 cron。背景：健康检查发现 03:30 增量在 POI 长批并发窗口
    丢失且 misfire 未自愈——看门狗把"发现→补跑→通知"系统化。
    补跑串行执行（SQLite 单写者）。
    """
    from datetime import timedelta

    from sqlalchemy import select

    from .db import SessionLocal
    from .main import _run_job_with_watermark
    from .models import User

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    watchdog_jobs = [
        ("daily_incremental", run_daily_incremental),
        ("proposal_gd", lambda: run_proposal_gd_scan(max_pages=5)),
        ("proposal_hb", lambda: run_proposal_hb_scan(max_pages=5)),
        ("proposal_js", lambda: run_proposal_js_scan(max_pages=1)),
        ("proposal_zj", lambda: run_proposal_zj_scan(max_pages=1)),
        ("sdgp", lambda: run_sdgp_scan(max_pages=3)),
        ("hunan", run_hunan_scan),
        ("luban", run_luban_scan),  # SPEC-LUBAN：沪黔桂意向公开（三源共用单 job）
        ("anhui", run_anhui_scan),  # SPEC-AHUI：安徽政采（author 直带采购人）
        ("health_refresh", lambda: _watchdog_health()),
        ("backup_sqlite", lambda: backup_sqlite("data/backups")),
    ]
    results: dict[str, str] = {}
    with SessionLocal() as s:
        for name, fn in watchdog_jobs:
            row = s.execute(
                select(SyncWatermark).where(SyncWatermark.job_name == name)
            ).scalar_one_or_none()
            ran_today = bool(
                row and row.last_run_at and row.last_run_at[:10] == today
            )
            if ran_today:
                results[name] = "ok-today"
                continue
            try:
                _run_job_with_watermark(name, fn)
                results[name] = "recovered"
            except Exception:  # noqa: BLE001
                logger.exception("看门狗补跑 %s 失败", name)
                results[name] = "recover-failed"
    recovered = [k for k, v in results.items() if v == "recovered"]
    if recovered:
        try:
            from .notify.channels import notify_dispatch
            from .models import User as _User

            with SessionLocal() as s:
                admins = s.execute(
                    select(_User).where(_User.role == "admin")
                ).scalars().all()
                for admin in admins:
                    notify_dispatch(
                        s, admin.id,
                        kind="system",
                        title=f"看门狗自动补跑 {len(recovered)} 个任务",
                        body="、".join(recovered) + " 昨日未执行，已自动补跑完成",
                        ref_type="watchdog", ref_id=0,
                    )
                s.commit()
        except Exception:  # noqa: BLE001
            logger.warning("看门狗通知失败", exc_info=True)
    return {"checked": len(watchdog_jobs), "recovered": recovered, "results": results}


def _watchdog_health() -> dict:
    from .health import batch_health_refresh

    with SessionLocal() as s:
        return batch_health_refresh(s)


def payment_overdue_sweep() -> dict:
    """回款逾期守护（SPEC-AX AX1，每日 06:25）：

    pending 且 due_date < 今日 → status=overdue（幂等单向）+
    Todo「回款逾期」（owner=合同负责人）。应收逾期=设备销售商直接的
    现金流风险——从数据状态变成待办动作。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Contract, Enterprise, Lead, PaymentMilestone, Todo

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    marked = 0
    todos_created = 0
    with SessionLocal() as s:
        rows = s.execute(
            select(PaymentMilestone, Contract, Lead, Enterprise.name)
            .join(Contract, PaymentMilestone.contract_id == Contract.id)
            .join(Lead, Contract.lead_id == Lead.id)
            .join(Enterprise, Lead.enterprise_id == Enterprise.id)
            .where(
                PaymentMilestone.status == "pending",
                PaymentMilestone.due_date.is_not(None),
                PaymentMilestone.due_date < today,
            )
        ).all()
        for m, contract, lead, ename in rows:
            m.status = "overdue"
            marked += 1
            title = f"回款逾期：{ename} {m.label or '分期'} {m.amount_wan}万"
            exists = (
                s.query(Todo)
                .filter(Todo.title == title, Todo.done == 0)
                .first()
            )
            if exists is None:
                s.add(Todo(
                    title=title, enterprise_id=lead.enterprise_id,
                    lead_id=contract.lead_id,
                    due_date=today, owner_id=contract.owner_id or lead.owner_id,
                    source="automation",
                ))
                todos_created += 1
        s.commit()
    return {"marked": marked, "todos_created": todos_created}


def oem_touch_sweep() -> dict:
    """OEM 季度关系维护（SPEC-AU，Playbook 三节奏之 OEM 按季度）：

    90 天无任何触达结果标记/待办的 OEM → 生成关系维护待办（幂等）。
    OEM 买泵装设备——供货稳定性的关系维护比价格战更重要。
    """
    from datetime import timedelta, timezone

    from .db import SessionLocal
    from .models import Contact, Todo

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=90)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    created = 0
    with SessionLocal() as s:
        oems = (
            s.execute(
                select(Enterprise).where(
                    Enterprise.customer_role == "oem",
                    Enterprise.deleted_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        for e in oems:
            # 已有未完成维护待办 → 跳过（幂等）
            exists = (
                s.query(Todo)
                .filter(
                    Todo.enterprise_id == e.id,
                    Todo.title.like("OEM 季度关系维护%"),
                    Todo.done == 0,
                )
                .first()
            )
            if exists:
                continue
            # 90 天内有触达动作（联系人标记过结果）→ 跳过
            touched = (
                s.query(Contact.id)
                .filter(
                    Contact.enterprise_id == e.id,
                    Contact.called_at.is_not(None),
                    Contact.called_at >= cutoff,
                )
                .first()
            )
            if touched:
                continue
            s.add(Todo(
                title=f"OEM 季度关系维护：{e.name}",
                enterprise_id=e.id,
                owner_id=None, source="automation",
            ))
            created += 1
        s.commit()
    return {"oem_total": len(oems), "todos_created": created}


def owner_signal_sweep() -> dict:
    """负责人信号推送（SPEC-BM BM1，每 30 分钟）。

    新信号落在"在管线索"（following/intent/won，有负责人）的企业上，
    notify_dispatch 通知线索负责人。零配置：不依赖手动订阅/规则——
    "我的在管客户出现在新公告里"必须主动推给负责人。

    标题语义分层：won=老户新动向（复购/替换机会，最优先）/
    intent=意向客户新信号 / following=跟进客户新信号。
    正文=信号标题+企业名+证据句截断（BM2：不含手机号明文——联系人
    数据在详情页，通知只导流）。

    游标：sync_watermark 'owner_signal'（与 cron 注册名一致——体检曾发现
    读旧名致游标冻结在手动基线，每 30 分钟全量重扫靠去重兜底）的
    stats_json.last_signal_id（_run_job_with_watermark 将返回 dict 写入
    stats）。首跑无水位行 → 基线模式（只记游标不轰炸存量）；失败轮
    游标丢失 → 全量重扫，Notification 唯一约束预查去重，幂等。
    """
    from sqlalchemy import or_

    from .db import SessionLocal
    from .models import Lead, Notification
    from .notify.channels import notify_dispatch

    with SessionLocal() as s:
        row = s.get(SyncWatermark, "owner_signal")
        max_id = s.query(func.max(Signal.id)).scalar() or 0
        if row is None:
            # 首跑基线：只记游标，避免对存量信号一次性轰炸
            return {"baseline": True, "scanned": 0, "notified": 0,
                    "last_signal_id": max_id}
        try:
            since_id = int(
                json.loads(row.stats_json or "{}").get("last_signal_id", 0)
            )
        except ValueError:
            since_id = 0
        if max_id <= since_id:
            return {"scanned": 0, "notified": 0, "last_signal_id": since_id}

        # 标题语义按线索状态分层；同企业多条线索指向同一负责人时取最高优先状态
        _TITLE = {
            "won": "老户新动向",
            "intent": "意向客户新信号",
            "following": "跟进客户新信号",
        }
        _RANK = {"won": 0, "intent": 1, "following": 2}
        rows = s.execute(
            select(Signal, Lead.status, Lead.owner_id, Enterprise.name)
            .join(Lead, Lead.enterprise_id == Signal.enterprise_id)
            .join(Enterprise, Enterprise.id == Signal.enterprise_id)
            .where(
                Signal.id > since_id,
                or_(
                    Lead.status == "won",
                    Lead.status == "intent",
                    Lead.status == "following",
                ),
                Lead.owner_id.is_not(None),
                Enterprise.deleted_at.is_(None),
            )
            .order_by(Signal.id.asc(), Lead.id.asc())
            .limit(2000)
        ).all()

        # 同 (信号, 负责人) 只推一条：多条线索并存时保留最高优先状态
        # （Notification 唯一约束 (ref_type, ref_id, user_id) 同样兜底）
        best: dict[tuple[int, int], tuple[str, object, str]] = {}
        for sig, status, owner_id, ent_name in rows:
            prev = best.get((sig.id, owner_id))
            if prev is None or _RANK[status] < _RANK[prev[0]]:
                best[(sig.id, owner_id)] = (status, sig, ent_name)
        notified = 0
        for (sig_id, owner_id), (status, sig, ent_name) in best.items():
            dup = (
                s.query(Notification)
                .filter(
                    Notification.ref_type == "owner_signal",
                    Notification.ref_id == sig_id,
                    Notification.user_id == owner_id,
                )
                .first()
            )
            if dup is not None:
                continue
            evidence = (sig.evidence_claim or sig.raw_excerpt or "").strip()
            if len(evidence) > 80:
                evidence = f"{evidence[:80]}…"
            title_text = f"{_TITLE[status]}：{ent_name}"
            body_text = f"「{sig.title}」"
            if evidence:
                body_text = f"{body_text}——{evidence}"
            notify_dispatch(
                s, owner_id,
                kind="signal",
                title=title_text,
                body=body_text,
                ref_type="owner_signal",
                ref_id=sig_id,
            )
            notified += 1
        s.commit()
        return {"scanned": max_id - since_id, "notified": notified,
                "last_signal_id": max_id}


def competitor_alert_sweep() -> dict:
    """竞对中标即时推送（SPEC-BU BU1，每小时 :12）。

    剑鱼"竞对监测"付费点的核心是推送不是视图——竞对中标=我们丢单的
    直接情报，必须主动推给管理层（role=admin，watchdog 同款查询）。
    正文一句话情报：「标题」成交价 X 万（卖给了 Y）——金额/买家从公告
    文本回抽。游标=水位 stats_json.last_signal_id（与老户雷达同模式）；
    首跑基线不轰炸；失败轮丢游标全量重扫唯一约束去重兜底。
    不入看门狗：日内推送任务非关键日批，缺失下一小时自愈。
    """
    from .api.competitors import _AMOUNT_RE, _BUYER_RE
    from .db import SessionLocal
    from .models import Notification, User
    from .notify.channels import notify_dispatch

    with SessionLocal() as s:
        row = s.get(SyncWatermark, "competitor_alert")
        max_id = s.query(func.max(Signal.id)).scalar() or 0
        if row is None:
            # 首跑基线：只记游标
            return {"baseline": True, "scanned": 0, "notified": 0,
                    "last_signal_id": max_id}
        try:
            since_id = int(
                json.loads(row.stats_json or "{}").get("last_signal_id", 0)
            )
        except ValueError:
            since_id = 0
        if max_id <= since_id:
            return {"scanned": 0, "notified": 0, "last_signal_id": since_id}

        rows = s.execute(
            select(Signal, Enterprise.name)
            .join(Enterprise, Enterprise.id == Signal.enterprise_id)
            .where(
                Signal.id > since_id,
                Signal.type == "tender_win",
                Enterprise.is_competitor == 1,
                Enterprise.deleted_at.is_(None),
            )
            .order_by(Signal.id.asc())
            .limit(500)
        ).all()

        admin_ids = [
            r[0] for r in s.execute(select(User.id).where(User.role == "admin")).all()
        ]
        notified = 0
        seen: set[tuple[int, int]] = set()
        for sig, comp_name in rows:
            haystack = f"{sig.title} {sig.raw_excerpt or ''}"
            m = _AMOUNT_RE.search(haystack)
            amount_txt = f"成交价 {m.group(1)} 万" if m else ""
            mb = _BUYER_RE.search(sig.evidence_claim or "")
            buyer_txt = f"（卖给了 {mb.group(1)}）" if mb else ""
            sep = " " if amount_txt and buyer_txt else ""
            body = f"「{sig.title}」{sep}{amount_txt}{buyer_txt}"
            for uid in admin_ids:
                key = (sig.id, uid)
                if key in seen:
                    continue
                seen.add(key)
                dup = (
                    s.query(Notification)
                    .filter(
                        Notification.ref_type == "competitor_win",
                        Notification.ref_id == sig.id,
                        Notification.user_id == uid,
                    )
                    .first()
                )
                if dup is not None:
                    continue
                notify_dispatch(
                    s, uid,
                    kind="signal",
                    title=f"竞对中标：{comp_name}",
                    body=body,
                    ref_type="competitor_win",
                    ref_id=sig.id,
                )
                notified += 1
        s.commit()
        return {"scanned": max_id - since_id, "notified": notified,
                "last_signal_id": max_id}


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
def _scan_ccgp_words(stats: dict, days: int, max_pages: int) -> None:
    """ccgp 强词增量入库（SPEC-BO 抽出共用）：buyer 抽取→建档分类→去重→tender 信号。

    日批（days=7）与小时轻扫（days=2）同语义逐字一致；拦截页由
    resilience 域级积分制熔断兜底，不硬冲。
    """
    from .api.imports import extract_enterprise_name
    from .collectors.ccgp import search_ccgp_all
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .vertical import device_strong_terms as _dst

    for kw in _dst()[:12]:
        for item in search_ccgp_all(kw, days=days, max_pages=max_pages):
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
                # SPEC-W W1：采购人建档同语义补 ICP 分类
                if ent.industry_key is None:
                    from .api.imports import apply_icp

                    apply_icp(ent)
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


def run_hourly_tender_scan() -> dict:
    """小时级轻增量（SPEC-BO BO1，工作时段每 2 小时）：

    招标中"行动窗口"数据全部出自 ccgp（ggzy 只出中标=事后语义）。
    12 细词 × days=2 × 1 页轻扫摊平——增量约 96 请求/日摊在 8 个时点，
    对齐"小时级到达"而不触碰 ccgp 限流实测线（密集 400+/日出拦截页）；
    resilience 遇拦截页自动熔断，日批 03:30 全量不动。
    一周实测无拦截页 → 提频只改注册参数（"7-21/2" → "7-21"）。
    SPEC-MULTI S1：ccgp 停用即短路（数据源管理）。
    """
    from .sources import any_source_disabled

    if any_source_disabled("ccgp"):
        return {"skipped_disabled": True}
    stats = {"scanned_words": 12, "ccgp_signals": 0}
    _scan_ccgp_words(stats, days=2, max_pages=1)
    return stats


def run_daily_incremental() -> dict:
    """每日增量：ggzy 宽词近 7 天 + ccgp 强词近 7 天（同步小批次，失败记水位）。

    采集器自带限速与去重，重复词安全；时长可控（分钟级）。
    SPEC-MULTI S1：ggzy/ccgp 各自停用即跳过对应段，双双停用整体短路。
    """
    from .db import SessionLocal
    from .normalize import resolve_enterprise
    from .vertical import device_strong_terms, ggzy_name_terms
    from .api.imports import extract_enterprise_name
    from .models import Signal
    from .sources import any_source_disabled

    ggzy_off = any_source_disabled("ggzy")
    ccgp_off = any_source_disabled("ccgp")
    if ggzy_off and ccgp_off:
        return {"skipped_disabled": True}
    stats = {"ggzy_new": 0, "ccgp_signals": 0, "ggzy_buyers": 0}
    # --- ggzy 宽词增量 ---
    from .collectors.ggzy import list_bids, normalize_bid_record, normalize_entity_record, search_all_entities

    for kw in ([] if ggzy_off else ggzy_name_terms()):
        for raw in search_all_entities(kw, max_pages=1, size=200):
            rec = normalize_entity_record(raw)
            if rec is None:
                continue
            # Phase 1 建档短事务：解析+建档即提交（SQLite 单写者——写事务绝不跨
            # HTTP 调用，否则 API 请求被锁到 busy timeout，SPEC-I I1c）
            try:
                with SessionLocal() as s:
                    ent, is_new = resolve_enterprise(
                        s, rec["name"], source="ggzy", uscc=rec["uscc"],
                        est_date=rec.get("est_date"),
                        legal_rep=rec.get("legal_rep"),
                    )
                    if is_new:
                        stats["ggzy_new"] += 1
                    # SPEC-W W1：增量建档同导入链路语义——ICP 分类（含同行打标）
                    if ent.industry_key is None:
                        from .api.imports import apply_icp

                        apply_icp(ent)
                    s.commit()
            except IntegrityError:
                logger.warning(
                    "ggzy 增量：企业「%s」建档撞约束，已跳过", rec["name"], exc_info=True
                )
                continue
            if not (rec["uscc"] and rec["bid_count"] > 0):
                continue
            # Phase 2 网络抓取：无会话无事务
            try:
                result = list_bids(rec["uscc"], page=1, size=20)
            except Exception:  # noqa: BLE001 — 单主体明细失败不影响其余主体
                logger.warning("ggzy 增量：主体 %s 中标明细抓取失败", rec["uscc"], exc_info=True)
                continue
            # autoflush=False：批内同名成交公示（源数据重复行）先 seen 去重
            pending: list[dict] = []
            seen_titles: set[str] = set()
            for b in result.get("records") or []:
                nb = normalize_bid_record(b)
                if nb is None or nb["project_name"] in seen_titles:
                    continue
                seen_titles.add(nb["project_name"])
                pending.append(nb)
            if not pending:
                continue
            # Phase 3 入库短事务：exists 查重对已提交数据有效
            try:
                with SessionLocal() as s:
                    for nb in pending:
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
                        # SPEC-SALE S2：ggzy 采购人是真买家——与 ggzy-harvest 同语义
                        # 建 buyer（ggzy_buyer）帧 + tender_buy 信号。bid_list 的
                        # areaCode 实锤是省名（"山东省"）=项目所在地=采购人所在地，
                        # 建 buyer 帧时只补空不覆盖；中标方（bidder）不落——area
                        # 不是其注册地，不发明。.get() 容错（测试 mock 可能缺键）。
                        buyer = nb.get("tender_org")
                        if buyer and len(buyer) >= 4 and buyer != ent.name:
                            buyer_ent, buyer_new = resolve_enterprise(
                                s, buyer, source="ggzy_buyer"
                            )
                            if not (buyer_ent.province or ""):
                                buyer_ent.province = nb.get("area")
                            if buyer_new:
                                stats["ggzy_buyers"] += 1
                            buyer_sig = (
                                s.query(Signal)
                                .filter(
                                    Signal.enterprise_id == buyer_ent.id,
                                    Signal.url == "",
                                    Signal.title == nb["project_name"],
                                    Signal.type == "tender_buy",
                                )
                                .first()
                            )
                            if buyer_sig is None:
                                s.add(Signal(
                                    enterprise_id=buyer_ent.id,
                                    type="tender_buy",
                                    title=nb["project_name"],
                                    url="",
                                    pub_date=nb.get("pub_date"),
                                    matched_terms="[]",
                                    hit_part="title",
                                    raw_excerpt=f"作为采购人由「{ent.name}」中标成交",
                                    evidence_claim=(
                                        f"该单位公开采购「{nb['project_name']}」，"
                                        f"由{ent.name}中标"
                                    ),
                                    base_score=50.0,
                                ))
                    s.commit()
            except IntegrityError:
                # 单主体批次降级：一条脏数据不再炸整条每日增量管道
                logger.warning(
                    "ggzy 增量：主体 %s 明细批次撞唯一约束，已跳过该批次", rec["uscc"],
                    exc_info=True,
                )

    # --- ccgp 强词增量（近 7 天，只扫第一页；SPEC-BO 抽出 _scan_ccgp_words 共用）---
    if not ccgp_off:
        _scan_ccgp_words(stats, days=7, max_pages=1)

    # 意图/画像/检索投影收尾
    from .api.imports import recompute_intent
    from .brand_extract import extract_brand_for

    with SessionLocal() as s:
        ids = s.scalars(select(Enterprise.id)).all()
        for eid in ids:
            recompute_intent(s, int(eid))
            # SPEC-BIM B4：品牌装机日批兜底（幂等 upsert，采集增量后自动增产）
            extract_brand_for(s, int(eid))
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
    # SPEC-AI AI4：增量尾部订阅即时命中（水位幂等）——订阅感知从 2h 提到分钟级
    try:
        from .db import SessionLocal as _SL2

        with _SL2() as s:
            stats["subscription"] = run_subscription_rules(s)
    except Exception:  # noqa: BLE001 — 订阅侧失败不影响增量
        logger.warning("订阅即时命中失败", exc_info=True)
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
    # SPEC-J J2：有公告验证邮箱的决策链联系人（可直接邮件触达）
    if rule.cond_has_email_decision is not None and session is not None:
        from .models import Contact

        has_email = (
            session.query(Contact.enterprise_id)
            .filter(
                Contact.enterprise_id == ent.id,
                Contact.email.is_not(None),
                Contact.email != "",
                Contact.verify_state == "verified",
            )
            .first()
            is not None
        )
        if bool(rule.cond_has_email_decision) != has_email:
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


# ---------------------------------------------------------------------------


# SPEC-RADAR：昊志获客词表（2026-11-13 用户校准：一切用真空泵的都是
# 潜在客户；真空系统改造利润最好；高端不碰）——
# 1) 扩张词 2) 真空应用工艺词（超宽口径） 3) 改造/维保服务词 4) 竞对品牌词
_RADAR_KEYWORDS = (
    "中标", "投产", "扩建", "新建", "开工", "竣工", "技改",
    "招聘", "采购", "签约", "奠基", "新产线", "扩产", "一期", "二期",
    "真空", "镀膜", "半导体", "光伏", "洁净", "实验室", "干燥",
    "灭菌", "包装", "检漏", "涂布", "吸塑", "真空包装", "冻干",
    "浸渍", "脱气", "蒸馏", "浓缩", "木工", "贴片", "回流焊",
    "维修", "大修", "配件", "泵油", "保养", "集中真空", "真空系统改造",
    "节能改造", "莱宝", "普发", "爱德华", "布希", "阿特拉斯",
)


def watch_fetch_text(url: str) -> str:
    """抓页面正文文本（httpx 禁代理 + netguard 三层 SSRF 守卫 + 禁重定向）。"""
    import httpx
    import re as _re

    from .netguard import guard_public_https

    guard_public_https(url)
    resp = httpx.get(
        url, timeout=15, follow_redirects=False, trust_env=False,
        headers={"User-Agent": "Mozilla/5.0 (compatible; vaclead-radar/0.1)"},
    )
    resp.raise_for_status()
    text = _re.sub(r"<(script|style)[\s\S]*?</\1>", " ", resp.text, flags=_re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    return _re.sub(r"\s+", " ", text).strip()


def watch_check_one(session, w) -> bool:
    """检查单个 watch：hash 变化 → 更新快照 + web_change 信号 + 通知。

    返回是否变更。昊志获客词命中 → base 70 高优；否则 45 静默入流。
    网络错误落 error_last 不炸 sweep。"""
    import hashlib
    from datetime import datetime as _dt

    ent = session.get(Enterprise, w.enterprise_id)
    if ent is None or ent.deleted_at is not None:
        return False
    try:
        text = watch_fetch_text(w.url)
        err = None
    except Exception as exc:  # noqa: BLE001 — 网络/守卫错误落 error_last
        w.error_last = str(exc)[:200]
        w.last_checked_at = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        session.commit()
        return False
    del err
    new_hash = hashlib.sha256(text.encode()).hexdigest()
    w.last_checked_at = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    w.check_count = (w.check_count or 0) + 1
    changed = w.content_hash is not None and new_hash != w.content_hash
    if not changed and w.content_hash is not None:
        w.error_last = None
        session.commit()
        return False
    # 首采建基线 或 变更
    w.content_hash = new_hash
    w.snapshot_head = text[:300] or None
    w.snapshot_text = text[:200000]  # SPEC-RADAR v2：全文留存（diff 视图数据源）
    w.error_last = None
    if changed:
        # SPEC-RADAR v2：变更前全文留存（diff 视图数据源）
        w.prev_snapshot_text = w.snapshot_text
        w.snapshot_text = text[:200000]  # 官网正文上限 200KB 防异常巨型页
        w.last_changed_at = w.last_checked_at
        hit = next((k for k in _RADAR_KEYWORDS if k in text), None)
        sig = Signal(
            enterprise_id=ent.id, type="web_change",
            title=f"官网更新（{w.label}）" + (f"：命中「{hit}」" if hit else ""),
            url=w.url, raw_excerpt=text[:300],
            evidence_claim=(text[:150] or None),
            base_score=70 if hit else 45,
        )
        session.add(sig)
        session.flush()
        from .notify.channels import notify_dispatch

        notify_dispatch(
            session, ent.owner_id or 1,
            kind="signal", title=f"官网更新：{ent.name}",
            body=f"「{w.label}」页面有更新——{text[:100]}",
            ref_type="signal", ref_id=sig.id,
        )
    session.commit()
    return changed


def watch_sweep() -> dict:
    """官网监控雷达定时扫播（SPEC-RADAR：每 30 分钟，水位防呆惯例）。"""
    from .db import SessionLocal

    with SessionLocal() as session:
        watches = session.query(Watch).filter(Watch.enabled == 1).all()
        changed = 0
        failed = 0
        for w in watches:
            try:
                if watch_check_one(session, w):
                    changed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("watch %s 检查失败: %s", w.id, exc)
        return {"watches": len(watches), "changed": changed, "failed": failed}
