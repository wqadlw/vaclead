"""SPEC-CH 回归：通话准备聚合 + 推荐产品线过滤。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ch-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Contact,
    Enterprise,
    Lead,
    Product,
    Signal,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Signal, Lead, Product, AuditLog]


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
    with SessionLocal() as s:
        e = Enterprise(name="CH冻干食品厂有限公司", source="poi",
                       industry_key="freeze_dried", industry_name="食品饮料/冻干", province="浙江省", city="湖州市",
                       intent_score=60)
        s.add(e)
        s.flush()
        c = Contact(enterprise_id=e.id, name="CH王工", role="buyer_contact",
                    phones_json='["13800000061"]', decision_role="decision_maker")
        s.add(c)
        s.flush()
        s.add(Signal(enterprise_id=e.id, type="proposal", title="CH冻干厂扩建备案",
                     url="https://example.com/ch1", pub_date="2026-11-01",
                     evidence_claim="该单位完成冻干生产线扩建项目备案"))
        s.add(Product(name="CH测试油封泵",
                      application_industries='["freeze_dried", "medical_health"]'))
        s.commit()
        return {"cid": c.id, "eid": e.id}


def test_ch_prep_endpoint(client, admin_headers, seeded):
    """prep 聚合：联系/企业/信号/产品匹配/线索状态。"""
    r = client.get(f"/api/contacts/{seeded['cid']}/prep", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["contact"]["name"] == "CH王工"
    assert d["contact"]["phones"] == ["13800000061"]
    assert d["enterprise"]["industry_name"]
    assert len(d["signals"]) == 1
    assert "冻干" in d["signals"][0]["evidence"]
    assert d["product_match"]  # freeze_dried 在测试产品应用面里
    assert d["lead"] is None  # 未转线索

    # 404
    assert client.get("/api/contacts/999999/prep", headers=admin_headers).status_code == 404


def test_ch_recommendations_product_filter(client, admin_headers, seeded):
    """product 过滤：应用面命中者保留，不匹配行业被滤掉。"""
    with SessionLocal() as s:
        other = Enterprise(name="CH无关模具厂有限公司", source="poi",
                           industry_key="machining", intent_score=80)
        s.add(other)
        s.flush()
        s.add(Contact(enterprise_id=other.id, name="CH李工", role="buyer_contact",
                      phones_json='["13800000062"]'))
        s.commit()

    # 全部：两家都可能上榜（阈值内）
    all_items = client.get("/api/recommendations?limit=100", headers=admin_headers).json()["items"]
    names = {i["name"] for i in all_items}
    assert "CH冻干食品厂有限公司" in names or "CH无关模具厂有限公司" in names

    # 产品过滤：CH测试油封泵应用面=freeze_dried/medical → 模具厂绝不出现
    items = client.get(
        "/api/recommendations?limit=100&product=CH%E6%B5%8B%E8%AF%95%E6%B2%B9%E5%B0%81%E6%B3%B5",
        headers=admin_headers,
    ).json()["items"]
    assert "CH无关模具厂有限公司" not in {i["name"] for i in items}
    if items:
        assert all(
            i["name"] != "CH无关模具厂有限公司" for i in items
        )
