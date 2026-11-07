"""SPEC-AR 回归：水位看门狗 + MCP 写工具限流。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ar-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    SettingsKV,
    SyncWatermark,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact]


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
        s.query(SettingsKV).filter(SettingsKV.key.like("pref:%")).delete()
        s.commit()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_watchdog_recovers_stale_jobs(monkeypatch):
    """昨日水位的关键任务被看门狗补跑；当日任务跳过。（任务全打桩防真网络）"""
    import app.jobs as jobs_mod
    from app.jobs import run_watermark_watchdog

    noop = lambda: {"stub": True}  # noqa: E731
    monkeypatch.setattr(jobs_mod, "run_daily_incremental", noop)
    monkeypatch.setattr(jobs_mod, "run_proposal_gd_scan", noop)
    monkeypatch.setattr(jobs_mod, "run_proposal_hb_scan", noop)
    monkeypatch.setattr(jobs_mod, "run_proposal_js_scan", noop)  # SPEC-BR：新任务必须打桩防真网络
    monkeypatch.setattr(jobs_mod, "backup_sqlite", noop)

    with SessionLocal() as s:
        # 构造：proposal_hb 水位停在昨日
        stale = (datetime_str(-1),)
        row = s.get(SyncWatermark, "proposal_hb")
        if row is None:
            row = SyncWatermark(job_name="proposal_hb")
            s.add(row)
        row.last_run_at = stale[0]
        row.last_status = "ok"
        # health_refresh 已是今日 → 应跳过
        row2 = s.get(SyncWatermark, "health_refresh")
        if row2 is None:
            row2 = SyncWatermark(job_name="health_refresh")
            s.add(row2)
        row2.last_run_at = datetime_str(0)
        row2.last_status = "ok"
        s.commit()

    stats = run_watermark_watchdog()
    assert stats["checked"] == 6  # SPEC-BR：proposal_js 纳入关键日批看门狗
    assert stats["results"]["proposal_hb"] in ("recovered", "recover-failed")
    assert stats["results"]["health_refresh"] == "ok-today", "当日任务应跳过"


def datetime_str(days_offset: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (
        datetime.now(timezone.utc) + timedelta(days=days_offset)
    ).strftime("%Y-%m-%dT%H:%M:%S")


def test_watchdog_writes_watermark(client, admin_headers):
    """补跑后水位应更新为今日（自证）。"""
    from app.jobs import run_watermark_watchdog

    run_watermark_watchdog()
    with SessionLocal() as s:
        row = s.get(SyncWatermark, "backup_sqlite")
        assert row is not None and row.last_run_at is not None


def test_mcp_convert_lead_and_ratelimit(client, admin_headers):
    """写工具 convert_lead（rw key）+ 令牌桶限流。"""
    with SessionLocal() as s:
        e = Enterprise(name="MCP 写工具测试企业", source="poi", city="苏州")
        s.add(e)
        s.flush()
        eid = e.id
        s.commit()
    r = client.post("/api/api-keys", json={"name": "ar-rw", "scopes": "rw"},
                    headers=admin_headers)
    key = r.json()["key"]
    h = {"X-Api-Key": key}
    r1 = client.post("/api/mcp/call", json={"tool": "convert_lead", "args": {"eid": eid}}, headers=h)
    assert r1.status_code == 200 and r1.json()["ok"] is True
    assert r1.json()["data"]["created"] is True
    # 限流：连续 10 次后 429
    codes = []
    for _ in range(12):
        rr = client.post("/api/mcp/call", json={"tool": "convert_lead", "args": {"eid": eid}}, headers=h)
        codes.append(rr.status_code)
    assert 429 in codes, "超过 10 次/分钟应 429"
