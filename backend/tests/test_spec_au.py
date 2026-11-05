"""SPEC-AU 回归：OEM 季度触达节奏 + 装机批量粘贴导入。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-au-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Contact,
    Enterprise,
    InstalledUnit,
    SettingsKV,
    Todo,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type, AuditLog] = [Enterprise, Contact, Todo, InstalledUnit, AuditLog]


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
        # audit_log 同步清（entity_id 跨模块撞库会污染审计断言，W57）
        s.query(AuditLog).filter(AuditLog.id > snap.get('audit_log', 0)).delete()
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


def test_oem_touch_sweep_creates_and_idempotent():
    """90 天未触达的 OEM → 维护待办；有近期触达跳过；重复扫描幂等。"""
    from datetime import datetime, timedelta, timezone

    from app.jobs import oem_touch_sweep

    with SessionLocal() as s:
        stale_oem = Enterprise(name="苏州久未维护 OEM 制造有限公司",
                               source="poi", customer_role="oem",
                               deleted_at=None)
        touched_oem = Enterprise(name="苏州近期触达 OEM 制造有限公司",
                                 source="poi", customer_role="oem",
                                 deleted_at=None)
        s.add_all([stale_oem, touched_oem])
        s.flush()
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        s.add(Contact(enterprise_id=touched_oem.id, name="地图公开电话",
                      role="poi", call_result="connected",
                      called_at=recent, source="amap"))
        s.commit()
        id1, id2 = stale_oem.id, touched_oem.id

    stats = oem_touch_sweep()
    assert stats["todos_created"] >= 1
    with SessionLocal() as s:
        stale = s.get(Enterprise, id1)
        todo = s.query(Todo).filter(Todo.enterprise_id == id1,
                                    Todo.title.like("OEM 季度关系维护%")).first()
        assert todo is not None, "90 天未触达 OEM 应生成维护待办"
        # 近期触达的不生成
        assert s.query(Todo).filter(Todo.enterprise_id == id2).count() == 0
    # 幂等
    stats2 = oem_touch_sweep()
    with SessionLocal() as s:
        assert s.query(Todo).filter(Todo.enterprise_id == id1,
                                    Todo.title.like("OEM 季度关系维护%")).count() == 1


def test_units_paste_import(client, admin_headers):
    """历史成交 Excel 粘贴导入：表头跳过/归一复用/去重幂等。"""
    text = (
        "企业名称\t型号\t序列号\t安装日期\n"
        "paste 导入测试真空设备有限公司\tEVS-100\tSN001\t2025-06-01\n"
        "paste 导入测试真空设备有限公司\tEVS-100\tSN002\t2025-06-01\n"  # 同企业同型号 → 去重
        "ab\tX\t\t\n"  # 过短跳过
    )
    r = client.post("/api/import/units-paste", json={"text": text},
                    headers=admin_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["created"] == 1 and body["skipped"] >= 2, body
    with SessionLocal() as s:
        # resolve_enterprise 归一会改写名称（空格剥离等）——经装机反查企业
        u = s.query(InstalledUnit).filter(
            InstalledUnit.model == "EVS-100").first()
        assert u is not None
        e = s.get(Enterprise, u.enterprise_id)
        assert e is not None
        units = s.query(InstalledUnit).filter(
            InstalledUnit.enterprise_id == e.id).all()
        assert len(units) == 1 and units[0].model == "EVS-100"
    # 二次导入幂等（exists 查重）
    r2 = client.post("/api/import/units-paste", json={"text": text},
                     headers=admin_headers)
    assert r2.json()["created"] == 0
