"""SPEC-BX 回归：省份归一映射与幂等回填。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bx-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.geoutil import province_from_locality  # noqa: E402
from app.jobs import backfill_province_geo  # noqa: E402
from app.models import AuditLog, Enterprise  # noqa: E402

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, AuditLog]


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


def test_bx_normalization():
    """归一规则：带缀/裸名/省级直判/直辖市区/脏值不发明。"""
    assert province_from_locality("苏州市") == "江苏省"
    assert province_from_locality("苏州") == "江苏省"
    assert province_from_locality("广东省") == "广东省"
    assert province_from_locality("徐汇区") == "上海市"
    assert province_from_locality("邳州市") == "江苏省"  # 县级市代管关系
    # 脏值/未知：None 不发明
    assert province_from_locality("市辖区") is None
    assert province_from_locality("319900") is None
    assert province_from_locality("掇刀区") is None  # 冷门区不猜
    assert province_from_locality("") is None
    assert province_from_locality(None) is None


def test_bx_backfill_idempotent(client, admin_headers):
    """只填空省；已有省不覆盖；幂等重跑 0；端点可达。"""
    with SessionLocal() as s:
        s.add_all([
            Enterprise(name="BX苏州机械有限公司", source="poi", city="苏州"),
            Enterprise(name="BX已省分子泵有限公司", source="poi", city="苏州",
                       province="浙江省"),  # 已有省：不覆盖（如实保留疑点）
            Enterprise(name="BX脏值贸易商行", source="ggzy", city="市辖区"),
        ])
        s.commit()

    r1 = client.post("/api/tasks/backfill-province", headers=admin_headers)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["city_map"] >= 1
    with SessionLocal() as s:
        e1 = s.query(Enterprise).filter(Enterprise.name == "BX苏州机械有限公司").one()
        assert e1.province == "江苏省"
        e2 = s.query(Enterprise).filter(Enterprise.name == "BX已省分子泵有限公司").one()
        assert e2.province == "浙江省"  # 不覆盖

    # 幂等重跑：无新增
    r2 = client.post("/api/tasks/backfill-province", headers=admin_headers)
    assert r2.json()["city_map"] == 0

    # 非 admin 401
    assert client.post("/api/tasks/backfill-province").status_code == 401
