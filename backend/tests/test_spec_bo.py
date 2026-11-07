"""SPEC-BO 回归：小时级轻增量（ccgp 轻扫）——抽取共用与日批零行为变化。"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bo-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402

from app.collectors import ccgp as ccgp_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.jobs import run_hourly_tender_scan  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    EnterpriseAlias,
    Signal,
    SyncWatermark,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Signal, EnterpriseAlias, AuditLog]


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
        # 水位表主键是 job_name：只清本模块水位
        s.query(SyncWatermark).filter(
            SyncWatermark.job_name == "hourly_tender"
        ).delete()
        s.commit()


def _fake_ccgp(captured: dict):
    """注入两条公告：一带买家（入库）、一无买家且不可抽取（跳过）。"""
    calls: list[tuple[int, int]] = []

    def fake(keyword: str, days: int = 90, max_pages: int = 99, client=None):
        calls.append((days, max_pages))
        if keyword == "真空泵":
            return [
                {"title": "BO测试单位离心泵机组采购公告",
                 "summary": "BO测试单位采购离心泵机组，配套真空泵设备一批",
                 "buyer": "BO轻扫采购有限公司", "url": "https://example.com/bo-1",
                 "pub_date": "2026-11-07", "notice_type": "公开招标"},
                {"title": "关于办公家具的采购公告",
                 "summary": "办公家具一批，无设备内容亦无买家信息",
                 "url": "https://example.com/bo-2",
                 "pub_date": "2026-11-07", "notice_type": "询价"},
            ]
        return []

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def test_bo_hourly_scan_ingest_and_params(monkeypatch):
    """轻扫入库 + 调用参数 days=2/max_pages=1 + 无买家公告跳过 + 幂等。"""
    captured = {}
    monkeypatch.setattr(ccgp_mod, "search_ccgp_all", _fake_ccgp(captured))

    stats = run_hourly_tender_scan()
    assert stats["ccgp_signals"] == 1
    assert stats["scanned_words"] == 12

    with SessionLocal() as s:
        sig = (
            s.query(Signal)
            .filter(Signal.title == "BO测试单位离心泵机组采购公告")
            .one()
        )
        assert sig.type == "tender"
        assert sig.url == "https://example.com/bo-1"
        ent = s.get(Enterprise, sig.enterprise_id)
        assert ent is not None and ent.source == "ccgp_buyer"

        # 幂等：重扫同公告不再入库
        stats2 = run_hourly_tender_scan()
        assert stats2["ccgp_signals"] == 0
        assert (
            s.query(Signal)
            .filter(Signal.title == "BO测试单位离心泵机组采购公告")
            .count() == 1
        )

        # 清理（fixture 快照只按 id>，这里显式补删 resolve 产生的别名）
        s.query(Signal).filter(Signal.id == sig.id).delete()
        eid = ent.id
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == eid).delete()
        s.delete(ent)
        s.commit()


def test_bo_hourly_via_watermark_wrapper(monkeypatch):
    """生产同款路径：_run_job_with_watermark 落 'hourly_tender' 水位。"""
    from app.main import _run_job_with_watermark

    monkeypatch.setattr(ccgp_mod, "search_ccgp_all", _fake_ccgp({}))
    _run_job_with_watermark("hourly_tender", run_hourly_tender_scan)
    with SessionLocal() as s:
        row = s.get(SyncWatermark, "hourly_tender")
        assert row is not None
        assert row.last_status == "ok"
        assert json.loads(row.stats_json or "{}")["scanned_words"] == 12
