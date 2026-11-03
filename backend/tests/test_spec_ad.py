"""SPEC-AD 回归：合同管理 + 回款分期 + 续约提醒 + 状态机。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ad-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    from app.db import init_db

    init_db()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def won_lead_id(client, admin_headers) -> int:
    row = "name,经营范围\n合同测试有限公司,真空设备研发\n"
    client.post("/api/import/enterprises",
                files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers)
    d = client.get("/api/enterprises", params={"q": "合同测试"}, headers=admin_headers).json()
    eid = d["items"][0]["id"]
    lid = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers).json()["id"]
    client.patch(f"/api/leads/{lid}", json={"status": "following"}, headers=admin_headers)
    client.patch(f"/api/leads/{lid}", json={"status": "intent"}, headers=admin_headers)
    client.patch(f"/api/leads/{lid}", json={"status": "won"}, headers=admin_headers)
    return lid


def test_contract_lifecycle(client, admin_headers, won_lead_id):
    # 创建合同（分期自动分摊）
    milestones = [
        {"seq": 1, "label": "预付款", "stage": "prepayment", "ratio": 30, "amount_wan": 30.0},
        {"seq": 2, "label": "发货款", "stage": "delivery", "ratio": 40, "amount_wan": 40.0},
        {"seq": 3, "label": "验收款", "stage": "acceptance", "ratio": 20, "amount_wan": 20.0},
        {"seq": 4, "label": "质保金", "stage": "warranty", "ratio": 10, "amount_wan": 10.0},
    ]
    r = client.post("/api/contracts", json={
        "name": "真空泵组采购合同", "lead_id": won_lead_id,
        "amount_wan": 100.0, "end_date": "2027-06-30",
        "payment_terms": "30/40/20/10", "milestones": milestones,
    }, headers=admin_headers)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["total_wan"] == 100.0

    # 状态机 draft→active→completed
    for st in ("active", "completed"):
        r = client.patch(f"/api/contracts/{cid}/status", json={"status": st},
                         headers=admin_headers)
        assert r.status_code == 200
    # completed 终态
    r = client.patch(f"/api/contracts/{cid}/status", json={"status": "draft"},
                     headers=admin_headers)
    assert r.status_code == 422

    # 详情含回款分期
    d = client.get(f"/api/contracts/{cid}", headers=admin_headers).json()
    assert len(d["milestones"]) == 4
    assert d["enterprise_name"] == "合同测试有限公司"

    # 列表
    items = client.get("/api/contracts", headers=admin_headers).json()["items"]
    assert any(c["id"] == cid for c in items)


def test_contract_renewal_todo_auto_created(client, admin_headers, won_lead_id):
    """到期合同自动创建续约 Todo。"""
    from app.db import SessionLocal
    from app.api.contracts import check_contract_renewals
    from app.models import Contract, Todo

    r = client.post("/api/contracts", json={
        "name": "即将到期合同", "lead_id": won_lead_id,
        "amount_wan": 50.0, "end_date": "2026-12-01",
        "milestones": [{"seq": 1, "label": "全款", "amount_wan": 50.0}],
    }, headers=admin_headers)
    cid = r.json()["id"]
    client.patch(f"/api/contracts/{cid}/status", json={"status": "active"},
                 headers=admin_headers)

    with SessionLocal() as s:
        stats = check_contract_renewals(s)
        assert stats["renewal_todos"] >= 1
        stats2 = check_contract_renewals(s)
        assert stats2["renewal_todos"] == 0
        s.query(Todo).filter(Todo.title.contains("续约：")).delete()
        s.query(Contract).filter(Contract.id == cid).delete()
        s.commit()
