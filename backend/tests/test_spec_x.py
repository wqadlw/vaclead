"""SPEC-X 回归：跟进健康度（X3）+ 阶段转化率（X1）+ 来源 ROI（X2）。"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-x-"), "t.db")
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


def _mk_lead(client, headers, name: str) -> int:
    row = f"name,经营范围\n{name},真空设备研发\n"
    client.post("/api/import/enterprises",
                files={"file": ("m.csv", row.encode(), "text/csv")}, headers=headers)
    d = client.get("/api/enterprises", params={"q": name}, headers=headers).json()
    eid = d["items"][0]["id"]
    return client.post(f"/api/enterprises/{eid}/lead", headers=headers).json()["id"], eid


# ---------------- X3 跟进健康度 ----------------

def test_followup_health_stale_detection(client, admin_headers):
    from app.db import SessionLocal
    from app.models import Lead, Todo

    lead_id, _ = _mk_lead(client, admin_headers, "健康度停滞测试有限公司")
    lead_id2, _ = _mk_lead(client, admin_headers, "健康度活跃测试有限公司")

    # lead1：上周跟进（停滞）；lead2：刚刚跟进（健康）
    with SessionLocal() as s:
        l1 = s.get(Lead, lead_id)
        l1.last_followed_at = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        l2 = s.get(Lead, lead_id2)
        l2.last_followed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        s.commit()

    summary = client.get("/api/dashboard/summary", headers=admin_headers).json()
    h = summary["health"]
    assert h["active_leads"] >= 2
    assert h["stale_7d"] >= 1
    assert h["stale_14d"] <= h["stale_7d"]

    # 有未来待办的停滞线索不算停滞（有人在管）
    with SessionLocal() as s:
        s.add(Todo(lead_id=lead_id, title="已安排回访", due_date="2026-12-01"))
        s.commit()
    summary2 = client.get("/api/dashboard/summary", headers=admin_headers).json()
    # lead1 不再计入停滞（待办兜底）
    assert summary2["health"]["stale_7d"] == summary["health"]["stale_7d"] - 1

    # 清理
    with SessionLocal() as s:
        for lid in (lead_id, lead_id2):
            s.query(Todo).filter(Todo.lead_id == lid).delete()
            lead = s.get(Lead, lid)
            if lead:
                eid = lead.enterprise_id
                s.query(Lead).filter(Lead.id == lid).delete()
                from app.models import Enterprise

                s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()


# ---------------- X1 阶段转化率 + X2 来源 ROI ----------------

def test_conversion_and_source_roi(client, admin_headers):
    from app.db import SessionLocal
    from app.models import AuditLog, Lead

    # 造流转：new→following→intent→won（走 API 触发审计钩子）
    lead_id, _ = _mk_lead(client, admin_headers, "转化率测试半导体有限公司")
    client.patch(f"/api/leads/{lead_id}", json={"status": "following"},
                 headers=admin_headers)
    client.patch(f"/api/leads/{lead_id}", json={"status": "intent"},
                 headers=admin_headers)
    client.patch(f"/api/leads/{lead_id}/opportunity",
                 json={"amount_wan": 200}, headers=admin_headers)
    client.patch(f"/api/leads/{lead_id}", json={"status": "won"},
                 headers=admin_headers)

    conv = client.get("/api/analytics/conversion", headers=admin_headers).json()
    edges = {(t["from"], t["to"]): t["edges"] for t in conv["conversion"]["transitions"]}
    # 转线索即 following 态（无 new→following 边）；真实业务流为 following→intent→won
    assert edges[("following", "intent")] >= 1
    assert edges[("intent", "won")] >= 1

    # 来源 ROI：赢单 1 笔/金额 200
    roi = {r["source"]: r for r in conv["source_roi"]}
    csv_row = roi.get("csv")
    assert csv_row is not None and csv_row["won"] >= 1
    assert csv_row["won_amount_wan"] >= 200
    assert csv_row["win_rate"] is not None and 0 < csv_row["win_rate"] <= 1

    # 清理
    with SessionLocal() as s:
        lead = s.get(Lead, lead_id)
        eid = lead.enterprise_id
        s.query(AuditLog).filter(
            AuditLog.entity == "lead", AuditLog.entity_id == lead_id).delete()
        s.query(Lead).filter(Lead.id == lead_id).delete()
        from app.models import Enterprise

        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()
