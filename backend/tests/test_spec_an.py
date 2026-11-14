"""SPEC-AN 回归：触达结果标记/推荐接入/漏斗/批量清洗。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-an-"), "t.db")
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
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact]


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


def _seed_with_phone(session, name: str, phone: str) -> tuple[int, int]:
    e = Enterprise(name=name, source="poi", city="苏州", industry_key="coating",
                   industry_name="coating")
    session.add(e)
    session.flush()
    c = Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                title=phone, phones_json=f'["{phone}"]', source="amap")
    session.add(c)
    session.commit()
    return e.id, c.id


def test_call_result_endpoint_and_validation(client, admin_headers):
    with SessionLocal() as s:
        eid, cid = _seed_with_phone(s, "触达标记测试镀膜有限公司", "0512-10010010")
    r = client.post(f"/api/contacts/{cid}/call-result",
                    json={"result": "interested"}, headers=admin_headers)
    assert r.status_code == 200
    d = client.get(f"/api/enterprises/{eid}/contacts", headers=admin_headers).json()
    assert d["items"][0]["call_result"] == "interested" and d["items"][0]["called_at"]
    # 非法枚举拒绝
    r2 = client.post(f"/api/contacts/{cid}/call-result",
                     json={"result": "瞎写的"}, headers=admin_headers)
    assert r2.status_code == 422


def test_recommend_excludes_all_invalid_and_boosts_interested():
    from app.recommend import score_candidates

    with SessionLocal() as s:
        bad, cbad = _seed_with_phone(s, "全空号排除测试企业", "0512-20020020")
        good, cgood = _seed_with_phone(s, "有意向加权测试企业", "0512-30030030")
        plain, cplain = _seed_with_phone(s, "普通未拨测试企业", "0512-40040040")
        s.commit()

        recs = score_candidates(s, limit=100)
        by = {r["ent_id"]: r for r in recs}
        assert bad in by and good in by and plain in by, "未标记时三者都应是候选"
        assert by[good]["score"] == by[plain]["score"], "标记前不应有触达加权"

        # 标记后行为生效：全 invalid 排除 / interested 加权
        for cid, r in ((cbad, "invalid"), (cgood, "interested")):
            s.get(Contact, cid).call_result = r
            s.get(Contact, cid).called_at = utcnow()
        s.commit()
        recs2 = score_candidates(s, limit=100)
        ids2 = {r["ent_id"] for r in recs2}
        assert bad not in ids2, "全部 invalid 的企业应被排除"
        assert good in ids2
        by2 = {r["ent_id"]: r for r in recs2}
        assert by2[good]["score"] > by[good]["score"], "interested 应加权"
        assert any("已表示兴趣" in x for x in by2[good]["reasons"])


def test_call_funnel_endpoint(client, admin_headers):
    with SessionLocal() as s:
        _, c1 = _seed_with_phone(s, "漏斗企业甲", "0512-50050050")
        _, c2 = _seed_with_phone(s, "漏斗企业乙", "0512-60060060")
        s.get(Contact, c1).call_result = "connected"
        s.get(Contact, c1).called_at = utcnow()
        s.get(Contact, c2).call_result = "no_answer"
        s.get(Contact, c2).called_at = utcnow()
        s.commit()
    d = client.get("/api/analytics/call-funnel", headers=admin_headers).json()
    assert d["dials"] >= 2 and d["connected"] >= 1 and d["no_answer"] >= 1
    assert 0 < d["connect_rate"] <= 1


def test_bulk_cleanup_dry_run_and_execute(client, admin_headers):
    _seed_with_phone = None  # noqa: F841 — 占位保持可读
    with SessionLocal() as s:
        for n in ("锦苑住宅区物业", "幸福家园便利店", "正常目标机械有限公司"):
            s.add(Enterprise(name=n, source="poi", city="苏州"))
        s.commit()
    # dry_run 预览
    d = client.post("/api/enterprises/bulk-cleanup",
                    json={"patterns": ["苑", "家园", "便利店"], "dry_run": 1},
                    headers=admin_headers).json()
    assert d["dry_run"] is True and d["matched"] == 2
    assert any("锦苑" in x for x in d["samples"])
    # 执行
    d2 = client.post("/api/enterprises/bulk-cleanup",
                     json={"patterns": ["苑", "家园", "便利店"]},
                     headers=admin_headers).json()
    assert d2["deleted"] == 2
    # 列表隐藏（全局过滤器）
    d3 = client.get("/api/enterprises", params={"q": "锦苑"}, headers=admin_headers).json()
    assert not any("锦苑" in it["name"] for it in d3["items"])


def test_bulk_cleanup_exclude_protects_false_positives(client, admin_headers):
    """2026-11-14 数据质量轮："体检"会子串误伤"半导体检测"（真买家）——
    exclude 参数必须把含排除词的名称护住。"""
    with SessionLocal() as s:
        for n in ("合肥盛源半导体检测有限公司", "无锡爱康体检中心", "无锡城东卫生院"):
            s.add(Enterprise(name=n, source="poi", city="苏州"))
        s.commit()
    d = client.post("/api/enterprises/bulk-cleanup",
                    json={"patterns": ["体检", "卫生院"], "exclude": ["体检测"],
                          "dry_run": 1},
                    headers=admin_headers).json()
    assert d["dry_run"] is True and d["matched"] == 2
    assert all("半导体" not in x for x in d["samples"]), "半导体检测必须被 exclude 护住"
    d2 = client.post("/api/enterprises/bulk-cleanup",
                     json={"patterns": ["体检", "卫生院"], "exclude": ["体检测"]},
                     headers=admin_headers).json()
    assert d2["deleted"] == 2
    with SessionLocal() as s:
        alive = s.query(Enterprise).filter(
            Enterprise.name == "合肥盛源半导体检测有限公司",
            Enterprise.deleted_at.is_(None),
        ).count()
        assert alive == 1, "半导体检测公司必须仍然在库"
