"""SPEC-BW 回归：数据质量与覆盖中心——漏斗/来源分层/缺口/权限。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bw-"), "t.db")
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
    ReviewQueue,
    Signal,
    User,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Signal, ReviewQueue, User, AuditLog]


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


def test_bw_overview_funnel_and_gaps(client, admin_headers):
    """漏斗计数 + 来源分层百分比 + 缺口字段。"""
    with SessionLocal() as s:
        # 完全体：poi + 坐标 + 行业 + 联系人带号
        full = Enterprise(name="BW完全体机械有限公司", source="poi",
                          lat=31.3, lng=120.6, province="江苏省", industry_key="machining")
        # 半空体：csv 来源无任何补全
        bare = Enterprise(name="BW空壳贸易商行", source="csv")
        gone = Enterprise(name="BW已删企业", source="poi", deleted_at=utcnow())
        s.add_all([full, bare, gone])
        s.flush()
        s.add(Contact(enterprise_id=full.id, name="BW王工", role="buyer_contact",
                      phones_json='["13800000031"]'))
        s.add(Signal(enterprise_id=full.id, type="tender", title="BW完全体信号", url=""))
        s.add(ReviewQueue(candidate_id=bare.id, target_id=full.id, score=85.0,
                          reason="fuzzy_name", status="pending"))
        s.commit()
        full_id = full.id

    r = client.get("/api/quality/overview", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()

    # 漏斗：本模块 3 家（1 已删）→ 活跃 2；完全体贡献 geo/行业/联系人/可拨
    assert d["funnel"]["total"] >= 2
    assert d["funnel"]["dialable"] >= 1
    assert d["funnel"]["has_industry"] >= 1
    assert d["funnel"]["has_geo"] >= 1

    # 来源分层：csv 行存在且百分比 0；poi 行可拨 ≥1
    csv_row = d["by_source"].get("csv")
    assert csv_row and csv_row["count"] >= 1 and csv_row["dialable_pct"] == 0.0
    poi_row = d["by_source"].get("poi")
    assert poi_row and poi_row["dialable"] >= 1

    # 缺口：省份缺失 ≥1（bare 无省）；查重 open ≥1；已删不入任何计数
    assert d["gaps"]["province_missing"] >= 1
    assert d["gaps"]["dup_open"] >= 1
    assert d["gaps"]["signal_coverage"] >= 1

    # 清理（ReviewQueue 在隔离清单里按 id> 删）


def test_bw_admin_only(client, admin_headers):
    """数据质量是运营口径——admin_only（无 token 401）。"""
    r = client.get("/api/quality/overview")
    assert r.status_code == 401
