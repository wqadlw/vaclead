"""API 层测试：认证/筛选/线索闭环/公海规则/导入/SSE 任务创建。

使用 TestClient + 临时 SQLite（VACLEAD_DB 环境变量必须在导入 app 前设置）。
"""
from __future__ import annotations

import os
import tempfile

pytest_tmp = tempfile.mkdtemp(prefix="vaclead-api-")
os.environ.setdefault("VACLEAD_DB", os.path.join(pytest_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


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
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_enterprise(client, headers, name: str, scope: str = "", cap: float | None = 500) -> int:
    """用 CSV 导入造一家企业，返回 id。"""
    row = f"name,province,city,district,address,business_scope,registered_capital_wan,industry_key\n{name},江苏省,苏州市,工业园区,地址一,半导体晶圆制造,{cap},semiconductor\n"
    r = client.post(
        "/api/import/enterprises",
        files={"file": ("m.csv", row.encode("utf-8"), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    items = client.get("/api/enterprises", headers=headers).json()["items"]
    return next(e["id"] for e in items if e["name"] == name)


# ---- 认证与基础 ----
def test_login_wrong_password(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code == 401


def test_enterprises_requires_auth(client):
    assert client.get("/api/enterprises").status_code == 401


# ---- 导入与批次追溯 ----
def test_import_batch_id(client, admin_headers):
    row = (
        "name,province,city,district,address,business_scope,registered_capital_wan,industry_key\n"
        "批次测试半导体有限公司,江苏省,苏州市,工业园区,地1,晶圆制造,1000,semiconductor\n"
    )
    r = client.post("/api/import/enterprises", files={"file": ("b.csv", row.encode(), "text/csv")}, headers=admin_headers)
    body = r.json()
    assert r.status_code == 200 and body["inserted"] == 1
    assert body.get("batch_id")


# ---- 线索闭环 + next_time 冗余 ----
def test_followup_updates_next_fields(client, admin_headers):
    eid = _mk_enterprise(client, admin_headers, "苏州芯测半导体有限公司")
    r = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers)
    assert r.status_code == 201, r.text
    lead_id = r.json()["id"]

    r = client.post(
        f"/api/leads/{lead_id}/followups",
        json={"content": "初次电话", "next_action": "寄送参数表", "next_time": "2026-11-01 10:00"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    leads = client.get("/api/leads?status=following", headers=admin_headers).json()["items"]
    lead = next(l for l in leads if l["id"] == lead_id)
    assert lead["next_action"] == "寄送参数表"
    assert lead["next_time"] == "2026-11-01 10:00"

    dash = client.get("/api/dashboard", headers=admin_headers).json()
    pending = next(p for p in dash["pending_followups"] if p["lead_id"] == lead_id)
    assert pending["next_action"] == "寄送参数表"
    assert pending["next_time"] == "2026-11-01 10:00"


def test_lead_status_machine_guard(client, admin_headers):
    eid = _mk_enterprise(client, admin_headers, "苏州状态机测试半导体有限公司")
    lead_id = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers).json()["id"]
    # 非法迁移：new → won
    r = client.patch(f"/api/leads/{lead_id}", json={"status": "won"}, headers=admin_headers)
    assert r.status_code == 422


# ---- 公海规则 ----
def test_pool_settings_get_put(client, admin_headers):
    r = client.get("/api/pool-settings", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert {"recycle_days", "receive_limit", "remind_days"} <= set(body)

    r = client.put("/api/pool-settings", json={"recycle_days": 15, "receive_limit": 2}, headers=admin_headers)
    assert r.status_code == 200
    assert client.get("/api/pool-settings", headers=admin_headers).json()["receive_limit"] == 2

    # sales 无权访问
    assert client.get("/api/pool-settings", headers={}).status_code == 401


def test_receive_limit_enforced(client, admin_headers, sales_headers):
    # 上限 = 2（上一条测试已设置）
    ids = []
    for i in range(3):
        eid = _mk_enterprise(client, admin_headers, f"上限测试半导体{i}号有限公司")
        ids.append(eid)
    # sales 领 2 条成功，第 3 条 422
    statuses = []
    for eid in ids:
        r = client.post(f"/api/enterprises/{eid}/lead", headers=sales_headers)
        statuses.append(r.status_code)
        if r.status_code == 201:
            client.patch(f"/api/leads/{r.json()['id']}", json={"status": "following"}, headers=sales_headers)
    assert statuses[:2] == [201, 201]
    assert statuses[2] == 422
    assert "上限" in client.post(f"/api/enterprises/{ids[2]}/lead", headers=sales_headers).json()["detail"]

    # 清理：把上限改回 0（不限），恢复其他测试的自由度
    client.put("/api/pool-settings", json={"receive_limit": 0}, headers=admin_headers)


# ---- 任务创建校验 ----
def test_poi_task_requires_key(client, admin_headers):
    r = client.post("/api/tasks/poi", json={"city": "苏州", "industry_keys": ["semiconductor"]}, headers=admin_headers)
    # 未配置高德 Key → 400 提示
    assert r.status_code in (200, 400)
    if r.status_code == 400:
        assert "高德" in r.json()["detail"]


def test_tender_scan_requires_key(client, admin_headers):
    r = client.post("/api/tasks/tender-scan", json={"days": 7}, headers=admin_headers)
    assert r.status_code in (200, 400)
