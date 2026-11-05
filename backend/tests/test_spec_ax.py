"""SPEC-AX 回归：回款逾期扫描 + 应收露出。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ax-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Contact,
    Enterprise,
    Lead,
    PaymentMilestone,
    SettingsKV,
    Todo,
    Contract,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Lead, Contract, PaymentMilestone,
                                Todo, Contact, AuditLog]


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


def _seed_contract_with_milestones():
    """active 合同 + 一笔已逾期分期 + 一笔 15 天内到期分期。"""
    from datetime import datetime, timedelta, timezone

    with SessionLocal() as s:
        e = Enterprise(name="应收守护测试企业", source="manual")
        s.add(e)
        s.flush()
        s.commit()  # W57 排查探针：企业先落库再建 lead
        print("\n[seed] ent id:", e.id, "| deleted:", e.deleted_at)
        lead = Lead(enterprise_id=e.id, status="won", owner_id=None)
        s.add(lead)
        s.flush()
        print("[seed] lead ok:", lead.id)
        c = Contract(lead_id=lead.id, name="应收测试合同", amount_wan=100,
                     status="active", owner_id=None)
        s.add(c)
        s.flush()
        overdue_d = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        soon_d = (datetime.now(timezone.utc) + timedelta(days=15)).strftime("%Y-%m-%d")
        m1 = PaymentMilestone(contract_id=c.id, seq=1, label="预付款",
                              amount_wan=30, due_date=overdue_d, status="pending")
        m2 = PaymentMilestone(contract_id=c.id, seq=2, label="发货款",
                              amount_wan=40, due_date=soon_d, status="pending")
        s.add_all([m1, m2])
        s.commit()
        return e.id, m1.id, m2.id


def test_payment_overdue_sweep_marks_and_todo():
    eid, m1, m2 = _seed_contract_with_milestones()
    from app.jobs import payment_overdue_sweep

    stats = payment_overdue_sweep()
    assert stats["marked"] == 1
    with SessionLocal() as s:
        m1r = s.get(PaymentMilestone, m1)
        assert m1r.status == "overdue", "过期分期应标 overdue"
        assert s.get(PaymentMilestone, m2).status == "pending", "未到期保持 pending"
        todo = s.query(Todo).filter(Todo.title.like("回款逾期%")).first()
        assert todo is not None and "30" in todo.title, "应自动建逾期待办"
    # 幂等：再跑不重复标记/建待办
    stats2 = payment_overdue_sweep()
    assert stats2["marked"] == 0 and stats2["todos_created"] == 0


def test_dashboard_receivables(client, admin_headers):
    _seed_contract_with_milestones()
    d = client.get("/api/dashboard/summary", headers=admin_headers).json()
    f = d["forecast"]
    assert f["receivable_overdue"] >= 30, "逾期金额应进 summary"
    assert f["receivable_due30"] >= 40, "30 天内到期金额应进 summary"
