"""SPEC-I 回归：调度防呆（I0）/ 空url信号共存（I1）/ 归一缓存等价与规模（I3）。"""
from __future__ import annotations

import os
import tempfile

# 必须在导入 app 前设置（与其他测试模块同款隔离；漏设会写到真库）
os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-i-"), "t.db")
)

import time as _t

import pytest

from app.collectors import ccgp as ccgp_mod
from app.collectors import ggzy as ggzy_mod
from app.db import SessionLocal
from app.jobs import run_daily_incremental, refresh_all_stats
from app.models import Enterprise, EnterpriseAlias, Signal, SyncWatermark


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    """独立临时库需自建表（其他模块经 TestClient lifespan 触发 init_db）。"""
    from app.db import init_db

    init_db()


# ---------------- I0 调度签名防呆 ----------------

def test_cron_signature_gate_blocks_missing_session():
    """refresh_all_stats(session) 被无参注册必须被拦下——本 bug 的最小复现。"""
    from app.main import _cron_signature_ok

    assert _cron_signature_ok(refresh_all_stats, {}) is False
    assert _cron_signature_ok(refresh_all_stats, {"session": object()}) is True
    assert _cron_signature_ok(lambda: None, {}) is True


def test_refresh_stats_job_writes_ok_watermark():
    """画像刷新 sweep 语义（自开会话有参调用）必须能落 ok 水位。"""
    from app.jobs import mark_watermark

    with SessionLocal() as s:
        stats = refresh_all_stats(s)
        assert isinstance(stats, dict)
    with SessionLocal() as s:
        mark_watermark(s, "refresh_enterprise_stats_test", "ok", stats)
        row = s.get(SyncWatermark, "refresh_enterprise_stats_test")
        assert row is not None and row.last_status == "ok"


# ---------------- I1 信号约束 ----------------

def test_empty_url_different_titles_coexist():
    """约束修复回归：同企业同 type、url=''、不同 title 两条共存不撞约束。"""
    with SessionLocal() as s:
        ent = Enterprise(name="约束测试真空有限公司", source="ggzy")
        s.add(ent)
        s.flush()
        s.add_all([
            Signal(enterprise_id=ent.id, type="tender_win", title="项目甲",
                   url="", pub_date="2026-10-26", base_score=70),
            Signal(enterprise_id=ent.id, type="tender_win", title="项目乙",
                   url="", pub_date="2026-10-26", base_score=70),
        ])
        s.commit()  # 修复前此处 IntegrityError
        assert (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.url == "")
            .count()
            == 2
        )
        eid = ent.id
        s.query(Signal).filter(Signal.enterprise_id == eid).delete()
        s.delete(ent)
        s.commit()


def test_daily_incremental_two_tender_wins_end_to_end(monkeypatch):
    """端到端回归（I1 根因）：每日增量 ggzy 段同企业两条不同项目 tender_win 共存。

    修复前：第二条撞 uq_signal_dedup（旧约束不含 title）→ 整个每日增量 failed，
    04:00/04:10 后续任务连锁中断。mock 采集器走真实 resolve/入库路径。
    """
    rec = {"name": "增量回归真空设备有限公司", "uscc": "91370000I1TEST01X",
           "bid_count": 2}
    monkeypatch.setattr(ggzy_mod, "search_all_entities", lambda *a, **k: ["x"])
    monkeypatch.setattr(ggzy_mod, "normalize_entity_record", lambda raw: rec)
    monkeypatch.setattr(
        ggzy_mod, "list_bids",
        lambda *a, **k: {"records": [
            {"projectName": "增量回归项目甲", "bidPrice": 120.0},
            {"projectName": "增量回归项目乙", "bidPrice": 80.5},
            # 同一成交公示在源数据里重复出现（I1b：autoflush=False 下批内同名
            # exists 查不到 pending 写入，修复前双双入库撞唯一约束炸整条管道）
            {"projectName": "增量回归项目甲", "bidPrice": 120.0},
        ]},
    )
    monkeypatch.setattr(
        ggzy_mod, "normalize_bid_record",
        lambda b: {"project_name": b["projectName"], "bid_price_wan": b["bidPrice"],
                   "pub_date": "2026-10-20", "trade_type": "中标"},
    )
    monkeypatch.setattr(ccgp_mod, "search_ccgp_all", lambda *a, **k: [])

    stats = run_daily_incremental()
    assert isinstance(stats, dict)

    with SessionLocal() as s:
        ent = (
            s.query(Enterprise)
            .filter(Enterprise.uscc == "91370000I1TEST01X")
            .first()
        )
        assert ent is not None
        titles = {
            r[0]
            for r in s.query(Signal.title).filter(
                Signal.enterprise_id == ent.id, Signal.type == "tender_win", Signal.url == ""
            )
        }
        assert {"增量回归项目甲", "增量回归项目乙"} <= titles

        eid = ent.id
        s.query(Signal).filter(Signal.enterprise_id == eid).delete()
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == eid).delete()
        s.delete(ent)
        s.commit()


# ---------------- I3 归一会话缓存 ----------------

def test_resolve_fuzzy_merge_session_cache_equivalence_and_perf():
    """缓存路径与逐行 fuzz 语义等价：同主体变体必并、不同主体不误并、批量化后不过慢。"""
    from app.normalize import resolve_enterprise

    n_base, n_resolve = 800, 200
    with SessionLocal() as s:
        base_ids: dict[int, int] = {}
        for i in range(n_base):
            ent = Enterprise(name=f"测试归一企业{i:04d}号", source="csv")
            s.add(ent)
            s.flush()
            base_ids[i] = ent.id
        s.commit()

        t0 = _t.perf_counter()
        for i in range(n_resolve):
            ent, is_new = resolve_enterprise(s, f"测试归一企业{i:04d}号公司", source="csv")
            assert not is_new
            assert ent.id == base_ids[i]
        # 未造过的主体名也不得并进已有企业
        new_ent, is_new = resolve_enterprise(s, "测试归一企业99999号公司", source="csv")
        assert is_new
        elapsed = _t.perf_counter() - t0
        assert elapsed < 3.0, f"批量归一过慢：{elapsed:.2f}s（疑似回退 O(N²) 路径）"
        # 全部变体合并，未新建企业
        assert (
            s.query(Enterprise)
            .filter(Enterprise.name.like("测试归一企业%"))
            .count()
            == n_base + 1  # 99999 新建那家
        )

        ids = list(base_ids.values()) + [new_ent.id]
        s.query(EnterpriseAlias).filter(
            EnterpriseAlias.enterprise_id.in_(ids)
        ).delete(synchronize_session=False)
        s.query(Enterprise).filter(Enterprise.id.in_(ids)).delete(
            synchronize_session=False
        )
        s.commit()
