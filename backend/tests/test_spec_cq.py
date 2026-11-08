"""SPEC-CQ 回归：地图 geo 增强——phone/recent_30d/学校排除/recent 过滤。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cq-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Contact, Enterprise, Signal  # noqa: E402

init_db(seed=False)


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in (Enterprise, Contact, Signal):
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in (Enterprise, Contact, Signal):
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
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with SessionLocal() as s:
        dial = Enterprise(name="CQ可拨镀膜有限公司", source="poi", lat=31.30, lng=120.58,
                          city="苏州市", industry_key="coating", intent_score=85)
        quiet = Enterprise(name="CQ沉默机械有限公司", source="poi", lat=31.31, lng=120.59,
                           city="苏州市", industry_key="machining", intent_score=40)
        school = Enterprise(name="CQ师范学院", source="poi", lat=31.32, lng=120.60,
                            city="苏州市", industry_key="research_edu")
        s.add_all([dial, quiet, school])
        s.flush()
        s.add(Contact(enterprise_id=dial.id, name="CQ王工", role="buyer_contact",
                      phones_json='["13800000101"]'))
        s.add_all([
            Signal(enterprise_id=dial.id, type="tender", title="CQ近期采购公告",
                   url="cq1", created_at=now),
            Signal(enterprise_id=quiet.id, type="tender", title="CQ旧公告",
                   url="cq2", created_at=old),
        ])
        s.commit()
        return {"dial": dial.id, "quiet": quiet.id, "school": school.id}


def test_cq_geo_enhancement(client, admin_headers, seeded):
    """phone 字段/recent_30d/学校排除。"""
    r = client.get("/api/enterprises/geo?limit=2000", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "points"  # 3 家不触发聚合
    items = {i["id"]: i for i in d["items"]}

    # 学校排除
    assert seeded["school"] not in items
    # phone 字段（首个可拨号码）
    assert items[seeded["dial"]]["phone"] == "13800000101"
    assert items[seeded["quiet"]].get("phone") is None
    # recent_30d
    assert items[seeded["dial"]]["recent_30d"] is True
    assert items[seeded["quiet"]]["recent_30d"] is False


def test_cq_geo_recent_filter(client, admin_headers, seeded):
    """recent=1 只看近 30 天有动态的企业。"""
    r = client.get("/api/enterprises/geo?recent=1&limit=2000", headers=admin_headers)
    items = {i["id"]: i for i in r.json()["items"]}
    assert seeded["dial"] in items
    assert seeded["quiet"] not in items


def test_cq_geo_research_explicit(client, admin_headers, seeded):
    """显式查学校行业时保留（审计/查看能力不丢）。"""
    r = client.get("/api/enterprises/geo?industry_key=research_edu&limit=2000",
                   headers=admin_headers)
    items = {i["id"]: i for i in r.json()["items"]}
    assert seeded["school"] in items


def test_cq_geo_intent_ordering(client, admin_headers, seeded):
    """SPEC-CR CR1：geo 按意图分降序——截断保最有价值。"""
    with SessionLocal() as s:
        high = Enterprise(name="CQ高意图目标有限公司", source="poi", lat=31.33, lng=120.61,
                          city="苏州市", industry_key="coating", intent_score=95)
        s.add(high)
        s.flush()
        # 可拨优先排序语义：给 high 加电话（95 分可拨压过 85 分可拨）
        s.add(Contact(enterprise_id=high.id, name="CQ高意图联系人", role="buyer_contact",
                      phones_json='["13800000102"]'))
        s.commit()
        high_id = high.id

    r = client.get("/api/enterprises/geo?limit=3", headers=admin_headers)
    items = r.json()["items"]
    assert items[0]["id"] == high_id  # 95 分压过 85/40 的种子企业
    scores = [i["intent_score"] for i in items if i["intent_score"] is not None]
    assert scores == sorted(scores, reverse=True)
