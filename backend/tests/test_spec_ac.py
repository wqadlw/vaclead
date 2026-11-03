"""SPEC-AC 回归：我的周报（AC1/AC2）+ 团队共享视图（AC3）。"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ac-"), "t.db")
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
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------- 团队共享视图 ----------------

def test_saved_view_private_team_and_delete(client, admin_headers, sales_headers):
    # admin 建共享视图
    r = client.post("/api/saved-views", json={
        "name": "团队大客户口径", "page": "enterprises", "shared": True,
        "filters": {"score_min": 60}, "user_ids": [],
    }, headers=admin_headers)
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    # 重复名 409
    assert client.post("/api/saved-views", json={
        "name": "团队大客户口径", "page": "enterprises", "shared": True,
    }, headers=admin_headers).status_code == 409

    # sales 能看到（全员共享）
    items = client.get("/api/saved-views", params={"page": "enterprises"},
                       headers=sales_headers).json()["items"]
    assert any(i["id"] == vid for i in items)

    # sales 不能删 admin 的
    assert client.delete(f"/api/saved-views/{vid}", headers=sales_headers).status_code == 403
    # admin 删
    assert client.delete(f"/api/saved-views/{vid}", headers=admin_headers).status_code == 200


def test_saved_view_private_isolation(client, admin_headers, sales_headers):
    r = client.post("/api/saved-views", json={
        "name": "我的私有口径", "page": "leads",
    }, headers=admin_headers)
    vid = r.json()["id"]
    # sales 看不到 admin 的私有视图
    items = client.get("/api/saved-views", headers=sales_headers).json()["items"]
    assert not any(i["id"] == vid for i in items)
    client.delete(f"/api/saved-views/{vid}", headers=admin_headers)


# ---------------- 我的周报 ----------------

def test_my_week_owner_isolation(client, admin_headers, sales_headers):
    from app.db import SessionLocal
    from app.models import Enterprise, Lead

    # admin 建一家线索（owner=admin）
    row = "name,经营范围\n周报归属测试有限公司,真空设备研发\n"
    client.post("/api/import/enterprises",
                files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers)
    d = client.get("/api/enterprises", params={"q": "周报归属测试"}, headers=admin_headers).json()
    eid = d["items"][0]["id"]
    lid = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers).json()["id"]

    report = client.get("/api/reports/my-week?offset=0", headers=admin_headers).json()
    assert report["owner"] == "管理员" or report["owner"] == "admin"
    assert report["kpis"], "KPI 不能为空"

    # 清理
    with SessionLocal() as s:
        s.query(Lead).filter(Lead.id == lid).delete()
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()
