"""SPEC-SEG2 回归：客户画像数据化——segment 表/种子十群/CRUD/统计口径/名单筛选/推荐权重。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-seg2-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.api.segments import seed_customer_segments  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    CustomerSegment,
    Enterprise,
    Lead,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, CustomerSegment, Lead]


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
    with TestClient(app) as c:  # lifespan 启动即种（SPEC-SEG2 §3）
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


def _seg_by_code(client, admin_headers, code: str) -> dict:
    items = client.get("/api/segments", headers=admin_headers).json()["items"]
    return next(i for i in items if i["code"] == code)


def _seed_ent(s, name: str, *, industry: str | None, source: str = "poi",
              intent: int = 0) -> Enterprise:
    e = Enterprise(name=name, source=source, industry_key=industry,
                   industry_name=industry, intent_score=intent, city="苏州")
    s.add(e)
    s.flush()
    return e


def _seed_phone(s, eid: int, phone: str) -> None:
    s.add(Contact(enterprise_id=eid, name="地图公开电话", role="poi",
                  title=phone, phones_json=f'["{phone}"]', source="amap"))
    s.commit()


# ---- §6 种子幂等 + 十群内容 ----


def test_segment_seed_idempotent(client):
    """种子幂等：重复跑零写入、数量不变（SPEC-SEG2 §6）。"""
    with SessionLocal() as s:
        before = s.query(CustomerSegment).count()
        assert before == 10, f"应已随 lifespan 种入十群，实际 {before}"
        n1 = seed_customer_segments(s)
        n2 = seed_customer_segments(s)
        assert n1 == 0 and n2 == 0, "标记命中即整段跳过，重跑零写入"
        assert s.query(CustomerSegment).count() == before


def test_segment_seed_ten_groups(client):
    """十群内容断言：code/priority/industry_keys/business_lines 与 SPEC §2 表格一致。"""
    with SessionLocal() as s:
        rows = {seg.code: seg for seg in s.query(CustomerSegment).all()}
        assert set(rows) == {
            "coating_factory", "semiconductor_factory", "heat_treatment_factory",
            "food_freeze_dry", "pharma_factory", "pv_battery_factory",
            "chemical_factory", "plastics_factory", "packaging_factory",
            "central_retrofit",
        }
        assert rows["coating_factory"].priority == "P0"
        assert rows["coating_factory"].industry_keys == '["coating"]'
        assert rows["coating_factory"].business_lines == '["整机","维修","配件","泵油"]'
        assert rows["semiconductor_factory"].industry_keys == '["semiconductor"]'
        assert rows["heat_treatment_factory"].industry_keys == '["heat_treatment"]'
        assert rows["food_freeze_dry"].industry_keys == '["freeze_dried"]'
        # 制药厂：enabled=0 + notes 注明词表待用户确认（SPEC §2）
        assert rows["pharma_factory"].enabled == 0
        assert "词表待用户确认" in rows["pharma_factory"].notes
        assert rows["pharma_factory"].priority == "P0"
        # 光伏/锂电与中央系统改造：行业类目暂空（notes 说明口径）
        assert rows["pv_battery_factory"].industry_keys == "[]"
        assert rows["pv_battery_factory"].enabled == 1
        assert rows["central_retrofit"].industry_keys == "[]"
        assert rows["central_retrofit"].priority == "P1"
        assert rows["chemical_factory"].industry_keys == '["chemical_new_material"]'
        assert rows["plastics_factory"].industry_keys == '["plastic_rubber"]'
        assert rows["packaging_factory"].industry_keys == '["packaging_printing"]'
        # 优先级分布：6×P0（含停用制药厂）+ 4×P1
        prios = sorted(seg.priority for seg in rows.values())
        assert prios == ["P0"] * 6 + ["P1"] * 4


# ---- §6 CRUD 权限往返 ----


def test_segment_crud_roundtrip(client, admin_headers, sales_headers):
    """admin 建/改/停用/删；sales 只读；未登录 401；code 唯一。"""
    # 未登录
    assert client.get("/api/segments").status_code == 401
    assert client.post("/api/segments", json={"name": "x", "code": "x"}).status_code == 401
    # sales 只读
    assert client.get("/api/segments", headers=sales_headers).status_code == 200
    assert client.post(
        "/api/segments", json={"name": "x", "code": "x"}, headers=sales_headers
    ).status_code == 403
    # admin 建群
    r = client.post("/api/segments", headers=admin_headers, json={
        "name": "SEG2测试群", "code": "seg2_crud", "keywords": ["测试词"],
        "industry_keys": ["coating"], "business_lines": ["整机"],
        "priority": "P1", "notes": "测试用", "sort": 500,
    })
    assert r.status_code == 201
    sid = r.json()["id"]
    item = _seg_by_code(client, admin_headers, "seg2_crud")
    assert item["name"] == "SEG2测试群"
    assert item["keywords"] == ["测试词"], "JSON 列出参应为数组而非原始串"
    assert item["industry_keys"] == ["coating"]
    assert item["enabled"] is True and item["priority"] == "P1"
    # code 唯一
    dup = client.post("/api/segments", headers=admin_headers, json={
        "name": "另存同码", "code": "seg2_crud",
    })
    assert dup.status_code == 422
    # 非法 priority
    bad = client.post("/api/segments", headers=admin_headers, json={
        "name": "x", "code": "seg2_bad", "priority": "P9",
    })
    assert bad.status_code == 422
    # admin 改群 + 停用
    r2 = client.put(f"/api/segments/{sid}", headers=admin_headers, json={
        "priority": "P0", "industry_keys": ["semiconductor"], "enabled": 0,
    })
    assert r2.status_code == 200
    item2 = _seg_by_code(client, admin_headers, "seg2_crud")
    assert item2["priority"] == "P0" and item2["enabled"] is False
    assert item2["industry_keys"] == ["semiconductor"]
    # sales 仍只读可见（停用群定义保留可查）
    assert client.get("/api/segments", headers=sales_headers).status_code == 200
    # 删除
    assert client.delete(f"/api/segments/{sid}", headers=admin_headers).json() == {"ok": True}
    assert client.delete(f"/api/segments/{sid}", headers=admin_headers).status_code == 404
    assert client.put(f"/api/segments/{sid}", headers=admin_headers,
                      json={"priority": "P2"}).status_code == 404
    with SessionLocal() as s:
        assert s.query(CustomerSegment).filter(
            CustomerSegment.code == "seg2_crud").count() == 0


# ---- §6 统计口径（非客户沙粒不计入） ----


def test_segment_stats_exclude_non_buyer(client, admin_headers):
    """造 *_buyer 与公共机构名称形态沙粒——均不计入该群企业数/可拨数。"""
    base = _seg_by_code(client, admin_headers, "coating_factory")
    with SessionLocal() as s:
        real = _seed_ent(s, "SEG2统计镀膜有限公司", industry="coating")
        _seed_phone(s, real.id, "0512-66000001")
        # 沙粒一：政采买家建档源（*_buyer）——SPEC-SEG2 §6 要求不计入
        buyer = _seed_ent(s, "SEG2政采买家镀膜有限公司", industry="coating",
                          source="ggzy_buyer")
        _seed_phone(s, buyer.id, "0512-66000002")
        # 沙粒二：行业未分类但名称是公共机构形态（不带"公司"+命中兜底词）
        inst = _seed_ent(s, "SEG2镀膜检验中心", industry="coating")
        _seed_phone(s, inst.id, "0512-66000003")
    after = _seg_by_code(client, admin_headers, "coating_factory")
    assert after["enterprise_count"] == base["enterprise_count"] + 1, "仅真客户计入"
    assert after["dialable_count"] == base["dialable_count"] + 1, "可拨仅计真客户联系人"


# ---- §6 segment 筛选 ----


def test_enterprises_segment_filter(client, admin_headers):
    """enterprises?segment=coating_factory 只回该群企业；非客户排除叠加。"""
    with SessionLocal() as s:
        a = _seed_ent(s, "SEG2筛选镀膜甲有限公司", industry="coating")
        a.icp_score = 1000  # 压过全库其他 coating 行，确保落在第一页
        s.add(a)
        s.flush()
        _seed_phone(s, a.id, "0512-66000011")
        b = _seed_ent(s, "SEG2筛选半导体乙有限公司", industry="semiconductor")
        b.icp_score = 999
        s.add(b)
        s.flush()
        _seed_phone(s, b.id, "0512-66000012")
        buyer = _seed_ent(s, "SEG2筛选买家镀膜有限公司", industry="coating",
                          source="ggzy_buyer")
        buyer.icp_score = 998
        s.add(buyer)
    r = client.get("/api/enterprises?segment=coating_factory", headers=admin_headers)
    assert r.status_code == 200
    names = [i["name"] for i in r.json()["items"]]
    assert "SEG2筛选镀膜甲有限公司" in names, "该群企业应在名单中"
    assert "SEG2筛选半导体乙有限公司" not in names, "他群企业不得混入"
    assert "SEG2筛选买家镀膜有限公司" not in names, "非客户排除须叠加（与画像统计同口径）"
    # 未知 code / 无行业映射的停用群 → 空结果
    r2 = client.get("/api/enterprises?segment=no_such_code", headers=admin_headers)
    assert r2.status_code == 200 and r2.json()["items"] == [] and r2.json()["total"] == 0
    r3 = client.get("/api/enterprises?segment=pharma_factory", headers=admin_headers)
    assert r3.status_code == 200 and r3.json()["items"] == [], "industry_keys 为空的群无名单"


# ---- §6 recommend segment 权重（对照组） ----


def test_recommend_segment_weight_control(client, admin_headers):
    """同基线对照：P0 群企业比 P2 群企业恰高 5 分；停用群不加权。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        s.add(CustomerSegment(
            name="SEG2测试P0群", code="seg2_test_p0", keywords="[]",
            industry_keys='["seg2_p0_ind"]', business_lines="[]",
            priority="P0", enabled=1, notes="", sort=200))
        s.add(CustomerSegment(
            name="SEG2测试P2群", code="seg2_test_p2", keywords="[]",
            industry_keys='["seg2_p2_ind"]', business_lines="[]",
            priority="P2", enabled=1, notes="", sort=210))
        # 对照控制：停用的 P0 群盖同一行业键——enabled=0 必须不参与加权
        s.add(CustomerSegment(
            name="SEG2停用P0群", code="seg2_test_dis", keywords="[]",
            industry_keys='["seg2_p2_ind"]', business_lines="[]",
            priority="P0", enabled=0, notes="", sort=220))
        pa = _seed_ent(s, "SEG2P0群企业有限公司", industry="seg2_p0_ind")
        _seed_phone(s, pa.id, "0512-66110001")
        pz = _seed_ent(s, "SEG2P2群企业有限公司", industry="seg2_p2_ind")
        _seed_phone(s, pz.id, "0512-66110002")
        s.commit()

        recs = score_candidates(s, limit=300)
        by_id = {r["ent_id"]: r for r in recs}
        assert pa.id in by_id and pz.id in by_id, "同基线（电话+未在管）双候选均应入榜"
        assert by_id[pa.id]["score"] - by_id[pz.id]["score"] == 5.0, "P0 比 P2 恰高 5 分"
        assert "客户群：SEG2测试P0群（P0）" in by_id[pa.id]["reasons"]
        assert "客户群：SEG2测试P2群（P2）" in by_id[pz.id]["reasons"]
        assert "SEG2停用P0群" not in " ".join(by_id[pz.id]["reasons"]), "停用群退出推荐"


# ---- 四出4：补数清单按群优先级排序 ----


def test_gongshang_export_sorted_by_segment_priority(client, admin_headers):
    """P0 群企业排在高意图的无群企业之前（配额先花在 P0 群上）。"""
    with SessionLocal() as s:
        s.add(CustomerSegment(
            name="SEG2补数P0群", code="seg2_export_p0", keywords="[]",
            industry_keys='["seg2_export_ind"]', business_lines="[]",
            priority="P0", enabled=1, notes="", sort=230))
        low = _seed_ent(s, "SEG2补数P0公司", industry="seg2_export_ind", intent=1)
        high = _seed_ent(s, "SEG2补数无群公司", industry=None, intent=999)
        s.commit()
        assert low.id and high.id
    text = client.get(
        "/api/tasks/gongshang-lookup-export", headers=admin_headers
    ).text
    i_p0 = text.index("SEG2补数P0公司")
    i_none = text.index("SEG2补数无群公司")
    assert i_p0 < i_none, "P0 群须排在无群企业之前（旧口径纯意图分会排反）"
