"""SPEC-BQ 回归：联系人中心——跨企业拨号工作台。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bq-"), "t.db")
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
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, AuditLog]


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
    """六个联系人 × 三企业：高/低意图、已删企业、各触达状态。"""
    with SessionLocal() as s:
        hi = Enterprise(name="BQ高意图半导体有限公司", source="poi", intent_score=88)
        lo = Enterprise(name="BQ低意图加工厂", source="poi", intent_score=12)
        gone = Enterprise(name="BQ已删企业", source="poi", deleted_at=utcnow())
        s.add_all([hi, lo, gone])
        s.flush()
        c_untouched_hi = Contact(
            enterprise_id=hi.id, name="BQ王工", role="buyer_contact",
            phones_json='["13800000011"]', decision_role="decision_maker",
        )
        c_interested = Contact(
            enterprise_id=hi.id, name="BQ李经理", role="buyer_contact",
            phones_json='["13800000012"]', call_result="interested", called_at="2026-11-06T10:00:00",
        )
        c_no_answer = Contact(
            enterprise_id=hi.id, name="BQ张助理", role="project_contact",
            phones_json='["13800000013"]', call_result="no_answer", called_at="2026-11-06T11:00:00",
        )
        c_lo = Contact(
            enterprise_id=lo.id, name="BQ赵老板", role="legal_rep",
            phones_json='["13800000014"]',
        )
        c_nophone = Contact(enterprise_id=lo.id, name="BQ无名氏", role="legal_rep")
        c_gone = Contact(enterprise_id=gone.id, name="BQ已删联系人", phones_json='["13800000015"]')
        s.add_all([c_untouched_hi, c_interested, c_no_answer, c_lo, c_nophone, c_gone])
        s.commit()
        return {
            "hi": hi.id, "lo": lo.id,
            "untouched_hi": c_untouched_hi.id, "interested": c_interested.id,
            "no_answer": c_no_answer.id, "lo_c": c_lo.id,
        }


def test_bq_worklist_stats_and_exclusion(client, admin_headers, seeded):
    """stats 口径（untouched=有号未拨）+ 已删企业排除。"""
    r = client.get("/api/contacts?has_phone=0&limit=200", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    st = d["stats"]
    # 已删企业联系人绝不出现
    assert all(i["enterprise"]["name"] != "BQ已删企业" for i in d["items"])
    # 本模块种子贡献：可拨 4（高3+低1）、未触达 2（高意图未拨+低意图未拨）
    assert st["with_phone"] >= 4
    assert st["untouched"] >= 2
    assert st["interested"] >= 1
    assert st["touched"] >= 2


def test_bq_worklist_filters_and_order(client, admin_headers, seeded):
    """untouched 过滤 + 排序（可拨+意图分降序在前）+ 决策链 + q 跨名。"""
    # untouched：只含有号未拨
    r = client.get("/api/contacts?call_status=untouched&limit=200", headers=admin_headers)
    items = r.json()["items"]
    mine = [i for i in items if i["name"].startswith("BQ")]
    assert {i["name"] for i in mine} == {"BQ王工", "BQ赵老板"}

    # 排序：全量列表中高意图企业联系人排低意图之前
    r = client.get("/api/contacts?has_phone=0&limit=200", headers=admin_headers)
    items = r.json()["items"]
    ids = [i["id"] for i in items]
    assert ids.index(seeded["untouched_hi"]) < ids.index(seeded["lo_c"])
    # 同企业内未拨优先于已拨
    assert ids.index(seeded["untouched_hi"]) < ids.index(seeded["interested"])

    # 决策链过滤
    r = client.get("/api/contacts?decision_role=decision_maker&limit=200", headers=admin_headers)
    mine = [i for i in r.json()["items"] if i["name"].startswith("BQ")]
    assert [i["name"] for i in mine] == ["BQ王工"]

    # q 跨联系人名/企业名（has_phone=0 纳入无号联系人）
    r = client.get("/api/contacts?q=BQ低意图&has_phone=0&limit=200", headers=admin_headers)
    mine = [i["name"] for i in r.json()["items"] if i["name"].startswith("BQ")]
    assert set(mine) == {"BQ赵老板", "BQ无名氏"}
