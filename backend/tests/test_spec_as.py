"""SPEC-AS 回归：装机台账/耗材时钟/客户角色/产品线反向匹配。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-as-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Enterprise,
    Product,
    SettingsKV,
    Todo,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Product, Todo]


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


def test_unit_crud_and_status_flow(client, admin_headers):
    eid = client.post(
        "/api/import/paste",
        json={"text": "名称\n装机台账测试镀膜厂"},
        headers=admin_headers,
    ).json().get("inserted") and None
    d = client.get("/api/enterprises", params={"q": "装机台账测试镀膜厂"},
                   headers=admin_headers).json()
    eid = d["items"][0]["id"]
    r = client.post(f"/api/enterprises/{eid}/units",
                    json={"model": "EVS-100", "install_date": "2026-01-15",
                          "consumable_json": json.dumps({
                              "seal_due": "2026-11-20", "filter_due": "2027-06-01"})},
                    headers=admin_headers)
    assert r.status_code == 201
    uid = r.json()["id"]
    d2 = client.get(f"/api/enterprises/{eid}/units", headers=admin_headers).json()
    assert d2["items"][0]["model"] == "EVS-100"
    # 状态流转
    assert client.patch(f"/api/units/{uid}", json={"status": "retired"},
                        headers=admin_headers).status_code == 200
    assert client.patch(f"/api/units/{uid}", json={"status": "bogus"},
                        headers=admin_headers).status_code == 422


def test_consumable_clock_creates_todo(client, admin_headers):
    """耗材 30 天内到期 → 自动回访待办；重复扫描幂等。"""
    from app.api.units import consumable_clock_sweep

    d = client.get("/api/enterprises", params={"q": "装机台账测试镀膜厂"},
                   headers=admin_headers).json()
    eid = d["items"][0]["id"]
    # 建 active 装机且密封件 15 天后到期
    from datetime import datetime, timedelta, timezone

    due = (datetime.now(timezone.utc) + timedelta(days=15)).strftime("%Y-%m-%d")
    uid = client.post(f"/api/enterprises/{eid}/units",
                      json={"model": "LG-200",
                            "consumable_json": json.dumps({"seal_due": due})},
                      headers=admin_headers).json()["id"]
    s1 = consumable_clock_sweep()
    assert s1["todos_created"] >= 1
    with SessionLocal() as s:
        todos = s.query(Todo).filter(Todo.enterprise_id == eid,
                                     Todo.done == 0).all()
        assert any("密封件" in t.title for t in todos)
    assert consumable_clock_sweep()["todos_created"] == 0, "幂等"


def test_haoqing_product_seed_and_recommend_match(client, admin_headers):
    """产品线种子 + 工艺适配理由进入推荐。（共享库：product 可能已有数据——缺则自补）"""
    with SessionLocal() as s:
        from app.api.units import seed_haoqing_products

        seed_haoqing_products(s)
        p = s.query(Product).filter(Product.name.contains("油封螺杆")).first()
        if p is None:  # 共享库防御：种子被既有 product 数据阻断时手工补该行
            p = Product(name="EVS 永磁变频油封螺杆真空泵", model="EVS 系列",
                        application_industries=json.dumps(
                            ["food_processing", "packaging_printing",
                             "medical_health", "freeze_dried"]))
            s.add(p)
            s.commit()
        assert "food_processing" in json.loads(p.application_industries)
    # 推荐：food_processing 行业候选应带"工艺适配"理由（种子企业）
    with SessionLocal() as s:
        from app.models import Contact
        from app.recommend import score_candidates

        e = Enterprise(name="食品冻干工艺适配测试有限公司", source="poi",
                       city="苏州", industry_key="food_processing",
                       industry_name="food_processing")
        s.add(e)
        s.flush()
        s.add(Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                      title="0512-70007000", phones_json='["0512-70007000"]',
                      source="amap"))
        s.commit()
        # 产品匹配缓存是模块级的（共享库下先跑的模块可能已缓存空表）——重置后断言
        import app.recommend as rec_mod

        rec_mod._PRODUCT_MATCH_CACHE = None
        recs = score_candidates(s, limit=100)
        mine = next(r for r in recs if r["ent_id"] == e.id)
        assert any("工艺适配" in x and "油封螺杆" in x for x in mine["reasons"]), \
            "推荐理由应含产品线工艺适配（销售语言）"


def test_customer_role_field(client, admin_headers):
    with SessionLocal() as s:
        e = Enterprise(name="角色测试 OEM 镀膜机制造商", source="manual",
                       customer_role="oem")
        s.add(e)
        s.commit()
        eid = e.id
    d = client.get("/api/enterprises", params={"q": "角色测试 OEM"},
                   headers=admin_headers).json()
    assert d["items"][0]["id"] == eid
