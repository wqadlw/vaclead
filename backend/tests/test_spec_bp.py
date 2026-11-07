"""SPEC-BP 回归：拟建雷达——备案视图聚合 + 省份地基 + 培育流。"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bp-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.jobs import backfill_proposal_geo  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    Signal,
    Todo,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Signal, Todo, AuditLog]


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


@pytest.fixture(scope="module")
def seeded():
    """4 拟建企业：GD 新备/HB 窗口内(60 天前)/HB 旧备(200 天前)/无省待回填。"""
    today = date.today()
    fresh = today.isoformat()
    window = (today - timedelta(days=60)).isoformat()
    old = (today - timedelta(days=200)).isoformat()
    with SessionLocal() as s:
        gd = Enterprise(name="BP广东新备材料有限公司", source="proposal_gd")
        hb_w = Enterprise(name="BP湖北窗口陶瓷有限公司", source="proposal_hb")
        hb_o = Enterprise(name="BP湖北旧备装备有限公司", source="proposal_hb")
        nofix = Enterprise(name="BP无省建材加工厂", source="proposal_hb", province="江苏省")
        s.add_all([gd, hb_w, hb_o, nofix])
        s.flush()
        s.add_all([
            Signal(enterprise_id=gd.id, type="proposal", title="广东项目备案：BP广东新备",
                   url="https://gd.example/1", pub_date=fresh,
                   evidence_claim="该单位于 今日 在广东省平台完成「BP新备年产8万吨项目」备案"),
            Signal(enterprise_id=hb_w.id, type="proposal", title="湖北项目备案：BP窗口陶瓷",
                   url="https://hb.example/1", pub_date=window,
                   evidence_claim="该单位于 60天前 在湖北省平台完成「年产4000万件陶瓷项目」备案"),
            Signal(enterprise_id=hb_o.id, type="proposal", title="湖北项目备案：BP旧备",
                   url="https://hb.example/2", pub_date=old,
                   evidence_claim="该单位于 200天前 在湖北省平台完成「旧备项目」备案"),
        ])
        s.commit()
        return {"gd": gd.id, "hb_w": hb_w.id, "hb_o": hb_o.id, "nofix": nofix.id}


def test_bp0_backfill_geo(client, seeded):
    """省份回填：空省按来源确定性补齐；已有省份不覆盖；幂等。"""
    r1 = backfill_proposal_geo()
    assert r1["updated"] >= 3  # gd/hb_w/hb_o 空省被补（共享库可能还有他模块的）
    with SessionLocal() as s:
        assert s.get(Enterprise, seeded["gd"]).province == "广东省"
        assert s.get(Enterprise, seeded["hb_w"]).province == "湖北省"
        assert s.get(Enterprise, seeded["nofix"]).province == "江苏省"  # 不覆盖
    r2 = backfill_proposal_geo()
    assert r2["updated"] == 0  # 幂等


def test_bp_center_and_window(client, admin_headers, seeded):
    """center 聚合：窗口语义（60 天前=正处招标窗口）+ 省份分布 + q 过滤。"""
    r = client.get("/api/proposals/center?days=0", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    ov = d["overview"]
    assert ov["window_now"] >= 1  # BP湖北窗口 60 天前备案
    assert d["provinces"].get("湖北省", 0) >= 2
    assert d["provinces"].get("广东省", 0) >= 1
    # 项目名从证据句回抽
    item = next(i for i in d["items"] if i["name"] == "BP湖北窗口陶瓷有限公司")
    assert item["project"] == "年产4000万件陶瓷项目"
    assert item["channel"] == "hb"
    # q 过滤（项目名命中）
    r = client.get("/api/proposals/center?q=陶瓷", headers=admin_headers)
    names = [i["name"] for i in r.json()["items"]]
    assert "BP湖北窗口陶瓷有限公司" in names
    assert all("陶瓷" in n or "陶瓷" in (i.get("project") or "") for n, i in zip(names, r.json()["items"]))


def test_bp_nurture_idempotent(client, admin_headers, seeded):
    """培育回访：建 Todo（due=+60 天）→ 重放不重建。"""
    r1 = client.post(f"/api/proposals/{seeded['hb_w']}/nurture", headers=admin_headers)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["created"] is True
    expected_due = (date.today() + timedelta(days=60)).isoformat()
    assert d1["due_date"] == expected_due
    with SessionLocal() as s:
        t = s.get(Todo, d1["todo_id"])
        assert t is not None and t.done == 0 and t.source == "automation"
        assert "BP湖北窗口陶瓷有限公司" in t.title

    r2 = client.post(f"/api/proposals/{seeded['hb_w']}/nurture", headers=admin_headers)
    assert r2.json()["created"] is False
    assert r2.json()["todo_id"] == d1["todo_id"]

    # 404：不存在/已删
    r = client.post("/api/proposals/999999/nurture", headers=admin_headers)
    assert r.status_code == 404
    with SessionLocal() as s:
        e = s.get(Enterprise, seeded["hb_o"])
        e.deleted_at = utcnow()
        s.commit()
    r = client.post(f"/api/proposals/{seeded['hb_o']}/nurture", headers=admin_headers)
    assert r.status_code == 404
