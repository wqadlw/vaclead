"""SPEC-AL 回归：智能推荐引擎（打分/理由/排除同行/阈值）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-al-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    Lead,
    SettingsKV,
    Signal,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact, Signal, Lead]


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


def _seed_ent(session, name: str, *, comp: int = 0, intent: int = 0,
              industry: str | None = "coating", city: str = "苏州") -> Enterprise:
    e = Enterprise(name=name, source="poi", is_competitor=comp,
                   intent_score=intent, industry_key=industry,
                   industry_name=industry, city=city)
    session.add(e)
    session.flush()
    return e


def _seed_phone(session, eid: int, phone: str) -> None:
    session.add(Contact(enterprise_id=eid, name="地图公开电话", role="poi",
                        title=phone, phones_json=f'["{phone}"]', source="amap"))
    session.commit()


def test_recommend_scoring_and_reasons():
    """电话+信号+未在管+tier 全命中 → 高分且理由齐全；同行被排除。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        # 满配候选：电话+新信号+高意图+未在管
        a = _seed_ent(s, "苏州推荐满配镀膜有限公司", intent=80)
        _seed_phone(s, a.id, "0512-66668888")
        s.add(Signal(enterprise_id=a.id, type="tender_win",
                     title="推荐测试中标公告", url="", base_score=60, created_at=utcnow()))
        # 同行：有电话但排除
        c = _seed_ent(s, "苏州同行打标设备制造有限公司", comp=1)
        _seed_phone(s, c.id, "0512-33333333")
        # 无电话无信号：不进候选
        _seed_ent(s, "苏州无电话无声企业", intent=0)
        # 已在管（following）：无"未分配"加分
        b = _seed_ent(s, "苏州已在管镀膜有限公司", intent=60)
        _seed_phone(s, b.id, "0512-55554444")
        s.add(Lead(enterprise_id=b.id, status="following"))
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"]: r for r in recs}
        assert a.id in ids, "满配候选应入榜"
        assert c.id not in ids, "同行必须被排除"
        assert ids[a.id]["score"] > ids[b.id]["score"], "满配 > 已在管"
        reasons_a = " ".join(ids[a.id]["reasons"])
        assert "有公开电话" in reasons_a and "尚未分配" in reasons_a
        assert ids[a.id]["phone"] == "0512-66668888"


def test_recommend_shared_phone_demoted():
    """2026-11-14 数据质量轮：号码挂 ≥3 家企业（总机/物业号）→ -15 分，
    纯"电话+未在管"基线候选（45 分）被推出阈值；独立号码候选不受影响。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        shared = "0512-77777777"
        a = _seed_ent(s, "苏州共享号甲镀膜有限公司")
        _seed_phone(s, a.id, shared)
        b = _seed_ent(s, "苏州共享号乙镀膜有限公司")
        _seed_phone(s, b.id, shared)
        c = _seed_ent(s, "苏州共享号丙镀膜有限公司")
        _seed_phone(s, c.id, shared)
        d = _seed_ent(s, "苏州独立号镀膜有限公司")
        _seed_phone(s, d.id, "0512-88889999")
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"] for r in recs}
        assert a.id not in ids and b.id not in ids and c.id not in ids, (
            "共享号码候选应被降权出榜"
        )
        assert d.id in ids, "独立号码候选不受影响"
        rec_d = next(r for r in recs if r["ent_id"] == d.id)
        assert rec_d["phone"] == "0512-88889999"


def test_recommend_endpoint(client, admin_headers):
    d = client.get("/api/recommendations", params={"limit": 5}, headers=admin_headers).json()
    assert isinstance(d["items"], list)
    for it in d["items"]:
        assert it["score"] >= 45 and it["reasons"], "推荐项必须过阈值且带理由"
