"""SPEC-BZ 回归：全局搜索——三实体分组/隔离/已删排除。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bz-"), "t.db")
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
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Signal, AuditLog]


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


def test_bz_grouped_search(client, admin_headers):
    """关键词同时命中三实体；已删企业零泄露；空词三空组。"""
    with SessionLocal() as s:
        e = Enterprise(name="BZ恒星真空科技有限公司", source="poi", intent_score=66)
        gone = Enterprise(name="BZ恒星已删分公司", source="poi", deleted_at=utcnow())
        s.add_all([e, gone])
        s.flush()
        s.add(Contact(enterprise_id=e.id, name="BZ恒星王工", role="buyer_contact"))
        s.add(Signal(enterprise_id=e.id, type="tender", title="BZ恒星真空设备采购公告", url=""))
        s.add(Signal(enterprise_id=gone.id, type="tender", title="BZ恒星已删信号", url=""))
        s.commit()

    r = client.get("/api/search?q=恒星", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert any(i["name"] == "BZ恒星真空科技有限公司" for i in d["enterprises"])
    assert not any("已删" in i["name"] for i in d["enterprises"])
    assert any(c["name"] == "BZ恒星王工" for c in d["contacts"])
    assert any("BZ恒星真空设备采购公告" in s2["title"] for s2 in d["signals"])
    assert not any("已删" in s2["title"] for s2 in d["signals"])

    # 空词：三空组
    r = client.get("/api/search?q=", headers=admin_headers)
    d = r.json()
    assert d == {"enterprises": [], "contacts": [], "signals": []}
