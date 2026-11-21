"""vaclead FastAPI 入口：路由装配 / CORS / 定时任务 / 工具注入。"""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .api import brief, enterprises, imports, leads, misc, tasks
from .db import SessionLocal, init_db
from .models import Enterprise, Lead, SyncWatermark, TaskCheckpoint, utcnow


def _recycle_leads() -> None:
    """公海回收：超期未跟进的线索退回公海（每晚定时，规则读 pool_setting）。"""
    from datetime import timedelta

    from .models import PoolSetting

    with SessionLocal() as s:
        pool = s.query(PoolSetting).filter(PoolSetting.active == 1).first()
        days = pool.recycle_days if pool else 30
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        stale = (
            s.query(Lead)
            .filter(
                Lead.status.in_(["following", "intent"]),
                Lead.last_followed_at.is_not(None),
                Lead.last_followed_at < cutoff,
            )
            .all()
        )
        # 领取后从未跟进且超期
        never_followed = (
            s.query(Lead)
            .filter(
                Lead.status.in_(["following", "intent"]),
                Lead.last_followed_at.is_(None),
                Lead.claimed_at.is_not(None),
                Lead.claimed_at < cutoff,
            )
            .all()
        )
        for lead in set(stale) | set(never_followed):
            lead.status = "pool"
            lead.owner_id = None
            lead.updated_at = utcnow()
        s.commit()


def _inject_tools() -> None:
    """把平台能力注入 Agent 框架的工具注册表（框架未就绪时静默跳过）。"""
    try:
        from .agents.tools import TOOL_REGISTRY
    except Exception:
        return

    def search_enterprises(payload: dict) -> dict:
        with SessionLocal() as s:
            q = s.query(Enterprise)
            if payload.get("industry_key"):
                q = q.filter(Enterprise.industry_key == payload["industry_key"])
            rows = q.order_by(Enterprise.icp_score.desc()).limit(payload.get("limit", 20)).all()
            return {
                "items": [
                    {"id": e.id, "name": e.name, "icp_score": e.icp_score} for e in rows
                ]
            }

    def create_lead(payload: dict) -> dict:
        with SessionLocal() as s:
            ent = s.get(Enterprise, payload.get("enterprise_id", 0))
            if ent is None:
                return {"error": "enterprise_not_found"}
            existing = s.query(Lead).filter(Lead.enterprise_id == ent.id).first()
            if existing:
                return {"lead_id": existing.id, "existed": True}
            lead = Lead(enterprise_id=ent.id, status="new", tenant_id=ent.tenant_id)
            s.add(lead)
            s.commit()
            return {"lead_id": lead.id, "existed": False}

    for name, fn in (
        ("search_enterprises", search_enterprises),
        ("create_lead", create_lead),
    ):
        if TOOL_REGISTRY.has(name):
            TOOL_REGISTRY.register_function(name, fn)


def _recover_interrupted_tasks() -> None:
    """启动恢复（学自 AI_Find_Customer recover_interrupted_running_jobs）：
    把上次进程退出时残留的 running 任务标记为 error，避免永远卡住。"""
    with SessionLocal() as s:
        stale = s.query(TaskCheckpoint).filter(TaskCheckpoint.status == "running").all()
        for cp in stale:
            cp.status = "error"
            cp.state_json = json.dumps(
                {"error": "进程重启导致任务中断，请重新发起"},
                ensure_ascii=False,
            )
            cp.updated_at = utcnow()
        s.commit()


def _run_job_with_watermark(job_name: str, fn, job_kwargs: dict | None = None) -> None:
    """定时任务统一包装：执行 + 写 sync_watermark（失败落 failed 不静默，SPEC-B §6）。"""
    from .jobs import mark_watermark

    try:
        stats = fn(**(job_kwargs or {}))
        with SessionLocal() as s:
            mark_watermark(s, job_name, "ok", stats if stats is not None else {})
    except Exception as exc:  # noqa: BLE001 — 定时任务兜底：失败必须落水位
        import logging

        logging.getLogger("vaclead.jobs").error(
            "定时任务 %s 失败", job_name, exc_info=True
        )
        with SessionLocal() as s:
            mark_watermark(s, job_name, "failed", {"error": str(exc)})


def _cron_signature_ok(fn, job_kwargs: dict | None) -> bool:
    """调度注册防呆（SPEC-I I0）：fn 必须能用注册的 kwargs 调起。

    曾有 refresh_all_stats(session) 漏传 job_kwargs 被无参调用，任务静默 failed
    三天才被发现——注册前 bind 校验，不匹配的直接不注册并响亮告警。
    """
    import inspect

    try:
        inspect.signature(fn).bind(**(job_kwargs or {}))
        return True
    except TypeError:
        return False


def _stale_incremental_hours() -> float | None:
    """上次增量距今小时数；从未跑过返回 None（SPEC-G G4：>26h 启动补跑）。"""
    from datetime import datetime, timezone

    from .models import SyncWatermark

    with SessionLocal() as s:
        row = s.get(SyncWatermark, "daily_incremental")
    if row is None or not row.last_run_at:
        return None
    try:
        last = datetime.fromisoformat(row.last_run_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except ValueError:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # SPEC-SALE S1：话术卡种子随启动即种（幂等 DB 标记防重，免等 06:19 cron；
    # 失败不阻断启动，cron 次日兜底）
    try:
        from .api.script_cards import seed_script_cards

        with SessionLocal() as _seed_s:
            seed_script_cards(_seed_s)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("vaclead.jobs").warning("话术卡种子失败", exc_info=True)
    # SPEC-SEG2：客户群种子随启动即种（幂等 DB 标记防重，免等 06:50 cron；
    # 失败不阻断启动，cron 次日兜底）
    try:
        from .api.segments import seed_customer_segments

        with SessionLocal() as _seed_s:
            seed_customer_segments(_seed_s)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("vaclead.jobs").warning("客户群种子失败", exc_info=True)
    _inject_tools()
    _recover_interrupted_tasks()
    # 日志按天滚动（I6 P2）：data/logs/vaclead.log，保留 14 天
    from .logrotate import setup_logging

    setup_logging()
    # 审计钩子（SPEC-J J1）：update/delete 落 audit_log
    from .audit import install as _install_audit

    _install_audit()
    # 线索自动分配钩子（SPEC-N N3）：before_flush 统一覆盖全部线索创建入口
    from .assign import install as _install_assign

    _install_assign()
    # Webhooks 出站事件钩子（SPEC-P）：enterprise/lead/signal/quote 变更入投递队列
    from .webhookbus import install as _install_webhooks

    _install_webhooks()
    # 记录主事件循环：采集线程用它把 SSE 事件推回订阅者
    import asyncio

    from .api.tasks import set_main_loop

    set_main_loop(asyncio.get_running_loop())

    # 测试/E2E 环境禁用全部后台任务（G4 补跑会对着真网络跑增量并持写事务，
    # 与用例并发写库 → database is locked，SPEC-I I0c）
    _jobs_disabled = os.environ.get("VACLEAD_DISABLE_JOBS") == "1"

    # SPEC-G G4：进程不常开——启动时若上次增量>26h（或从未跑过且有数据）自动后台补一次
    if not _jobs_disabled:
        try:
            from .db import SessionLocal as _SL0
            from .models import Enterprise as _Ent

            with _SL0() as _s0:
                _has_data = _s0.query(_Ent).count() > 0
            _hours = _stale_incremental_hours()
            if _has_data and (_hours is None or _hours > 26):
                import threading as _th

                from .jobs import run_daily_incremental

                def _catchup() -> None:
                    _run_job_with_watermark("daily_incremental", run_daily_incremental)

                _th.Thread(target=_catchup, daemon=True).start()
                import logging as _lg

                _lg.getLogger("vaclead.jobs").info(
                    "启动补跑增量（距上次 %s 小时）", "从未" if _hours is None else round(_hours)
                )
        except Exception:  # noqa: BLE001 — 补跑失败不影响启动
            import logging

            logging.getLogger("vaclead.jobs").warning("启动补增量检查失败", exc_info=True)

    try:
        import logging

        from apscheduler.schedulers.background import BackgroundScheduler

        from .db import DATA_DIR as _DATA_DIR
        from .jobs import backup_sqlite, refresh_all_stats, run_daily_incremental

        scheduler = BackgroundScheduler()

        def _register_cron(job_name: str, fn, hour: "int | str", minute: "int | str",
                           job_kwargs: dict | None = None,
                           day_of_week: str | None = None) -> None:
            """统一 cron 注册（SPEC-I I0）：签名防呆 + 失败落水位 + 防重入。

            注册前 bind 校验拦下参数笔误（曾有 refresh_all_stats 漏传 session
            静默 failed 三天）；misfire 宽限/合并/单实例为每日任务标准加固。
            day_of_week：APScheduler 语义（如 "mon"），供周任务使用（SPEC-O）。
            hour/minute 支持 "*/n" 表达式（SPEC-AA AA3 订阅扫播提频、
            SPEC-BM 老户雷达每 30 分钟）。
            """
            if _jobs_disabled:
                logging.getLogger("vaclead.jobs").info(
                    "VACLEAD_DISABLE_JOBS=1：跳过注册定时任务 %s（测试环境）", job_name
                )
                return
            if not _cron_signature_ok(fn, job_kwargs):
                logging.getLogger("vaclead.jobs").error(
                    "定时任务 %s 注册签名不匹配（fn=%r kwargs=%r），已跳过——请修复注册参数",
                    job_name, fn, job_kwargs,
                )
                return
            extra = {"day_of_week": day_of_week} if day_of_week else {}
            try:
                scheduler.add_job(
                    _run_job_with_watermark, "cron", hour=hour, minute=minute,
                    args=[job_name, fn, job_kwargs],
                    misfire_grace_time=3600, coalesce=True, max_instances=1,
                    **extra,
                )
            except Exception:  # noqa: BLE001 — SPEC-GUARD 教训：单 job 参数毒雷
                # （如 hour="*/30" 超 0-23 范围）不得炸掉整个调度器注册——
                # 2026-11-15 watch_radar 一颗雷停摆全部 30+ cron 5 天。响亮告警
                # 并继续注册其余任务。
                logging.getLogger("vaclead.jobs").critical(
                    "定时任务 %s 注册失败（hour=%r minute=%r）——已跳过，其余任务照常",
                    job_name, hour, minute, exc_info=True,
                )

        # SPEC-B §6：全部定时任务统一走水位包装（公海回收 → 增量采集 → 画像 → 备份串行错峰）
        _register_cron("recycle_leads", _recycle_leads, 2, 0)
        _register_cron("daily_incremental", run_daily_incremental, 3, 30)

        def _refresh_stats_sweep() -> dict:
            from .db import SessionLocal as _SL

            with _SL() as s:
                return refresh_all_stats(s)

        _register_cron("refresh_enterprise_stats", _refresh_stats_sweep, 4, 0)

        def _notify_sweep() -> dict:
            from .jobs import generate_notifications
            from .db import SessionLocal as _SL

            with _SL() as s:
                return generate_notifications(s)

        _register_cron("notify_sweep", _notify_sweep, 4, 5)

        def _digest_flush() -> dict:
            from .notify.channels import digest_flush
            from .db import SessionLocal as _SL

            with _SL() as s:
                return digest_flush(s)

        _register_cron("digest_flush", _digest_flush, 8, 5)  # SPEC-G：静默期聚合出舱

        def _reports_weekly_sweep() -> dict:
            from .api.reports import reports_weekly_sweep

            return reports_weekly_sweep()

        # SPEC-O：每周一 08:30 上周经营周报摘要 → admin（email 渠道走 J4 emailengine）
        _register_cron("reports_weekly", _reports_weekly_sweep, 8, 30, day_of_week="mon")

        def _automation_sweep() -> dict:
            from .jobs import run_automation_rules
            from .db import SessionLocal as _SL

            with _SL() as s:
                return run_automation_rules(s)

        _register_cron("automation_sweep", _automation_sweep, 4, 7)

        def _notice_contacts_daily() -> dict:
            # 直接复用端点逻辑（内部限速/幂等/限流即停），免 HTTP 依赖。
            # SPEC-AA AA1：每日限额 30→300（详情页 1s 限速约 8 分钟），
            # 招标中企业优先（回填查询内排序）。
            from .api.tasks import backfill_notice_contacts as _fn

            from .db import SessionLocal as _SL

            with _SL() as s:
                result = _fn(type("B", (), {"limit": 300})(), s, None)
            return result if isinstance(result, dict) else {}

        _register_cron("notice_contacts", _notice_contacts_daily, 4, 8)
        _register_cron("backup_sqlite", backup_sqlite, 4, 10,
                       {"backup_dir": str(_DATA_DIR / "backups")})

        def _subscription_rules_sweep() -> dict:
            from .db import SessionLocal as _SL
            from .jobs import run_subscription_rules

            with _SL() as s:
                return run_subscription_rules(s)

        _register_cron("subscription_rules", _subscription_rules_sweep, "*", 6)  # SPEC-BO：每小时（与小时级数据到达对齐；原每 2 小时 AA3）

        def _hourly_tender_sweep() -> dict:
            from .jobs import run_hourly_tender_scan

            return run_hourly_tender_scan()

        # SPEC-BO BO1：工作时段每 2 小时 ccgp 轻扫（12 细词×1 页×days=2 摊平，
        # 不触碰限流实测线；拦截页 resilience 自动熔断）——"7-21/2" 区间步进语法
        _register_cron("hourly_tender", _hourly_tender_sweep, "7-21/2", 40)

        def _proposal_gd_sweep() -> dict:
            from .jobs import run_proposal_gd_scan

            return run_proposal_gd_scan(max_pages=5)

        # SPEC-AB：广东拟建/备案公示（比招标早 1-3 个月的采购前兆）
        _register_cron("proposal_gd", _proposal_gd_sweep, 5, 0)

        def _proposal_hb_sweep() -> dict:
            from .jobs import run_proposal_hb_scan

            return run_proposal_hb_scan(max_pages=5)

        # SPEC-AG：湖北备案查询（项目级拟建信号，与广东错峰 10 分钟）
        _register_cron("proposal_hb", _proposal_hb_sweep, 5, 10)

        def _proposal_js_sweep() -> dict:
            from .jobs import run_proposal_js_scan

            return run_proposal_js_scan(max_pages=1)

        # SPEC-BR：江苏备案公示（昊志腹地；服务端关键词过滤，12 请求/日；错峰 05:20）
        _register_cron("proposal_js", _proposal_js_sweep, 5, 20)

        def _proposal_zj_sweep() -> dict:
            from .jobs import run_proposal_zj_scan

            return run_proposal_zj_scan(max_pages=2)

        # SPEC-BS：浙江办理结果公示（JSON+事项维度；ggzy 宽词 12 请求/日；错峰 05:30）
        _register_cron("proposal_zj", _proposal_zj_sweep, 5, 30)

        def _sdgp_sweep() -> dict:
            from .jobs import run_sdgp_scan

            return run_sdgp_scan(max_pages=3)

        # SPEC-SDGP：山东政采分网直采（昊志本省+ccgp 冗余备份；05:40 错峰于四省后）
        # 2026-11-14 夜：每日 05:40+12:40 双时段——WAF 解封恢复机会翻倍；被封时
        # 每轮仅 1-3 请求即熔断，成本低。单任务名双触发，看门狗/水印不变。
        _register_cron("sdgp", _sdgp_sweep, "5,12", 40)

        def _hunan_sweep() -> dict:
            from .jobs import run_hunan_scan

            return run_hunan_scan()

        # SPEC-HUNAN：湖南政采当日块（luban 平台无关键词接口，拉一次够；
        # 06:00 错峰于 sdgp 05:40 后）
        _register_cron("hunan", _hunan_sweep, 6, 0)

        def _luban_sweep() -> dict:
            from .jobs import run_luban_scan

            return run_luban_scan()

        # SPEC-LUBAN：政采云系三省（沪黔桂）意向公开——全量入库建档+proposal
        # 信号（06:10 湖南后；三源共用单 job luban，单水位）
        _register_cron("luban", _luban_sweep, 6, 10)

        def _contract_renewals_sweep() -> dict:
            from .api.contracts import check_contract_renewals
            from .db import SessionLocal as _SL

            with _SL() as s:
                return check_contract_renewals(s)

        # SPEC-AD：每日 06:10 扫描即将到期合同 → 自动创建续约 Todo
        _register_cron("contract_renewals", _contract_renewals_sweep, 6, 10)

        def _purge_sweep() -> dict:
            from .jobs import purge_deleted_sweep

            return purge_deleted_sweep()

        # SPEC-AM：每日 03:00 清空超过 30 天保留期的回收站
        _register_cron("purge_deleted", _purge_sweep, 3, 0)

        def _watchdog_sweep() -> dict:
            from .jobs import run_watermark_watchdog

            return run_watermark_watchdog()

        # SPEC-AR AR1：每日 06:30 水位看门狗（关键日批未执行自动补跑+通知）
        _register_cron("watermark_watchdog", _watchdog_sweep, 6, 30)

        def _consumable_sweep() -> dict:
            from .api.units import consumable_clock_sweep

            return consumable_clock_sweep()

        # SPEC-AS AS1：每日 06:20 装机耗材时钟扫描 → 回访待办
        _register_cron("consumable_clock", _consumable_sweep, 6, 20)

        # SPEC-AS AS3：昊青产品线种子（幂等）
        def _seed_products() -> dict:
            from .db import SessionLocal as _SL
            from .api.units import seed_haoqing_products

            with _SL() as s:
                return {"seeded": seed_haoqing_products(s)}

        _register_cron("product_seed", _seed_products, 6, 18)

        # SPEC-SALE S1：话术卡种子（幂等 DB 标记防重，与 product_seed 错峰 1 分钟）
        def _seed_script_cards() -> dict:
            from .api.script_cards import seed_script_cards
            from .db import SessionLocal as _SL

            with _SL() as s:
                return {"seeded": seed_script_cards(s)}

        _register_cron("script_card_seed", _seed_script_cards, 6, 19)

        # SPEC-SEG2：客户群种子（幂等 DB 标记防重，错峰 06:50）
        def _seed_segments() -> dict:
            from .api.segments import seed_customer_segments
            from .db import SessionLocal as _SL

            with _SL() as s:
                return {"seeded": seed_customer_segments(s)}

        _register_cron("segment_seed", _seed_segments, 6, 50)

        # SPEC-BIM B4：品牌装机全量抽取（幂等无害；04:20 与采集日批错峰）。
        # 看门狗纪律：非采集任务（无网络失败面），不进 watchdog_jobs。
        def _brand_extract_sweep() -> dict:
            from .brand_extract import extract_all
            from .db import SessionLocal as _SL

            with _SL() as s:
                return extract_all(s)

        _register_cron("brand_extract", _brand_extract_sweep, 4, 20)

        def _oem_touch_sweep() -> dict:
            from .jobs import oem_touch_sweep

            return oem_touch_sweep()

        # SPEC-AU：每周一 07:00 OEM 季度关系维护扫描（90 天未触达 → 待办）
        _register_cron("oem_touch", _oem_touch_sweep, 1, 7, day_of_week="mon")

        def _payment_overdue_sweep() -> dict:
            from .jobs import payment_overdue_sweep

            return payment_overdue_sweep()

        # SPEC-AX AX1：每日 06:25 回款逾期扫描（pending→overdue + 逾期待办）
        _register_cron("payment_overdue", _payment_overdue_sweep, 6, 25)

        def _owner_signal_sweep() -> dict:
            from .jobs import owner_signal_sweep

            return owner_signal_sweep()

        # SPEC-BM BM1：每 30 分钟老户雷达——在管客户新信号零配置推送负责人
        # （won=老户新动向 / intent=意向客户新信号 / following=跟进客户新信号）
        _register_cron("owner_signal", _owner_signal_sweep, "*", "*/30")

        def _competitor_alert_sweep() -> dict:
            from .jobs import competitor_alert_sweep

            return competitor_alert_sweep()

        # SPEC-BU BU1：每小时 :12 竞对中标推送 admin（管理层情报，剑鱼对位收口）
        _register_cron("competitor_alert", _competitor_alert_sweep, "*", 12)

        # SPEC-AP AP1：Meili 可用性预热——冷启动首击的 2.5s 探测在启动期消化，
        # 不让第一个倒霉请求吃（探测结果随后走 30s 缓存）
        def _meili_probe_safe() -> None:
            try:
                from . import search as _meili

                _meili.available()
            except Exception:  # noqa: BLE001
                pass

        import threading as _th

        _th.Thread(target=_meili_probe_safe, daemon=True).start()

        def _health_sweep() -> dict:
            from .health import batch_health_refresh
            from .db import SessionLocal as _SL

            with _SL() as s:
                return batch_health_refresh(s)

        # SPEC-AE：每日 06:15 刷新客户健康分与生命周期
        _register_cron("health_refresh", _health_sweep, 6, 15)

        def _audit_cleanup_sweep() -> dict:
            from .db import SessionLocal as _SL
            from .jobs import audit_cleanup

            with _SL() as s:
                return audit_cleanup(s)

        _register_cron("audit_cleanup", _audit_cleanup_sweep, 4, 12)

        # SPEC-J J4：触达调度——工作时段两次（收件同步 + 预算内发送）
        def _outreach_dispatch_sweep() -> dict:
            from .db import SessionLocal as _SL
            from .outreach.dispatcher import run_outreach_dispatch

            with _SL() as s:
                return run_outreach_dispatch(s)

        _register_cron("outreach_dispatch", _outreach_dispatch_sweep, 9, 30)
        _register_cron("outreach_dispatch", _outreach_dispatch_sweep, 14, 30)

        # SPEC-P：webhook 投递引擎——分钟级 interval（_register_cron 是 hour:minute 粒度）
        if not _jobs_disabled:
            def _webhook_flush_sweep() -> dict:
                from .db import SessionLocal as _SLW
                from .webhookbus import flush_deliveries

                with _SLW() as _sw:
                    return flush_deliveries(_sw)

            scheduler.add_job(
                _run_job_with_watermark, "interval", minutes=10,
                args=["webhook_flush", _webhook_flush_sweep, None],
                misfire_grace_time=300, coalesce=True, max_instances=1,
            )
            logging.getLogger("vaclead.jobs").info(
                "webhook 投递引擎已注册（interval 10min）"
            )

        # SPEC-Q：每周六 05:00 全库查重扫描 → 喂复核队列
        def _dup_scan_sweep() -> dict:
            from .db import SessionLocal as _SLD
            from .dedupe import scan_duplicates

            with _SLD() as _sd:
                return scan_duplicates(_sd)

        _register_cron("dup_scan", _dup_scan_sweep, 5, 0, day_of_week="sat")

        # SPEC-RADAR：官网变更监控雷达（昊志获客前兆——竞对/重点客户官网
        # 新闻/招聘页更新，工业词命中即推通知与群播报）
        def _watch_sweep() -> dict:
            from .jobs import watch_sweep

            return watch_sweep()

        # SPEC-RADAR：官网监控每 30 分钟——2026-11-15 生产事故根因修复：原参数
        # (hour="*/30", minute="*") 把 30 分钟步长写进小时字段（范围 0-23 装不下），
        # APScheduler 3.11 硬拒绝 → 注册中断 → scheduler.start() 从未执行 →
        # 全部 30+ cron 自 11-10 起停摆 5 天（watchdog 本身也是 cron 同死）。
        # 正确语义：hour="*", minute="*/30"。
        _register_cron("watch_radar", _watch_sweep, "*", "*/30")
        scheduler.start()
        app.state.scheduler = scheduler  # SPEC-GUARD：探针/诊断可查 job 触发时刻

        # ---- SPEC-GUARD：调度器心跳自愈（2026-11-15 生产事故根治）----
        # 事故：11-10 18:41 起 APScheduler 调度线程静默死亡（进程存活 HTTP
        # 正常，watchdog 也是 cron 所以同样死），全部定时任务停摆 5-6 天无人
        # 报警（hourly_tender 5 天/四省 6 天欠账）。根治=双层：
        # ①哨兵 job 每分钟更新共享心跳（sched_state）；②守护线程每 120s 检查
        # ——心跳超 180s 视为调度器死亡，尝试 start() 自愈并落 critical 日志
        # +水位告警；③/api/tasks/recent 暴露 scheduler_alive 给前端。
        from . import sched_state as _sched_state

        def _scheduler_heartbeat() -> None:
            _sched_state.beat()
            logging.getLogger("vaclead.jobs").info("调度器心跳 tick（%s）", _sched_state.LAST_BEAT)

        try:
            if not _jobs_disabled:
                scheduler.add_job(
                    _scheduler_heartbeat, "interval", minutes=1,
                    misfire_grace_time=300, coalesce=True, max_instances=1,
                )
        except Exception:  # noqa: BLE001
            logging.getLogger("vaclead.jobs").warning("心跳哨兵注册失败", exc_info=True)

        def _scheduler_guard() -> None:
            import logging as _lg
            import time as _t

            while True:
                _t.sleep(120)
                _silent = _t.time() - _sched_state.LAST_BEAT
                if _silent <= _sched_state.STALE_AFTER_S:
                    continue
                _lg.getLogger("vaclead.jobs").critical(
                    "调度器心跳静默 %.0fs（>%ds）——APScheduler 线程疑似死亡，尝试自愈",
                    _silent, _sched_state.STALE_AFTER_S,
                )
                try:
                    if not scheduler.running:
                        scheduler.start()
                        _sched_state.beat()
                        _lg.getLogger("vaclead.jobs").critical("调度器已重新 start()——自愈完成")
                    else:
                        # running 但心跳停：调度线程卡死，重启进程才能彻底恢复——落水位告警
                        with SessionLocal() as _gs:
                            _row = _gs.get(SyncWatermark, "scheduler_guard")
                            if _row is None:
                                _row = SyncWatermark(job_name="scheduler_guard")
                                _gs.add(_row)
                            _row.last_run_at = utcnow()
                            _row.last_status = "degraded"
                            _row.stats_json = '{"silent_seconds": %d}' % int(_silent)
                            _gs.commit()
                except Exception:  # noqa: BLE001
                    _lg.getLogger("vaclead.jobs").critical("调度器自愈失败", exc_info=True)

        import threading as _guard_th

        _guard_th.Thread(target=_scheduler_guard, daemon=True).start()
    except Exception:  # noqa: BLE001 — APScheduler 不可用时跳过定时任务（不影响主功能）
        import logging

        logging.getLogger("vaclead.jobs").warning("调度器启动失败，定时任务停摆", exc_info=True)
    yield


app = FastAPI(title="vaclead", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- 可观测（企业级）：请求访问日志 + 慢请求标记 + 全局异常兜底 ----
@app.middleware("http")
async def access_log_middleware(request, call_next):
    import logging
    import time as _time

    start = _time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger("vaclead.http").exception(
            "未处理异常 %s %s", request.method, request.url.path
        )
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "服务器内部错误，详见服务端日志"}, status_code=500)
    cost_ms = (_time.monotonic() - start) * 1000
    if request.url.path.startswith("/api/") and cost_ms > 1500:
        logging.getLogger("vaclead.http").warning(
            "慢请求 %s %s %.0fms", request.method, request.url.path, cost_ms
        )
    response.headers["X-Process-Time-Ms"] = f"{cost_ms:.0f}"
    return response

app.include_router(misc.router, prefix="/api")
app.include_router(enterprises.router, prefix="/api")
app.include_router(brief.router, prefix="/api")  # SPEC-BRF 客户作战简报
app.include_router(leads.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
from .api import review  # noqa: E402

app.include_router(review.router, prefix="/api")
from .api import intel  # noqa: E402

app.include_router(intel.router, prefix="/api")
from .api import competitors  # noqa: E402

app.include_router(competitors.router, prefix="/api")
from .api import data as data_api  # noqa: E402

app.include_router(data_api.router, prefix="/api")  # SPEC-INFO 信息中心
from .api import proposals  # noqa: E402

app.include_router(proposals.router, prefix="/api")
from .api import contacts  # noqa: E402

app.include_router(contacts.router, prefix="/api")
from .api import timeline  # noqa: E402

app.include_router(timeline.router, prefix="/api")
from .api import quality  # noqa: E402

app.include_router(quality.router, prefix="/api")
from .api import search  # noqa: E402

app.include_router(search.router, prefix="/api")
from .api import automation  # noqa: E402

app.include_router(automation.router, prefix="/api")
from .api import todos  # noqa: E402

app.include_router(todos.router, prefix="/api")
from .api import analytics  # noqa: E402

app.include_router(analytics.router, prefix="/api")
from .api import reports  # noqa: E402

app.include_router(reports.router, prefix="/api")
from .api import views  # noqa: E402

app.include_router(views.router, prefix="/api")
from .api import governance  # noqa: E402

app.include_router(governance.router, prefix="/api")
from .api import quotes  # noqa: E402

app.include_router(quotes.router, prefix="/api")
from .api import leadforms  # noqa: E402

app.include_router(leadforms.router, prefix="/api")
from .api import contracts  # noqa: E402

app.include_router(contracts.router, prefix="/api")
from .api import outreach  # noqa: E402

app.include_router(outreach.router, prefix="/api")
from .api import org  # noqa: E402

app.include_router(org.router, prefix="/api")
from .api import bulletin  # noqa: E402

app.include_router(bulletin.router, prefix="/api")
from .api import webhooks  # noqa: E402

app.include_router(webhooks.router, prefix="/api")
from .api import prefs  # noqa: E402

app.include_router(prefs.router, prefix="/api")
from .api import mcp  # noqa: E402

app.include_router(mcp.router, prefix="/api")
from .api import units  # noqa: E402

app.include_router(units.router, prefix="/api")
from .api import proposal  # noqa: E402

app.include_router(proposal.router, prefix="/api")
from .api import script_cards  # noqa: E402

app.include_router(script_cards.router, prefix="/api")
from .api import segments  # noqa: E402

app.include_router(segments.router, prefix="/api")  # SPEC-SEG2 客户群画像


# ---- 单进程部署：托管前端静态导出（frontend/out），SPA 回退 ----
from pathlib import Path as _Path

_STATIC_DIR = _Path(__file__).resolve().parents[2] / "frontend" / "out"
if _STATIC_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app.mount("/_next", StaticFiles(directory=_STATIC_DIR / "_next"), name="next-static")

    @app.get("/", include_in_schema=False)
    async def _spa_index():
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        if full_path.startswith(("api/", "docs", "openapi.json")):
            raise HTTPException(404, "Not Found")
        # SPEC-SEG2：/profile/{code}（客户群专属页漂亮 URL）→ 302 到查询参数页
        # （静态导出无动态路由；view 为真实页排除；带点路径是静态资源/文件，排除）
        if full_path.startswith("profile/"):
            seg_code = full_path[len("profile/"):]
            if seg_code and seg_code != "view" and "/" not in seg_code and "." not in seg_code:
                from fastapi.responses import RedirectResponse

                return RedirectResponse(f"/profile/view?code={seg_code}", status_code=302)
        candidate = _STATIC_DIR / f"{full_path}.html"
        if candidate.is_file():
            return FileResponse(candidate)
        candidate = _STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")  # SPA 客户端路由回退
else:

    @app.get("/")
    def root():
        return {"app": "vaclead", "docs": "/docs"}
