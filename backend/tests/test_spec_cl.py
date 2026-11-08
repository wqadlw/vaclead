"""SPEC-CL 回归：手工录入联系人——查到电话直接记。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cl-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Contact, Enterprise  # noqa: E402
from app.phones import load_phones  # noqa: E402

init_db(seed=False)


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in (Enterprise, Contact):
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in (Enterprise, Contact):
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
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


def test_cl_manual_contact(client, admin_headers):
    e = Enterprise(name="CL手工录入目标有限公司", source="csv")
    s_db = SessionLocal()
    s_db.add(e)
    s_db.commit()
    eid = e.id
    s_db.close()

    # 正常录入：座机带分机规范化
    r = client.post(f"/api/enterprises/{eid}/contacts",
                    json={"name": "CL王工", "phone": "0512-66668888-801",
                          "title": "设备科", "role": "buyer_contact"},
                    headers=admin_headers)
    assert r.status_code == 201
    assert r.json()["phones"] == ["0512-66668888-801"]

    # 同号拒重
    r = client.post(f"/api/enterprises/{eid}/contacts",
                    json={"name": "CL李工", "phone": "0512-66668888"},
                    headers=admin_headers)
    assert r.status_code == 422

    # 无电话也可录（纯姓名联系人）
    r = client.post(f"/api/enterprises/{eid}/contacts",
                    json={"name": "CL赵总"}, headers=admin_headers)
    assert r.status_code == 201
    assert r.json()["phones"] == []

    # 坏号码格式 422
    r = client.post(f"/api/enterprises/{eid}/contacts",
                    json={"name": "CL钱工", "phone": "hello"}, headers=admin_headers)
    assert r.status_code == 422

    # 空姓名 422
    r = client.post(f"/api/enterprises/{eid}/contacts",
                    json={"name": "  "}, headers=admin_headers)
    assert r.status_code == 422

    # 落库字段
    with SessionLocal() as s:
        c = s.query(Contact).filter(Contact.enterprise_id == eid,
                                    Contact.name == "CL王工").one()
        assert c.source == "manual"
        assert load_phones(c.phones_json) == ["0512-66668888-801"]
        assert "手工录入" in (c.evidence_claim or "")

    # 404
    r = client.post("/api/enterprises/999999/contacts",
                    json={"name": "X"}, headers=admin_headers)
    assert r.status_code == 404
