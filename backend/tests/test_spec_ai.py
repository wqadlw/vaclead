"""SPEC-AI 回归：认证 query-token 回退 + 信号增量游标 + 订阅即时命中。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ai-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Enterprise,
    SettingsKV,
    Signal,
    User,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Signal, Enterprise]


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
def admin_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(client, admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _seed_signal(session, name: str, title: str) -> int:
    ent = session.query(Enterprise).filter(Enterprise.name == name).first()
    if ent is None:
        ent = Enterprise(name=name, source="manual")
        session.add(ent)
        session.flush()
    sig = Signal(enterprise_id=ent.id, type="tender", title=title, url="",
                 base_score=40, created_at=utcnow())
    session.add(sig)
    session.commit()
    return sig.id


# ---------------- AI1 query-token 回退 ----------------
def test_query_token_fallback(client, admin_token):
    """无 Authorization 头时 ?token= 可认证（SSE/EventSource 场景）。"""
    r = client.get("/api/enterprises", params={"token": admin_token, "page_size": 1})
    assert r.status_code == 200, "query token 回退应通过认证"
    # 无任何凭据 → 401
    r2 = client.get("/api/enterprises", params={"page_size": 1})
    assert r2.status_code == 401
    # 篡改 token → 401
    r3 = client.get("/api/enterprises", params={"token": admin_token[:-2] + "xx", "page_size": 1})
    assert r3.status_code == 401


def test_tasks_sse_wired_with_query_token_path():
    """tasks SSE 端点走 admin_only→current_user 同一认证链（AI1 覆盖即生效）。

    不实际请求：SSE 是无限流式响应，TestClient 会等到流关闭。
    """
    import inspect

    from app.api import tasks as tasks_mod

    src = inspect.getsource(tasks_mod.task_events)
    assert "admin_only" in src, "tasks SSE 应走 current_user 认证链（query token 回退生效）"


# ---------------- AI2 since_id 增量游标 ----------------
def test_signals_since_id_increment(client, admin_headers):
    with SessionLocal() as s:
        id1 = _seed_signal(s, "游标测试大学", "第一批信号信号A")
        id2 = _seed_signal(s, "游标测试大学", "第二批信号信号B")
    # 基线
    d = client.get("/api/signals", params={"since_id": id1, "page_size": 10},
                   headers=admin_headers).json()
    ids = [it["id"] for it in d["items"]]
    assert id2 in ids and id1 not in ids, "since_id 游标应只回增量"
    assert max(ids) == max(id1, id2)
    # 无新信号
    d2 = client.get("/api/signals", params={"since_id": id2, "page_size": 10},
                    headers=admin_headers).json()
    assert all(it["id"] <= id2 for it in d2["items"]) or d2["total"] == 0


# ---------------- AI4 订阅即时命中 ----------------
def test_proposal_scan_tail_hits_subscription(monkeypatch):
    """proposal 扫描尾部应触发订阅匹配（水位幂等）。"""
    import app.jobs as jobs_mod

    called: list[dict] = []

    def fake_sub(session):
        called.append({"ok": True})
        return {"matched": 0}

    monkeypatch.setattr(jobs_mod, "run_subscription_rules", fake_sub)
    from app.collectors import proposal_hb as ph
    from app.jobs import run_proposal_hb_scan

    records = [{
        "unit": "订阅尾部测试机械有限公司",
        "project": "自动化装配线扩建项目",
        "declared_at": "2026-11-05 09:00:00",
        "project_code": "2611-420300-04-01-000001",
    }]
    monkeypatch.setattr(
        ph, "fetch_hb_filing_page",
        lambda page_no=1, page_size=20, client=None: records if page_no == 1 else [],
    )
    stats = run_proposal_hb_scan(max_pages=1)
    assert stats["new_signals"] >= 1
    assert len(called) >= 1, "扫描尾部应调用订阅匹配"


def test_daily_incremental_tail_wired():
    """增量尾部订阅命中已接线（真网络扫描不进单测——管道校验）。"""
    import inspect

    import app.jobs as jobs_mod

    src = inspect.getsource(jobs_mod.run_daily_incremental)
    assert "run_subscription_rules" in src, "增量尾部应调用订阅匹配"
