"""复核队列 API 测试（SPEC-B §7）：merge 迁移完整性 / reject / 幂等。"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vaclead-review-")
os.environ.setdefault("VACLEAD_DB", os.path.join(_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


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
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_review_merge_moves_everything(client, admin_headers):
    """构造 target + candidate（candidate 带信号与别名）→ 走 tier3 相似路径或手工插入队列 → merge。

    断言：信号/别名迁到 target、candidate 删除、队列项 merged、再次 merge 返回 422。
    """
    from app.db import SessionLocal
    from app.models import Enterprise, EnterpriseAlias, ReviewQueue, Signal

    with SessionLocal() as s:
        target = Enterprise(name="上海夯业真空设备科技有限公司", source="ggzy")
        cand = Enterprise(name="上海夯业真空设备科技(苏州)有限公司", source="ccgp")
        s.add_all([target, cand])
        s.flush()
        s.add(Signal(enterprise_id=cand.id, type="tender_win", title="某项目成交",
                     url="http://x/1", pub_date="2026-10-01", base_score=70,
                     evidence_claim="该主体中标，成交价100万元"))
        s.add(EnterpriseAlias(enterprise_id=cand.id, alias="夯业苏州", source="auto"))
        s.add(ReviewQueue(candidate_id=cand.id, target_id=target.id, score=85.0,
                          reason="fuzzy_name"))
        s.commit()
        cand_id, target_id = cand.id, target.id

    r = client.get("/api/review-queue", headers=admin_headers)
    assert r.status_code == 200
    items = [i for i in r.json()["items"] if i["candidate"]["id"] == cand_id]
    assert items and items[0]["score"] == 85.0
    rid = items[0]["id"]

    r = client.post(f"/api/review-queue/{rid}/merge", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["moved_signals"] == 1
    assert r.json()["moved_aliases"] == 1

    with SessionLocal() as s:
        assert s.get(Enterprise, cand_id) is None  # candidate 已删
        sigs = s.query(Signal).filter(Signal.enterprise_id == target_id).all()
        assert len(sigs) == 1 and sigs[0].title == "某项目成交"  # 信号迁移+可溯源
        assert s.query(EnterpriseAlias).filter(
            EnterpriseAlias.enterprise_id == target_id, EnterpriseAlias.alias == "夯业苏州"
        ).count() == 1
        rq = s.get(ReviewQueue, rid)
        # candidate 删除后复核记录保留（candidate_id SET NULL）作审计痕迹
        assert rq is not None and rq.status == "merged"
        assert rq.candidate_id is None
        assert rq.target_id == target_id

    # 幂等保护：已 merged 的复核项不可再 merge
    assert client.post(f"/api/review-queue/{rid}/merge", headers=admin_headers).status_code == 422


def test_review_reject_keeps_both(client, admin_headers):
    from app.db import SessionLocal
    from app.models import Enterprise, ReviewQueue

    with SessionLocal() as s:
        a = Enterprise(name="北京中凯威科技发展有限责任公司", source="ggzy")
        b = Enterprise(name="北京中凯威科技发展有限公司", source="ccgp")  # 同主体不同写法也可以被驳回
        s.add_all([a, b])
        s.flush()
        s.add(ReviewQueue(candidate_id=b.id, target_id=a.id, score=82.0, reason="fuzzy_name"))
        s.commit()
        rid = s.query(ReviewQueue).filter(ReviewQueue.candidate_id == b.id).first().id
        a_id, b_id = a.id, b.id

    r = client.post(f"/api/review-queue/{rid}/reject", headers=admin_headers)
    assert r.status_code == 200
    with SessionLocal() as s:
        assert s.get(Enterprise, a_id) is not None
        assert s.get(Enterprise, b_id) is not None  # 两档都保留
        assert s.get(ReviewQueue, rid).status == "rejected"


def test_review_requires_admin(client, sales_headers):
    r = client.get("/api/review-queue", headers=sales_headers)
    assert r.status_code == 403
