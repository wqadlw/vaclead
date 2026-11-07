"""SPEC-CD 回归：找相似 v2——可解释排序/在管排除/理由链。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cd-"), "t.db")
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
    Signal,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Signal, Lead, AuditLog]


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


def test_cd_similar_ranking(client, admin_headers):
    """排序：同行业+同省+可拨 压过仅意图高；在管排除；理由链。"""
    today_signal = "2026-11-07T10:00:00"
    with SessionLocal() as s:
        seed = Enterprise(name="CD种子冻干食品厂", source="csv",
                          industry_key="food_lyo", province="山东省", city="菏泽市",
                          intent_score=50)
        # 候选甲：全维度命中（同行业同省同市可拨近期信号）
        a = Enterprise(name="CD甲冻干厂", source="poi", industry_key="food_lyo",
                       province="山东省", city="菏泽市", intent_score=40)
        # 候选乙：意图最高但跨行业跨省
        b = Enterprise(name="CD乙高分电子厂", source="poi", industry_key="machining",
                       province="广东省", city="深圳市", intent_score=95)
        # 候选丙：维度命中但已在管
        c = Enterprise(name="CD丙冻干厂已在管", source="poi", industry_key="food_lyo",
                       province="山东省", city="菏泽市", intent_score=70)
        comp = Enterprise(name="CD同行真空泵厂", source="ggzy", is_competitor=1,
                          industry_key=None)
        s.add_all([seed, a, b, c, comp])
        s.flush()
        s.add(Contact(enterprise_id=a.id, name="CD甲联系人", role="buyer_contact",
                      phones_json='["13800000051"]'))
        s.add(Signal(enterprise_id=a.id, type="tender", title="CD甲近期信号", url="",
                     created_at=today_signal))
        s.add(Lead(enterprise_id=c.id, status="following", owner_id=None, tenant_id=1))
        s.commit()
        seed_id, a_id, b_id, c_id = seed.id, a.id, b.id, c.id

    r = client.get(f"/api/enterprises/{seed_id}/similar", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [i["id"] for i in items]
    # 甲排第一；乙意图 95 但维度弱只能靠后；丙在管排除；同行排除
    assert ids[0] == a_id
    assert b_id in ids and c_id not in ids
    top = items[0]
    assert set(top["reasons"]) == {"同行业", "同省", "同市", "可拨", "近期有信号"}

    # 404
    assert client.get("/api/enterprises/999999/similar", headers=admin_headers).status_code == 404
