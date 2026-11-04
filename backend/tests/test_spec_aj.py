"""SPEC-AJ 回归：客户地图 geo 端点（bbox/with_phone/industry/limit 防炸）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-aj-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    SettingsKV,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact]


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


def _seed(name: str, lat: float | None, lng: float | None, *, industry: str | None = None,
          phone: str | None = None):
    with SessionLocal() as s:
        e = Enterprise(name=name, source="poi", lat=lat, lng=lng,
                       industry_key=industry, industry_name=industry, city="测试市")
        s.add(e)
        s.flush()
        if phone:
            s.add(Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                          title=phone, phones_json=f'["{phone}"]', source="amap"))
        s.commit()
        return e.id


def test_geo_endpoint_filters(client, admin_headers):
    with_phone_id = _seed("苏州地图有电话企业", 31.30, 120.58, industry="vacuum", phone="0512-66668888")
    no_phone_id = _seed("苏州地图无电话企业", 31.32, 120.60, industry="vacuum")
    nocoord_id = _seed("无坐标企业不返回", None, None, industry="vacuum")

    d = client.get("/api/enterprises/geo", headers=admin_headers).json()
    ids = [it["id"] for it in d["items"]]
    assert with_phone_id in ids and no_phone_id in ids
    assert nocoord_id not in ids, "无坐标企业不应出现在地图"
    item = next(it for it in d["items"] if it["id"] == with_phone_id)
    assert item["has_phone"] is True and item["lat"] == 31.30

    # bbox 过滤（只圈无电话企业附近）
    d2 = client.get(
        "/api/enterprises/geo",
        params={"south": 31.31, "north": 31.33, "west": 120.59, "east": 120.61},
        headers=admin_headers,
    ).json()
    ids2 = [it["id"] for it in d2["items"]]
    assert no_phone_id in ids2 and with_phone_id not in ids2

    # with_phone=1
    d3 = client.get("/api/enterprises/geo", params={"with_phone": 1}, headers=admin_headers).json()
    ids3 = [it["id"] for it in d3["items"]]
    assert with_phone_id in ids3 and no_phone_id not in ids3

    # industry 筛选
    _seed("苏州环保地图企业", 31.35, 120.62, industry="environmental", phone="0512-77779999")
    d4 = client.get("/api/enterprises/geo", params={"industry_key": "environmental"},
                    headers=admin_headers).json()
    assert all(it["industry_name"] == "environmental" for it in d4["items"]) and d4["items"]


def test_geo_limit_cap(client, admin_headers):
    """limit ≤2000 防炸。"""
    r = client.get("/api/enterprises/geo", params={"limit": 99999}, headers=admin_headers)
    assert r.status_code == 200  # 内部 min(limit, 2000) 生效不报错
