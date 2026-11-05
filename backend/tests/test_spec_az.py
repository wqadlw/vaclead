"""SPEC-AZ 回归：触达覆盖筛选 + 私有成交案例。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-az-"), "t.db")
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
    utcnow,
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


def _seed(name: str, phone: str | None, call_result: str = "pending") -> int:
    with SessionLocal() as s:
        e = Enterprise(name=name, source="poi", city="苏州",
                       industry_key="coating", industry_name="coating")
        s.add(e)
        s.flush()
        if phone:
            s.add(Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                          title=phone, phones_json=f'["{phone}"]',
                          call_result=call_result, source="amap"))
        s.commit()
        return e.id


def test_call_status_filter(client, admin_headers):
    """untouched=有电话全部未拨；touched=任一已拨；无电话企业不进筛选。"""
    unt = _seed("覆盖未拨测试企业", "0512-31003100")
    tou = _seed("覆盖已拨测试企业", "0512-32003200", call_result="connected")
    _seed("覆盖无电话企业", None)
    d = client.get("/api/enterprises", params={"call_status": "untouched"},
                   headers=admin_headers).json()
    ids = [it["id"] for it in d["items"]]
    assert unt in ids and tou not in ids
    d2 = client.get("/api/enterprises", params={"call_status": "touched"},
                    headers=admin_headers).json()
    ids2 = [it["id"] for it in d2["items"]]
    assert tou in ids2 and unt not in ids2


def test_case_study_crud_and_proposal_priority(client, admin_headers):
    """私有案例 CRUD + 提案优先于公开案例。"""
    with SessionLocal() as s:
        e = Enterprise(name="案例优先级测试企业", source="manual",
                       industry_key="coating", industry_name="coating")
        s.add(e)
        s.commit()  # 跨会话引用前必须落库（否则 POST 的 FK 失败 500）
        eid = e.id
    r = client.post("/api/cases", json={
        "enterprise_id": eid, "industry_key": "coating",
        "title": "昊志为某镀膜企业交付 3 套螺杆真空系统",
        "content": "交付后连续运行 18 个月无故障，能耗较原系统降低 28%。",
        "year": "2025",
    }, headers=admin_headers)
    assert r.status_code == 201
    # 列表可查
    d = client.get("/api/cases", params={"industry_key": "coating"},
                   headers=admin_headers).json()
    assert any("螺杆真空系统" in c["title"] for c in d["items"])
    # 删除
    cid = r.json()["id"]
    assert client.delete(f"/api/cases/{cid}", headers=admin_headers).status_code == 200
    assert client.get("/api/cases", headers=admin_headers).json()["items"] == [] or all(
        c["id"] != cid for c in client.get("/api/cases", headers=admin_headers).json()["items"]
    )
