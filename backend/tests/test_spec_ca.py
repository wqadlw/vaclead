"""SPEC-CA 回归：查重治理——poi_store 规则/批量回写/导出。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ca-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    ReviewQueue,
    Signal,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, ReviewQueue, Signal, AuditLog]


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


def test_ca_auto_dismiss_poi_store(client, admin_headers):
    """门店对自动拒绝；非门店对不动；幂等。"""
    with SessionLocal() as s:
        names = [
            ("CA卡西欧(万象汇店)", None), ("CA卡西欧(银泰城店)", None),
            ("CA安世半导体(中国)有限公司", None), ("CA安世半导体有限公司", None),
        ]
        ents = []
        for n, _ in names:
            e = Enterprise(name=n, source="poi")
            s.add(e)
            ents.append(e)
        s.flush()
        rq_store = ReviewQueue(candidate_id=ents[0].id, target_id=ents[1].id,
                               score=88.0, status="pending")
        rq_semi = ReviewQueue(candidate_id=ents[2].id, target_id=ents[3].id,
                              score=92.0, status="pending")
        s.add_all([rq_store, rq_semi])
        s.commit()
        store_id, semi_id = rq_store.id, rq_semi.id

    r = client.post("/api/tasks/review-auto-dismiss", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["dismissed"] >= 1
    with SessionLocal() as s:
        rq1 = s.get(ReviewQueue, store_id)
        assert rq1.status == "rejected" and rq1.reason == "auto_poi_store"
        rq2 = s.get(ReviewQueue, semi_id)
        assert rq2.status == "pending"  # 非门店对留人工

    # 幂等：再跑不再动
    d2 = client.post("/api/tasks/review-auto-dismiss", headers=admin_headers).json()
    assert d2["dismissed"] == 0


def test_ca_bulk_and_export(client, admin_headers):
    """bulk 批量 reject；export CSV 含表头与行。"""
    with SessionLocal() as s:
        e1 = Enterprise(name="CA批量甲公司", source="csv")
        e2 = Enterprise(name="CA批量甲公司分店", source="csv")
        s.add_all([e1, e2])
        s.flush()
        rq = ReviewQueue(candidate_id=e2.id, target_id=e1.id, score=84.0, status="pending")
        s.add(rq)
        s.commit()
        rid = rq.id

    # bulk reject
    r = client.post("/api/review-queue/bulk",
                    json={"items": [{"id": rid, "action": "reject"}]},
                    headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["ok"] == 1
    with SessionLocal() as s:
        assert s.get(ReviewQueue, rid).status == "rejected"

    # bulk 非法 action 计失败不中断
    r = client.post("/api/review-queue/bulk",
                    json={"items": [{"id": rid, "action": "explode"}]},
                    headers=admin_headers)
    assert r.json()["failed"] == 1

    # export：CSV 含表头
    r = client.get("/api/review-queue/export", headers=admin_headers)
    assert r.status_code == 200
    text = r.text
    assert text.startswith("rid,score,candidate")
