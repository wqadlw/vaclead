"""SPEC-J 回归：审计(J1)/API Key(J1)/加权预测(J6)/订阅规则(J7)。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-j-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_enterprise(client, headers, name: str) -> int:
    row = f"name,province,city,business_scope,registered_capital_wan,industry_key\n{name},江苏省,苏州市,真空设备制造,800,semiconductor\n"
    r = client.post(
        "/api/import/enterprises", files={"file": ("m.csv", row.encode(), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    from urllib.parse import quote

    items = client.get(f"/api/enterprises?q={quote(name)}&page_size=5", headers=headers).json()["items"]
    return next(e["id"] for e in items if e["name"] == name)


# ---------------- J1 审计 ----------------

def test_audit_insert_on_import(client, admin_headers):
    eid = _mk_enterprise(client, admin_headers, "审计建档回归半导体有限公司")
    items = client.get(
        "/api/audit", params={"entity": "enterprise", "action": "insert"}, headers=admin_headers
    ).json()["items"]
    assert any(i["entity_id"] == eid and i["user"] for i in items), items[:3]


def test_audit_update_diff(client, admin_headers):
    eid = _mk_enterprise(client, admin_headers, "审计改档回归半导体有限公司")
    r = client.patch(
        f"/api/enterprises/{eid}/quick-update",
        json={"field": "address", "value": "苏州市工业园区 scheming 路 1 号"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    items = client.get(
        "/api/audit", params={"entity": "enterprise", "action": "update"}, headers=admin_headers
    ).json()["items"]
    row = next(i for i in items if i["entity_id"] == eid)
    assert "address" in row["changes"]
    before, after = row["changes"]["address"]
    assert "工业园区" in after and "工业园区" not in before


def test_system_insert_not_audited(client, admin_headers):
    """采集/任务路径（actor=0）的建档不进审计——纯噪音。"""
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        ent = Enterprise(name="系统建档不该审计有限公司", source="ggzy")
        s.add(ent)
        s.commit()
        eid = ent.id
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()
    items = client.get(
        "/api/audit", params={"entity": "enterprise", "action": "insert"}, headers=admin_headers
    ).json()["items"]
    assert not any(i["entity_id"] == eid for i in items)


def test_audit_cleanup_keeps_latest(client, admin_headers):
    """滚动清理：保留最近 keep 条。"""
    from app.db import SessionLocal
    from app.jobs import audit_cleanup
    from app.models import AuditLog

    with SessionLocal() as s:
        for i in range(5):
            s.add(AuditLog(user_id=0, action="update", entity="enterprise",
                           entity_id=1, changes_json="{}"))
        s.commit()
        total = s.query(AuditLog).count()
        out = audit_cleanup(s, keep=3)
        assert out["removed"] == max(0, total - 3)
        assert s.query(AuditLog).count() == min(total, 3)


# ---------------- J1 API Key ----------------

def test_api_key_read_scope_enforced(client, admin_headers):
    r = client.post("/api/api-keys", json={"name": "只读集成", "scopes": "read"},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    raw = r.json()["key"]
    assert raw.startswith("vla_")

    # 只读 Key 可查列表
    r = client.get("/api/enterprises", headers={"X-Api-Key": raw})
    assert r.status_code == 200, r.text
    # 只读 Key 写操作 403
    r = client.post("/api/api-keys", json={"name": "x"}, headers={"X-Api-Key": raw})
    assert r.status_code == 403
    # 错误 Key 401（合法 Key 反转，哈希必然不命中；避免硬编码 Key 字面量）
    bogus = raw[::-1]
    r = client.get("/api/enterprises", headers={"X-Api-Key": bogus})
    assert r.status_code == 401

    # 吊销后 401
    listed = client.get("/api/api-keys", headers=admin_headers).json()["items"]
    kid = next(k["id"] for k in listed if k["name"] == "只读集成")
    r = client.delete(f"/api/api-keys/{kid}", headers=admin_headers)
    assert r.status_code == 200
    r = client.get("/api/enterprises", headers={"X-Api-Key": raw})
    assert r.status_code == 401


# ---------------- J6 加权管道预测 ----------------

def test_forecast_weighted_pipeline_and_target(client, admin_headers):
    from datetime import datetime, timezone

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    eid = _mk_enterprise(client, admin_headers, "预测回归半导体有限公司")
    lead_id = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers).json()["id"]
    r = client.patch(
        f"/api/leads/{lead_id}/opportunity",
        json={"amount_wan": 100, "probability": 50, "expected_close": f"{month}-15"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text

    r = client.put("/api/my-target", json={"month": month, "amount_wan": 200},
                   headers=admin_headers)
    assert r.status_code == 200, r.text

    summary = client.get("/api/dashboard/summary", headers=admin_headers).json()
    fc = summary["forecast"]
    assert fc["target"] == 200
    # 至少包含本用例商机贡献的 50 万加权值（共享库中可能有其他商机）
    assert fc["current_month_weighted"] >= 50.0
    assert 0 < fc["attain"] <= 1


# ---------------- J7 关键词/地区订阅规则 ----------------

def test_subscription_rule_matches_and_idempotent(client, admin_headers):
    from app.db import SessionLocal
    from app.jobs import mark_watermark, run_subscription_rules
    from app.models import Enterprise, Notification, Signal

    r = client.post("/api/subscription-rules",
                    json={"kind": "keyword", "value": "分子泵订阅回归"},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    # 重复订阅 409
    r = client.post("/api/subscription-rules",
                    json={"kind": "keyword", "value": "分子泵订阅回归"},
                    headers=admin_headers)
    assert r.status_code == 409

    with SessionLocal() as s:
        ent = Enterprise(name="订阅规则回归半导体有限公司", source="ggzy")
        s.add(ent)
        s.flush()
        s.add(Signal(enterprise_id=ent.id, type="tender", title="北京大学分子泵订阅回归采购公告",
                     url="https://sub.test/1", pub_date="2026-10-29", base_score=50))
        s.commit()
        ent_id = ent.id

        out = run_subscription_rules(s)
        assert out["matched"] >= 1
        s.flush()  # autoflush=False：pending 通知须显式刷出再查
        notes = (
            s.query(Notification)
            .filter(Notification.ref_type == "sub_signal", Notification.user_id == 1)
            .all()
        )
        assert any("分子泵订阅回归" in n.title or "分子泵订阅回归" in n.body for n in notes)
        # 幂等：重扫不重复
        out2 = run_subscription_rules(s)
        matched_notes = (
            s.query(Notification)
            .filter(Notification.ref_type == "sub_signal", Notification.user_id == 1)
            .count()
        )
        assert out2["matched"] == 0 or matched_notes == len(notes)

        mark_watermark(s, "subscription_rules", "ok", out)
        s.query(Signal).filter(Signal.enterprise_id == ent_id).delete()
        s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
        s.commit()

    r = client.delete(f"/api/subscription-rules/{rid}", headers=admin_headers)
    assert r.status_code == 200
