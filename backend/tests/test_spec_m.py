"""SPEC-M 回归：标讯中心（M1）+ 筛选扩容 12 维（M2）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-m-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


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
def seeded(client, admin_headers):
    """3 家企业 + 3 类信号：标讯流筛选/facet 的确定性数据。"""
    from app.db import SessionLocal
    from app.models import Enterprise, EnterpriseStat, Signal

    with SessionLocal() as s:
        e1 = Enterprise(name="标讯测试半导体有限公司", province="江苏省",
                        industry_key="semiconductor", source="ggzy")
        e2 = Enterprise(name="标讯测试医院", province="北京市",
                        industry_key="medical_health", source="ggzy_buyer")
        e3 = Enterprise(name="标讯测试机械厂", province="江苏省",
                        industry_key="machining", source="csv")
        s.add_all([e1, e2, e3])
        s.flush()
        s.add(Signal(enterprise_id=e1.id, type="tender_win",
                     title="12英寸真空镀膜机采购中标公告", url="",
                     raw_excerpt="政府采购｜成交价320.5万元",
                     evidence_claim="该主体中标「真空镀膜机」，成交价320.5万元",
                     base_score=70))
        s.add(Signal(enterprise_id=e2.id, type="tender_buy",
                     title="监护仪采购项目公开招标", url="",
                     raw_excerpt="", evidence_claim="该单位公开采购「监护仪」",
                     base_score=70))
        s.add(Signal(enterprise_id=e3.id, type="tender",
                     title="数控车床采购公告", url="", raw_excerpt="",
                     evidence_claim="政采公告：该单位就「数控车床」发布公告",
                     base_score=50))
        s.add(EnterpriseStat(enterprise_id=e3.id, bid_count=3,
                             bid_amount_total_wan=150.0))
        s.commit()
        ids = {"e1": e1.id, "e2": e2.id, "e3": e3.id}
    yield ids


# ---------------- M1 标讯中心 ----------------

def test_signals_stream_filters_and_facets(client, admin_headers, seeded):
    r = client.get("/api/signals", params={"facets": 1}, headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] >= 3
    assert d["facets"]["type"]["tender_win"] >= 1
    assert d["facets"]["province"].get("江苏省", 0) >= 2

    # 行字段完备：类型中文/金额提取/证据句/企业归属
    row = next(i for i in d["items"] if i["type"] == "tender_win")
    assert row["type_cn"] == "中标"
    assert row["amount_wan"] == 320.5  # 证据句正则提取
    assert "真空镀膜机" in row["evidence_claim"]
    assert row["ent_name"] == "标讯测试半导体有限公司"

    # 类型筛选
    d2 = client.get("/api/signals", params={"type": "tender_buy"},
                    headers=admin_headers).json()
    assert all(i["type"] == "tender_buy" for i in d2["items"])
    assert d2["total"] >= 1

    # 行业筛选（join enterprise）
    d3 = client.get("/api/signals", params={"industry_key": "machining"},
                    headers=admin_headers).json()
    assert all(i["ent_industry"] == "machining" for i in d3["items"])

    # 关键词（contains 参数化，% 不作通配符）
    d4 = client.get("/api/signals", params={"q": "监护仪"}, headers=admin_headers).json()
    assert d4["total"] >= 1 and all("监护仪" in i["title"] for i in d4["items"])
    d5 = client.get("/api/signals", params={"q": "%"}, headers=admin_headers).json()
    assert d5["total"] == 0  # 通配符被转义，按字面匹配


# ---------------- M2 筛选扩容 ----------------

def test_established_at_csv_import_and_filter(client, admin_headers):
    row = (
        "name,province,industry_key,成立日期\n"
        "成立日期筛选新锐科技有限公司,江苏省,semiconductor,2024-03-01\n"
        "成立日期筛选老牌机械有限公司,江苏省,machining,2005-06-15\n"
    )
    r = client.post(
        "/api/import/enterprises",
        files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    d = client.get("/api/enterprises", params={"established_within_years": 3},
                   headers=admin_headers).json()
    names = [i["name"] for i in d["items"]]
    assert "成立日期筛选新锐科技有限公司" in names
    assert "成立日期筛选老牌机械有限公司" not in names


def test_bid_power_filter(client, admin_headers, seeded):
    # e3 有 stat：bid_count=3 / 150 万
    d = client.get("/api/enterprises", params={"bid_min": 2},
                   headers=admin_headers).json()
    assert any(i["name"] == "标讯测试机械厂" for i in d["items"])
    d2 = client.get("/api/enterprises", params={"bid_amount_min": 100},
                    headers=admin_headers).json()
    assert any(i["name"] == "标讯测试机械厂" for i in d2["items"])
    d3 = client.get("/api/enterprises", params={"bid_amount_min": 9999},
                    headers=admin_headers).json()
    assert not any(i["name"] == "标讯测试机械厂" for i in d3["items"])


def test_resolve_est_date_backfill():
    """resolve est_date：新建落库 + 已有企业空值补填不覆盖。"""
    from app.db import SessionLocal
    from app.models import Enterprise, EnterpriseAlias
    from app.normalize import resolve_enterprise

    with SessionLocal() as s:
        e1, new1 = resolve_enterprise(s, "成立日期回填测试有限公司", source="ggzy",
                                      uscc="91370000M2ATEST01", est_date="2019-05-01")
        assert new1 and e1.established_at == "2019-05-01"
        # 同 uscc 再解析（命中 tier0），补填不同日期不得覆盖
        e2, new2 = resolve_enterprise(s, "成立日期回填测试有限公司", source="ggzy",
                                      uscc="91370000M2ATEST01", est_date="2020-01-01")
        assert not new2 and e2.established_at == "2019-05-01"
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == e1.id).delete()
        s.delete(e1)
        s.commit()
