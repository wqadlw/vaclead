"""SPEC-CP 回归：联系人编辑与删除——错号修正运维能力。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cp-"), "t.db")
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


@pytest.fixture(scope="module")
def seeded():
    e = Enterprise(name="CP联系人修正目标有限公司", source="csv")
    e2 = Enterprise(name="CP第二家企业", source="csv")
    s_db = SessionLocal()
    s_db.add_all([e, e2])
    s_db.commit()
    c1 = Contact(enterprise_id=e.id, name="CP王工", role="buyer_contact",
                 phones_json='["13800000091"]', title="采购部")
    c2 = Contact(enterprise_id=e2.id, name="CP别家企业联系人", role="legal_rep",
                 phones_json='["13800000092"]')
    s_db.add_all([c1, c2])
    s_db.commit()
    ids = {"eid": e.id, "cid": c1.id, "cid2": c2.id, "eid2": e2.id}
    s_db.close()
    return ids


def test_cp_update_contact(client, admin_headers, seeded):
    """编辑：姓名/职务/角色/电话（规范化重写）。"""
    r = client.patch(f"/api/contacts/{seeded['cid']}",
                     json={"name": "CP王工改", "title": "采购总监",
                           "phone": "0512-66667777-801", "role": "legal_rep"},
                     headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "CP王工改"
    assert d["title"] == "采购总监"
    assert d["role"] == "legal_rep"
    assert d["phones"] == ["0512-66667777-801"]

    with SessionLocal() as s:
        c = s.get(Contact, seeded["cid"])
        assert load_phones(c.phones_json) == ["0512-66667777-801"]

    # 部分更新：仅姓名
    r = client.patch(f"/api/contacts/{seeded['cid']}",
                     json={"name": "CP只改名字"}, headers=admin_headers)
    d = r.json()
    assert d["name"] == "CP只改名字"
    assert d["phones"] == ["0512-66667777-801"]  # 电话不变


def test_cp_update_guards(client, admin_headers, seeded):
    """守卫：同企业他联系人同号 422 / 坏号码 422 / 非法角色 422 / 空名 422 / 跨企业同号放行。"""
    # 同企业他联系人号码 → 422（本企业仅一个联系人，先用手工建第二个）
    r = client.post(f"/api/enterprises/{seeded['eid']}/contacts",
                    json={"name": "CP二号", "phone": "13800000093"},
                    headers=admin_headers)
    assert r.status_code == 201
    c_new = r.json()["id"]

    # 改成与同企业另一联系人相同基础号（含分机差异也应命中查重）
    r = client.patch(f"/api/contacts/{c_new}",
                     json={"phone": "0512-66667777-999"}, headers=admin_headers)
    assert r.status_code == 422

    # 跨企业同号 → 放行（不同企业允许同号）
    r = client.patch(f"/api/contacts/{seeded['cid2']}",
                     json={"phone": "13800000091"}, headers=admin_headers)
    assert r.status_code == 200

    # 坏号码
    r = client.patch(f"/api/contacts/{seeded['cid']}",
                     json={"phone": "hello"}, headers=admin_headers)
    assert r.status_code == 422

    # 非法角色
    r = client.patch(f"/api/contacts/{seeded['cid']}",
                     json={"role": "boss"}, headers=admin_headers)
    assert r.status_code == 422

    # 空姓名
    r = client.patch(f"/api/contacts/{seeded['cid']}",
                     json={"name": "  "}, headers=admin_headers)
    assert r.status_code == 422

    # 404
    r = client.patch("/api/contacts/999999", json={"name": "X"}, headers=admin_headers)
    assert r.status_code == 404


def test_cp_delete_contact(client, admin_headers, seeded):
    """删除：硬删生效；404。"""
    r = client.delete(f"/api/contacts/{seeded['cid']}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    with SessionLocal() as s:
        assert s.get(Contact, seeded["cid"]) is None

    # 再删 → 404
    r = client.delete(f"/api/contacts/{seeded['cid']}", headers=admin_headers)
    assert r.status_code == 404
