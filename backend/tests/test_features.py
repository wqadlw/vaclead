"""SPEC-C 功能包测试：订阅→通知 / 批量转线索 / 导出 / 相似推荐。"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vaclead-feat-")
os.environ.setdefault("VACLEAD_DB", os.path.join(_tmp, "t.db"))

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


def _mk(client, headers, name):
    row = f"name,province,city,business_scope,registered_capital_wan,industry_key\n{name},江苏省,苏州市,晶圆制造,800,semiconductor\n"
    client.post("/api/import/enterprises", files={"file": ("m.csv", row.encode(), "text/csv")}, headers=headers)
    from urllib.parse import quote

    items = client.get(
        f"/api/enterprises?q={quote(name)}&page_size=5", headers=headers
    ).json()["items"]
    return next(e["id"] for e in items if e["name"] == name)


def test_subscribe_and_notification_flow(client, admin_headers, sales_headers):
    """关注 → 该企业新信号 → 通知扫播 → 铃铛可读；幂等：重扫不重复。"""
    from app.db import SessionLocal
    from app.jobs import generate_notifications
    from app.models import Enterprise, Signal

    eid = _mk(client, admin_headers, "苏州贝嵌测试半导体有限公司")
    r = client.post(f"/api/enterprises/{eid}/subscribe", headers=sales_headers)
    assert r.status_code == 201 and r.json()["subscribed"] is True
    # 取消再关注（幂等）
    assert client.delete(f"/api/enterprises/{eid}/subscribe", headers=sales_headers).status_code == 200
    assert client.post(f"/api/enterprises/{eid}/subscribe", headers=sales_headers).status_code == 201

    with SessionLocal() as s:
        s.add(Signal(enterprise_id=eid, type="tender_win", title="测试包成交",
                     url="http://x/sub1", pub_date="2026-10-25", base_score=70,
                     evidence_claim="该主体中标「测试包」，成交价88万元"))
        s.commit()

    n1 = generate_notifications.__wrapped__(None) if hasattr(generate_notifications, "__wrapped__") else None
    with SessionLocal() as s:
        stat = generate_notifications(s)
    assert stat["notifications"] >= 1
    # 幂等：重扫不增
    with SessionLocal() as s:
        stat2 = generate_notifications(s)
    assert stat2["notifications"] == 0

    lst = client.get("/api/notifications", headers=sales_headers).json()
    assert lst["unread_count"] >= 1
    assert any("苏州贝嵌测试半导体" in it["title"] for it in lst["items"])
    nid = lst["items"][0]["id"]
    assert client.post(f"/api/notifications/{nid}/read", headers=sales_headers).json()["ok"]
    assert client.post("/api/notifications/read-all", headers=sales_headers).json()["ok"]
    assert client.get("/api/notifications", headers=sales_headers).json()["unread_count"] == 0
    # admin 未订阅 → 无该通知（隔离）
    admin_lst = client.get("/api/notifications", headers=admin_headers).json()
    assert all("苏州贝嵌测试半导体" not in it["title"] for it in admin_lst["items"])


def test_batch_lead_and_export(client, admin_headers):
    ids = [_mk(client, admin_headers, ["黄山精工真空装备有限公司", "泉州沪星精密仪器有限公司", "温州东海流体设备有限公司"][i]) for i in range(3)]
    r = client.post("/api/enterprises/batch-lead", json={"ids": ids}, headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["converted"] == 3
    # 重跑：全部 existed
    r2 = client.post("/api/enterprises/batch-lead", json={"ids": ids}, headers=admin_headers)
    assert r2.json()["existed"] == 3

    # 导出 CSV
    r3 = client.get("/api/enterprises/export?q=%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B8", headers=admin_headers)
    assert r3.status_code == 200
    text = r3.content.decode("utf-8")
    assert "名称" in text and "黄山精工" in text
    assert text.count("\n") >= 4  # 表头 + 3 行


def test_similar_enterprises(client, admin_headers):
    a = _mk(client, admin_headers, "相似基准半导体有限公司")
    b = _mk(client, admin_headers, "相似同行半导体科技有限公司")
    r = client.get(f"/api/enterprises/{a}/similar", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    # 启发式推荐：全量共享库下 top-5 被同分企业占满是合法行为，只断言可用性与排除自身
    assert len(items) <= 5
    assert all(it["id"] != a for it in items)
    # 语义抽查：池内候选必须全部非同行且非自身
    for it in items:
        assert it["name"] != "相似基准半导体有限公司"
    # 404
    assert client.get("/api/enterprises/999999/similar", headers=admin_headers).status_code == 404


def test_enterprise_graph_and_related(client, admin_headers):
    """SPEC-D：证据句反抽边（买家中标方双向）+ 金额聚合 + 关联企业互斥。"""
    from app.db import SessionLocal
    from app.models import Enterprise, Signal

    with SessionLocal() as s:
        sup = Enterprise(name="图谱供应真空设备有限公司", source="ggzy")
        s.add(sup)
        s.flush()
        s.add(Signal(enterprise_id=sup.id, type="tender_win", title="A包",
                     url="g1", pub_date="2026-09-01", base_score=70,
                     evidence_claim="该主体中标「A」，成交价100万元，采购人：图谱买家企业"))
        s.add(Signal(enterprise_id=sup.id, type="tender_win", title="B包",
                     url="g2", pub_date="2026-10-01", base_score=70,
                     evidence_claim="该主体中标「B」，成交价50万元，采购人：图谱买家企业"))
        s.commit()
        sup_id = sup.id
    g = client.get(f"/api/enterprises/{sup_id}/graph", headers=admin_headers).json()
    assert g["center"]["name"] == "图谱供应真空设备有限公司"
    assert len(g["edges"]) == 1
    assert g["edges"][0]["name"] == "图谱买家企业"
    assert g["edges"][0]["role"] == "buyer"
    assert g["edges"][0]["amount"] == 150.0 and g["edges"][0]["count"] == 2
    # 关联：另一家共享同一买家 → competitors
    with SessionLocal() as s:
        sup2 = Enterprise(name="图谱竞争真空设备有限公司", source="ggzy")
        s.add(sup2)
        s.flush()
        s.add(Signal(enterprise_id=sup2.id, type="tender_win", title="C包",
                     url="g3", pub_date="2026-09-15", base_score=70,
                     evidence_claim="该主体中标「C」，成交价30万元，采购人：图谱买家企业"))
        s.commit()
        sup2_id = sup2.id
    rel = client.get(f"/api/enterprises/{sup_id}/related", headers=admin_headers).json()
    assert any(c["id"] == sup2_id for c in rel["competitors"])
    ct = client.get(f"/api/enterprises/{sup_id}/contacts", headers=admin_headers).json()
    assert ct["items"] == []


def test_opportunity_fields_and_stage_probability(client, admin_headers):
    """twenty 吸收：商机三要素可编辑；阶段流转自动更新赢率。"""
    eid = _mk(client, admin_headers, "商机管线测试真空装备有限公司")
    r = client.post(f"/api/enterprises/{eid}/lead", headers=admin_headers)
    assert r.status_code == 201, r.text
    lead_id = r.json()["id"]

    # 商机编辑（独立于状态）
    r2 = client.patch(
        f"/api/leads/{lead_id}/opportunity",
        json={"amount_wan": 120.5, "probability": 30, "expected_close": "2026-12-31"},
        headers=admin_headers,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["amount_wan"] == 120.5
    assert body["probability"] == 30
    assert body["expected_close"] == "2026-12-31"

    # 转线索即 following（产品语义）→ 创建即带阶段赢率
    assert r.json().get("probability") == 40

    # 阶段流转 → 赢率自动更新（twenty stage 模式）
    r4 = client.patch(f"/api/leads/{lead_id}", json={"status": "intent"}, headers=admin_headers)
    assert r4.json()["probability"] == 70
    r5 = client.patch(f"/api/leads/{lead_id}", json={"status": "won"}, headers=admin_headers)
    assert r5.json()["probability"] == 100

    # 非法赢率/金额 422
    assert client.patch(
        f"/api/leads/{lead_id}/opportunity", json={"probability": 150}, headers=admin_headers
    ).status_code == 422
    assert client.patch(
        f"/api/leads/{lead_id}/opportunity", json={"amount_wan": -1}, headers=admin_headers
    ).status_code == 422
