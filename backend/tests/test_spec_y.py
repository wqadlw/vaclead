"""SPEC-Y 回归：批量打标签（Y1）+ 批量退公海（Y2）。"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-y-"), "t.db")
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


@pytest.fixture(scope="module")
def three_ent_ids(client, admin_headers) -> list[int]:
    row = (
        "name,经营范围\n"
        "批量标签甲有限公司,真空设备研发\n"
        "批量标签乙有限公司,真空设备研发\n"
        "批量标签丙有限公司,真空设备研发\n"
    )
    client.post("/api/import/enterprises",
                files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers)
    d = client.get("/api/enterprises", params={"q": "批量标签"}, headers=admin_headers).json()
    return sorted(i["id"] for i in d["items"])


# ---------------- Y1 批量打标签 ----------------

def test_batch_tags_append_preserves_existing(client, admin_headers, three_ent_ids):
    # 先给甲一家单打"已有标签"
    client.put(f"/api/enterprises/{three_ent_ids[0]}/tags",
               json={"tags": ["已有标签"]}, headers=admin_headers)
    # 批量 append：三家都打"重点客户"
    r = client.post("/api/enterprises/batch-tags",
                    json={"ids": three_ent_ids, "tags": ["重点客户"], "mode": "append"},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 3

    # 甲：append 保留已有标签
    d = client.get(f"/api/enterprises/{three_ent_ids[0]}", headers=admin_headers).json()
    assert set(d["tags"]) == {"已有标签", "重点客户"}
    # 乙：无已有标签，直接写入
    d2 = client.get(f"/api/enterprises/{three_ent_ids[1]}", headers=admin_headers).json()
    assert d2["tags"] == ["重点客户"]

    # 空标签 422
    assert client.post("/api/enterprises/batch-tags",
                       json={"ids": three_ent_ids, "tags": []},
                       headers=admin_headers).status_code == 422


def test_batch_tags_append_over_limit_skips(client, admin_headers, three_ent_ids):
    # 甲先打满 7 个标签，append 3 个新标签会超 8 → 跳过
    client.put(f"/api/enterprises/{three_ent_ids[0]}/tags",
               json={"tags": [f"标签{i}" for i in range(7)]}, headers=admin_headers)
    r = client.post("/api/enterprises/batch-tags",
                    json={"ids": three_ent_ids, "tags": ["新A", "新B", "新C"],
                          "mode": "append"},
                    headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["updated"] == 2  # 乙丙成功
    assert len(d["skipped"]) == 1 and "超 8 个" in d["skipped"][0]["reason"]
    # 被跳过的企业标签未被部分写入
    detail = client.get(f"/api/enterprises/{three_ent_ids[0]}",
                        headers=admin_headers).json()
    assert "新A" not in detail["tags"]


def test_batch_tags_replace_mode(client, admin_headers, three_ent_ids):
    client.put(f"/api/enterprises/{three_ent_ids[0]}/tags",
               json={"tags": ["旧标签"]}, headers=admin_headers)
    r = client.post("/api/enterprises/batch-tags",
                    json={"ids": [three_ent_ids[0]], "tags": ["替换后"], "mode": "replace"},
                    headers=admin_headers)
    assert r.status_code == 200
    d = client.get(f"/api/enterprises/{three_ent_ids[0]}", headers=admin_headers).json()
    assert d["tags"] == ["替换后"]
    # 清理
    client.put(f"/api/enterprises/{three_ent_ids[0]}/tags",
               json={"tags": []}, headers=admin_headers)


# ---------------- Y2 批量退公海 ----------------

def test_batch_pool_guards_and_release(client, admin_headers, sales_headers, three_ent_ids):
    # 三家都转线索（admin 转即归 admin 所有）
    lead_ids: list[int] = []
    for eid in three_ent_ids:
        r = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers)
        assert r.status_code == 201, r.text
        lead_ids.append(r.json()["id"])

    # sales 退 admin 的线索 → 全部 skipped（非本人）
    r = client.post("/api/leads/batch-pool", json={"ids": lead_ids}, headers=sales_headers)
    assert r.status_code == 200
    assert r.json()["moved"] == 0
    assert len(r.json()["skipped"]) == 3

    # admin 退第一家（following ✓）+ 已 lost 的（状态守卫跳过；转 lost 需合法放弃原因）
    client.patch(f"/api/leads/{lead_ids[1]}",
                 json={"status": "lost", "lost_reason": "no_need"},
                 headers=admin_headers)
    r = client.post("/api/leads/batch-pool",
                    json={"ids": [lead_ids[0], lead_ids[1]]}, headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["moved"] == 1
    assert len(d["skipped"]) == 1 and "lost" in d["skipped"][0]["reason"]

    from app.db import SessionLocal
    from app.models import Enterprise, Lead

    with SessionLocal() as s:
        p = s.get(Lead, lead_ids[0])
        assert p.status == "pool" and p.owner_id is None and p.claimed_at is None
        lost_lead = s.get(Lead, lead_ids[1])
        assert lost_lead.status == "lost"
        # 清理
        for lid in lead_ids:
            s.query(Lead).filter(Lead.id == lid).delete()
        for eid in three_ent_ids:
            s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()
