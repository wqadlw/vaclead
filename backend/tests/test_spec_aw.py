"""SPEC-AW 回归：推荐触达快标（call_state 聚合 + mark-call 端点）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-aw-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Contact,
    Enterprise,
    SettingsKV,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact, AuditLog]


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


def _seed_with_phone(session, name: str, phone: str) -> tuple[int, int]:
    e = Enterprise(name=name, source="poi", city="苏州",
                   industry_key="coating", industry_name="coating")
    session.add(e)
    session.flush()
    c = Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                title=phone, phones_json=f'["{phone}"]', source="amap")
    session.add(c)
    session.commit()
    return e.id, c.id


def test_recommend_call_state_aggregation(client, admin_headers):
    """推荐输出 call_state 聚合态（interested 优先）+ contact_id。"""
    with SessionLocal() as s:
        eid, cid = _seed_with_phone(s, "快标聚合测试镀膜有限公司", "0512-91009100")
    d = client.get("/api/recommendations", params={"limit": 100},
                   headers=admin_headers).json()
    mine = next(r for r in d["items"] if r["ent_id"] == eid)
    assert mine["call_state"] == "pending" and mine["contact_id"] == cid
    # 标 connected → 聚合态跟随
    client.post(f"/api/recommendations/{eid}/mark-call",
                json={"result": "connected"}, headers=admin_headers)
    d2 = client.get("/api/recommendations", params={"limit": 100},
                    headers=admin_headers).json()
    assert next(r for r in d2["items"] if r["ent_id"] == eid)["call_state"] == "connected"


def test_mark_call_validation_and_contact_update(client, admin_headers):
    """快标五态校验；结果写主联系人；非法 422；无电话企业 404。"""
    with SessionLocal() as s:
        eid, cid = _seed_with_phone(s, "快标校验测试企业", "0512-92009200")
        nophone = Enterprise(name="快标无电话企业", source="manual", city="苏州")
        s.add(nophone)
        s.commit()
        nid = nophone.id
    r = client.post(f"/api/recommendations/{eid}/mark-call",
                    json={"result": "interested"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["call_result"] == "interested"
    with SessionLocal() as s:
        c = s.get(Contact, cid)
        assert c.call_result == "interested" and c.called_at
    assert client.post(f"/api/recommendations/{eid}/mark-call",
                       json={"result": "bogus"}, headers=admin_headers).status_code == 422
    assert client.post(f"/api/recommendations/{nid}/mark-call",
                       json={"result": "connected"}, headers=admin_headers).status_code == 404
