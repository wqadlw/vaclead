"""SPEC-CC 回归：晨检行 radar-strip 口径（通知中心零新端点不测后端）。"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cc-"), "t.db")
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
    Signal,
    User,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Signal, User, AuditLog]


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


def test_cc_radar_strip(client, admin_headers):
    """三数口径：竞对中标 30 天/拟建窗口/未触达联系人。"""
    today = date.today()
    with SessionLocal() as s:
        comp = Enterprise(name="CC竞对公司", source="poi", is_competitor=1)
        prop_e = Enterprise(name="CC拟建单位", source="proposal_hb")
        normal = Enterprise(name="CC普通企业", source="poi")
        s.add_all([comp, prop_e, normal])
        s.flush()
        window = (today - timedelta(days=60)).isoformat()
        s.add_all([
            Signal(enterprise_id=comp.id, type="tender_win", title="CC竞对中标成交价50万元", url=""),
            Signal(enterprise_id=prop_e.id, type="proposal", title="CC窗口内备案",
                   url="", pub_date=window),
            Signal(enterprise_id=prop_e.id, type="proposal", title="CC窗口外备案",
                   url="", pub_date="2026-01-01"),
        ])
        s.add(Contact(enterprise_id=normal.id, name="CC未拨联系人", role="buyer_contact",
                      phones_json='["13800000041"]'))
        s.commit()

    r = client.get("/api/dashboard/radar-strip", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["competitor_wins_30d"] >= 1
    assert d["proposals_window_now"] >= 1  # 60 天前备案在窗口内；1/1 出窗
    assert d["contact_untouched"] >= 1
