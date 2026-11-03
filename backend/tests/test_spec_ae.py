"""SPEC-AE 回归：客户健康分 + 生命周期自动升降级。"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ae-"), "t.db")
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


def _mk_won_lead(client, headers, name: str) -> int:
    row = f"name,经营范围\n{name},真空设备研发\n"
    client.post("/api/import/enterprises",
                files={"file": ("m.csv", row.encode(), "text/csv")}, headers=headers)
    d = client.get("/api/enterprises", params={"q": name}, headers=headers).json()
    eid = d["items"][0]["id"]
    lid = client.post(f"/api/enterprises/{eid}/lead", headers=headers).json()["id"]
    client.patch(f"/api/leads/{lid}", json={"status": "following"}, headers=headers)
    client.patch(f"/api/leads/{lid}", json={"status": "intent"}, headers=headers)
    client.patch(f"/api/leads/{lid}", json={"status": "won"}, headers=headers)
    return lid


def test_health_score_calculation(client, admin_headers):
    """健康分基于可量化行为信号：有信号+有跟进+有合同=高分。"""
    from app.db import SessionLocal
    from app.health import compute_health_score
    from app.models import Contract, Enterprise, Followup, Lead, Signal

    lid = _mk_won_lead(client, admin_headers, "健康分高分测试有限公司")
    with SessionLocal() as s:
        lead = s.get(Lead, lid)
        eid = lead.enterprise_id
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        s.add(Signal(enterprise_id=eid, type="tender_win",
                     title="健康分测试信号", url="", base_score=70,
                     created_at=now))
        s.add(Followup(lead_id=lid, content="电话跟进", created_at=now))
        s.add(Contract(lead_id=lid, name="测试合同", amount_wan=50, status="active"))
        s.commit()

        score = compute_health_score(s, lead)
        assert score >= 70, f"三重正向信号健康分应 ≥70，实际 {score}"

        s.query(Signal).filter(Signal.enterprise_id == eid).delete()
        s.query(Followup).filter(Followup.lead_id == lid).delete()
        s.query(Contract).filter(Contract.lead_id == lid).delete()
        s.delete(lead)
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()


def test_lifecycle_auto_downgrade_upgrades(client, admin_headers):
    """customer_success 健康分<30 → at_risk；at_risk ≥60 → customer_success。"""
    from app.db import SessionLocal
    from app.health import auto_lifecycle
    from app.models import Enterprise, Followup, Lead, Signal

    with SessionLocal() as s:
        # 造一个 customer_success 线索但健康分应为低（无信号+超期未跟进）
        ent = Enterprise(name="生命周期测试有限公司", source="csv")
        s.add(ent)
        s.flush()
        lead = Lead(enterprise_id=ent.id, status="won", owner_id=1,
                    lifecycle_stage="customer_success", last_followed_at="2026-08-01T00:00:00")
        s.add(lead)
        s.commit()

        new_stage = auto_lifecycle(s, lead)
        assert new_stage == "at_risk", f"健康分低的 customer_success 应降 at_risk，实际 {new_stage}"

        # 回升：模拟健康分升高（添加新跟进+信号）
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        s.add(Followup(lead_id=lead.id, content="回访", created_at=now))
        s.add(Signal(enterprise_id=ent.id, type="tender", title="新信号", url="",
                     base_score=60, created_at=now))
        lead.lifecycle_stage = "at_risk"
        s.commit()

        new_stage2 = auto_lifecycle(s, lead)
        assert new_stage2 == "customer_success", f"at_risk 好转应升回，实际 {new_stage2}"

        s.query(Signal).filter(Signal.enterprise_id == ent.id).delete()
        s.query(Followup).filter(Followup.lead_id == lead.id).delete()
        s.delete(lead)
        s.delete(ent)
        s.commit()


def test_batch_health_refresh(client, admin_headers):
    from app.db import SessionLocal
    from app.health import batch_health_refresh
    from app.models import Lead

    with SessionLocal() as s:
        stats = batch_health_refresh(s)
        assert stats["total"] >= 0


def test_won_hook_and_at_risk_filter(client, admin_headers):
    """赢单自动升 customer_success；?at_risk=1 只回预警线索；序列化含健康分字段。"""
    lid = _mk_won_lead(client, admin_headers, "露出层测试有限公司")
    d = client.get("/api/leads", params={"at_risk": 1}, headers=admin_headers).json()
    ids = [it["id"] for it in d["items"]]
    item = next((it for it in d["items"] if it["id"] == lid), None)
    # 新赢单+刚导入→健康分不高（无信号-30），但 lifecycle 应已升 customer_success
    if item is None:
        # 该线索未被预警筛中（分数≥40），用 lifecycle 筛选验证
        d2 = client.get(
            "/api/leads", params={"lifecycle": "customer_success"}, headers=admin_headers
        ).json()
        item = next((it for it in d2["items"] if it["id"] == lid), None)
    assert item is not None, "赢单线索应处于 customer_success 生命周期"
    assert item["lifecycle_stage"] == "customer_success"
    assert isinstance(item["health_score"], int) and 0 <= item["health_score"] <= 100
    assert all(
        it["lifecycle_stage"] == "at_risk" or it["health_score"] < 40 for it in d["items"]
    ), "at_risk=1 视图只应返回预警线索"
