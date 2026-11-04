"""SPEC-AM 回归：软删除/回收站/恢复/彻底删 + 全局过滤器 + 豁免。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-am-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    SettingsKV,
    Signal,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact, Signal]


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


def _seed(name: str, phone: str | None = None) -> int:
    with SessionLocal() as s:
        e = Enterprise(name=name, source="poi", city="测试市")
        s.add(e)
        s.flush()
        if phone:
            s.add(Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                          title=phone, phones_json=f'["{phone}"]', source="amap"))
        s.commit()
        return e.id


def test_soft_delete_hides_everywhere(client, admin_headers):
    """软删后：列表/详情/geo 消失；回收站出现；恢复后回归。"""
    eid = _seed("待删除噪音店", "0512-00001111")
    # 基线可见
    d = client.get("/api/enterprises", params={"q": "待删除噪音店"}, headers=admin_headers).json()
    assert any(it["id"] == eid for it in d["items"])
    # 软删
    r = client.delete(f"/api/enterprises/{eid}", headers=admin_headers)
    assert r.status_code == 200
    # 列表隐藏
    d2 = client.get("/api/enterprises", params={"q": "待删除噪音店"}, headers=admin_headers).json()
    assert not any(it["id"] == eid for it in d2["items"]), "软删后列表应隐藏"
    # 详情 404
    assert client.get(f"/api/enterprises/{eid}", headers=admin_headers).status_code == 404
    # geo 隐藏
    d3 = client.get("/api/enterprises/geo", headers=admin_headers).json()
    assert all(it["id"] != eid for it in d3["items"])
    # 回收站出现
    t = client.get("/api/enterprises/trash", headers=admin_headers).json()
    assert any(it["id"] == eid for it in t["items"])
    # 恢复 → 回归
    r2 = client.post(f"/api/enterprises/{eid}/restore", headers=admin_headers)
    assert r2.status_code == 200
    d4 = client.get("/api/enterprises", params={"q": "待删除噪音店"}, headers=admin_headers).json()
    assert any(it["id"] == eid for it in d4["items"])


def test_purge_requires_confirm_and_cascades(client, admin_headers):
    eid = _seed("待彻底删企业", "0512-22223333")
    with SessionLocal() as s:
        e = s.get(Enterprise, eid)
        e.deleted_at = utcnow()
        s.add(Signal(enterprise_id=eid, type="tender", title="s", url="",
                     base_score=10, created_at=utcnow()))
        s.commit()
    # 无 confirm 拒绝
    r = client.delete(f"/api/enterprises/{eid}/purge", headers=admin_headers)
    assert r.status_code == 422
    # confirm=1 级联删除
    r2 = client.delete(f"/api/enterprises/{eid}/purge", params={"confirm": 1},
                       headers=admin_headers)
    assert r2.status_code == 200
    with SessionLocal() as s:
        assert s.get(Enterprise, eid) is None, "彻底删后行应消失"
        assert s.query(Signal).filter(Signal.enterprise_id == eid).count() == 0, "信号应级联删除"
    # 回收站不再出现
    t = client.get("/api/enterprises/trash", headers=admin_headers).json()
    assert not any(it["id"] == eid for it in t["items"])


def test_purge_sweep_cuts_expired():
    """超过保留期的软删企业被清空（30 天语义）。"""
    from datetime import datetime, timedelta, timezone

    from app.jobs import purge_deleted_sweep

    old = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%S")
    fresh = utcnow()
    with SessionLocal() as s:
        e1 = Enterprise(name="过期待清企业", source="poi", deleted_at=old)
        e2 = Enterprise(name="未过期保留企业", source="poi", deleted_at=fresh)
        s.add_all([e1, e2])
        s.commit()
        id1, id2 = e1.id, e2.id
    stats = purge_deleted_sweep(retention_days=30)
    assert stats["purged"] >= 1
    with SessionLocal() as s:
        # 全局软删过滤器连 get 都拦截——回收站语义的读取用豁免选项
        assert s.execute(
            __import__("sqlalchemy").select(Enterprise)
            .where(Enterprise.id == id1)
            .execution_options(include_deleted=True)
        ).scalar_one_or_none() is None, "过期企业应被清空"
        kept = s.execute(
            __import__("sqlalchemy").select(Enterprise)
            .where(Enterprise.id == id2)
            .execution_options(include_deleted=True)
        ).scalar_one()
        assert kept.deleted_at is not None, "未过期企业应保留在回收站"
