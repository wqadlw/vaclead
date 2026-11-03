"""SPEC-AA 回归：联系人回填升级（AA1）+ 竞对动态筛选（AA2）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-aa-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    from app.db import init_db

    init_db()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------- AA1 回填优先级与跳过（查询语义重放） ----------------

def test_backfill_priority_query_semantics():
    """回填查询语义：招标中企业排最前；已有可拨电话的企业被排除。"""
    from sqlalchemy import func

    from app.db import SessionLocal
    from app.models import Contact, Enterprise, Signal

    with SessionLocal() as s:
        tendering = Enterprise(name="回填优先招标中有限公司", source="ccgp_buyer")
        normal = Enterprise(name="回填普通后置有限公司", source="ccgp_buyer")
        has_phone = Enterprise(name="回填已有电话有限公司", source="ccgp_buyer")
        s.add_all([tendering, normal, has_phone])
        s.flush()
        s.add(Signal(enterprise_id=tendering.id, type="tender",
                     title="回填优先测试招标公告", url="", base_score=50))
        s.add(Contact(enterprise_id=has_phone.id, name="测试联系人", role="采购联系人",
                      title="13800001111", source="ccgp"))
        s.commit()

        # 重放实现同款查询
        has_callable = (
            s.query(Contact.enterprise_id)
            .filter(
                Contact.enterprise_id == Enterprise.id,
                Contact.title.op("GLOB")("*[0-9]*"),
            )
            .subquery()
        )
        tendering_exists = (
            s.query(Signal.enterprise_id)
            .filter(Signal.type == "tender", Signal.enterprise_id == Enterprise.id)
            .exists()
        )
        ordered = (
            s.query(Enterprise)
            .filter(
                Enterprise.source == "ccgp_buyer",
                ~Enterprise.id.in_(has_callable.select()),
            )
            .order_by(tendering_exists.desc(), Enterprise.id.asc())
            .all()
        )
        assert ordered[0].id == tendering.id, "招标中企业应排第一"
        assert has_phone.id not in [e.id for e in ordered], "已有电话企业不应进队列"

        for e in (tendering, normal, has_phone):
            s.query(Signal).filter(Signal.enterprise_id == e.id).delete()
            s.query(Contact).filter(Contact.enterprise_id == e.id).delete()
            s.delete(e)
        s.commit()


# ---------------- AA2 竞对动态筛选 ----------------

def test_bulletin_competitor_filter(client, admin_headers):
    from app.db import SessionLocal
    from app.models import Enterprise, Signal

    with SessionLocal() as s:
        comp = Enterprise(name="竞对筛选回归真空设备制造有限公司", source="ggzy",
                          is_competitor=1)
        buyer = Enterprise(name="竞对筛选回归买家单位", source="ggzy_buyer")
        s.add_all([comp, buyer])
        s.flush()
        s.add(Signal(enterprise_id=comp.id, type="tender_win",
                     title="竞对中标动态测试项目公告", url="", base_score=70))
        s.add(Signal(enterprise_id=buyer.id, type="tender_win",
                     title="买家单位中标动态测试项目公告", url="", base_score=70))
        s.commit()

    d = client.get("/api/signals", params={"competitor": 1, "q": "中标动态测试"},
                   headers=admin_headers).json()
    assert all(i["is_competitor"] == 1 for i in d["items"])
    assert d["total"] >= 1

    d2 = client.get("/api/signals", params={"competitor": 0, "q": "中标动态测试"},
                    headers=admin_headers).json()
    assert all(i["is_competitor"] == 0 for i in d2["items"])

    with SessionLocal() as s:
        for e in (comp, buyer):
            s.query(Signal).filter(Signal.enterprise_id == e.id).delete()
            s.delete(e)
        s.commit()
