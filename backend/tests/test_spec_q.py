"""SPEC-Q 数据质量查重扫描测试：命中入队/幂等重扫/阈值/任务端点。

使用 TestClient + 临时 SQLite（VACLEAD_DB 环境变量必须在导入 app 前设置）。
"""
from __future__ import annotations

import os
import tempfile
import uuid

pytest_tmp = tempfile.mkdtemp(prefix="vaclead-q-")
os.environ.setdefault("VACLEAD_DB", os.path.join(pytest_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.dedupe import scan_duplicates  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Enterprise, ReviewQueue  # noqa: E402

init_db(seed=False)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def db():
    with SessionLocal() as s:
        yield s


def test_scan_queues_planted_pair(db):
    tag = uuid.uuid4().hex[:5]
    ent_a = Enterprise(name=f"恒星真空仪器{tag}有限公司", source="manual")
    ent_b = Enterprise(name=f"恒星真空仪器{tag}股份公司", source="ggzy")  # 近名不同渠道
    db.add_all([ent_a, ent_b])
    db.commit()
    stats = scan_duplicates(db)
    assert stats["queued"] >= 1
    row = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.reason == "dup_scan")
        .order_by(ReviewQueue.id.desc())
        .first()
    )
    assert row is not None and row.score >= 85
    ids = {row.candidate_id, row.target_id}
    assert {ent_a.id, ent_b.id} <= ids | {row.candidate_id, row.target_id}


def test_scan_idempotent_rescan(db):
    tag = uuid.uuid4().hex[:5]
    db.add_all([
        Enterprise(name=f"南华半导体{tag}有限公司", source="manual"),
        Enterprise(name=f"南华半导体{tag}有限责任公司", source="ccgp"),
    ])
    db.commit()
    first = scan_duplicates(db)
    second = scan_duplicates(db)
    assert first["queued"] >= 1 and second["queued"] == 0  # UNIQUE 幂等


def test_scan_threshold_and_distinct(db):
    tag = uuid.uuid4().hex[:5]
    db.add_all([
        Enterprise(name=f"甲{tag}科技有限公司", source="manual"),
        Enterprise(name=f"完全不同{tag}餐饮管理有限公司", source="manual"),
    ])
    db.commit()
    stats = scan_duplicates(db)
    dup = (
        db.query(ReviewQueue)
        .join(Enterprise, ReviewQueue.candidate_id == Enterprise.id)
        .filter(Enterprise.name.like(f"%完全不同{tag}%"))
        .first()
    )
    assert dup is None  # 低分不入队


def test_dup_scan_task_endpoint(client, admin_headers):
    h = admin_headers
    r = client.post("/api/tasks/dup-scan", headers=h)
    assert r.status_code == 200 and r.json().get("task_id")
    assert client.post("/api/tasks/dup-scan").status_code in (401, 403)
