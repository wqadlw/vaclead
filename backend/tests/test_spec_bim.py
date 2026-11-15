"""SPEC-BIM 回归：竞对抢单地图 + 品牌装机预备（P1 兑现轮）。

B1 词表访问器 / B3 抽取器（品牌锚定型号三闸防误收 + upsert 幂等 + 竞对→买家
链）/ B4 任务端点 / B5 四出（install-map / 跨品牌群 count 注入 / 详情页
install_brands / 话术卡 context）。
"""
from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bim-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.brand_extract import extract_all, extract_brand_for  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    BrandInstall,
    Contact,
    Enterprise,
    EnterpriseAlias,
    Lead,
    ReviewQueue,
    Signal,
    TaskCheckpoint,
)
from app.vertical import brand_terms  # noqa: E402

init_db(seed=False)

# 隔离铁律：模块快照清理（照 test_spec_seg2 头部）——子表在前父表在后，
# 另清 brand-extract 任务 checkpoint 与 resolve 链路的别名/复核行
_ISOLATED_MODELS = [
    Signal,
    Contact,
    BrandInstall,
    EnterpriseAlias,
    ReviewQueue,
    Lead,
    Enterprise,
]


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, object] = {}
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
        s.query(TaskCheckpoint).filter(TaskCheckpoint.kind == "brand_extract").delete()
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
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_ent(s, name: str, **kw) -> Enterprise:
    kw.setdefault("source", "csv")
    e = Enterprise(name=name, **kw)
    s.add(e)
    s.flush()
    return e


def _mk_contact(s, eid: int) -> int:
    c = Contact(enterprise_id=eid, name="联系人", role="buyer_contact",
                phones_json='["13800001111"]')
    s.add(c)
    s.flush()
    return c.id


# ---- B1 词表访问器 ----


def test_brand_terms_accessor():
    """词表非空 + 中英对照都命中 + 真库校准词在列（含三井光中全称段）。"""
    terms = brand_terms()
    assert len(terms) >= 19
    for t in ("莱宝", "Leybold", "普旭", "Busch", "北方华创", "中科科仪", "三井光中"):
        assert t in terms, f"品牌词缺失：{t}"
    # 全称段防误收：裸"三井"不收（常州三井街道 POI 13 家），"光中"不单收
    assert "三井" not in terms
    assert "光中" not in terms


# ---- B3 抽取器：型号三闸 + 幂等 upsert ----


def test_extract_brand_model_and_idempotency():
    """品牌锚定型号邻近命中 / 纯品牌无型号 / 项目编号不误抽 / 二跑零新增。"""
    with SessionLocal() as s:
        ent = _mk_ent(s, "BIM精工机械有限公司")
        s.add(Signal(
            enterprise_id=ent.id, type="tender", title="真空设备采购",
            url="https://example.com/bim-m1", pub_date="2024-05-01",
            raw_excerpt="采购北方华创螺杆真空泵 SV200B 型设备",
            evidence_claim="该单位采购北方华创螺杆真空泵 SV200B 型设备",
        ))
        s.commit()
        n1 = extract_brand_for(s, ent.id)
    assert n1 == 1
    with SessionLocal() as s:
        rows = s.query(BrandInstall).filter(BrandInstall.enterprise_id == ent.id).all()
        assert len(rows) == 1
        r = rows[0]
        assert (r.brand, r.model) == ("北方华创", "SV200B")
        assert r.first_seen == "2024-05-01" and r.last_seen == "2024-05-01"

        # 二跑幂等：零新增、时间戳不漂移
        assert extract_brand_for(s, ent.id) == 0

        # 新信号（更晚 pub_date + 更大 id）：first_seen=min / last_seen=max /
        # signal_id、evidence 取最新
        sig2 = Signal(
            enterprise_id=ent.id, type="tender", title="设备维护记录",
            url="https://example.com/bim-m2", pub_date="2026-01-05",
            raw_excerpt="北方华创设备 SV200B 年度维护",
            evidence_claim="北方华创设备 SV200B 年度维护完成",
        )
        s.add(sig2)
        s.commit()
        assert extract_brand_for(s, ent.id) == 0  # 同 (brand, model) 命中 upsert
        s.refresh(r)
        assert r.signal_id == sig2.id
        assert r.first_seen == "2024-05-01" and r.last_seen == "2026-01-05"
        assert "年度维护" in r.evidence
        # 全表仍只有该企业 2 行键内 1 行（幂等不留重复）
        assert (
            s.query(BrandInstall).filter(BrandInstall.enterprise_id == ent.id).count()
            == 1
        )


def test_extract_brand_without_model_and_project_number_guard():
    """纯品牌无型号落 model=""；项目编号形态（连字链）不误抽成型号。"""
    with SessionLocal() as s:
        ent = _mk_ent(s, "BIM围栏精工有限公司")
        s.add(Signal(
            enterprise_id=ent.id, type="tender", title="设备采购公告",
            url="https://example.com/bim-nm", pub_date="2025-03-01",
            raw_excerpt="项目采用北方华创设备，项目编号：HJ-2023-1106",
            evidence_claim="该项目采用北方华创设备（项目编号：HJ-2023-1106）",
        ))
        s.commit()
        n = extract_brand_for(s, ent.id)
    assert n == 1
    with SessionLocal() as s:
        rows = s.query(BrandInstall).filter(BrandInstall.enterprise_id == ent.id).all()
        # 只落 (北方华创, "") 一行——HJ-2023 是连字链编号，拒收；1106 纯数字不配
        assert len(rows) == 1
        assert (rows[0].brand, rows[0].model) == ("北方华创", "")
        assert all(not r.model.startswith("HJ") for r in rows)


def test_extract_same_pair_multi_signals():
    """同一竞对多条中标信号命中同买家 → 买家侧单行 upsert（不撞唯一键）。

    真库实弹 2026-11-15 实锤：autoflush=False 下同批第二条信号的 upsert
    查询看不到 pending 插入行，重复 add 撞 uq_brand_install——flush 修复回归。"""
    with SessionLocal() as s:
        comp = _mk_ent(s, "BIM批内竞对真空设备有限公司", is_competitor=1)
        for i, (url, date) in enumerate((
            ("https://example.com/bim-d1", "2025-01-10"),
            ("https://example.com/bim-d2", "2026-02-20"),
        )):
            s.add(Signal(
                enterprise_id=comp.id, type="tender_win", title=f"BIM批内项目{i}",
                url=url, pub_date=date,
                raw_excerpt="成交价66.0万元，采购人：BIM批内买方有限公司",
                evidence_claim=f"该主体中标「BIM批内项目{i}」，采购人：BIM批内买方有限公司",
            ))
        s.commit()
        assert extract_brand_for(s, comp.id) == 1  # 两条信号合并为买家侧单行
        assert extract_brand_for(s, comp.id) == 0  # 复跑零新增
        buyer = s.query(Enterprise).filter(
            Enterprise.name == "BIM批内买方有限公司"
        ).first()
        rows = s.query(BrandInstall).filter(
            BrandInstall.enterprise_id == buyer.id
        ).all()
        assert len(rows) == 1
        assert rows[0].brand == comp.name and rows[0].model == ""
        assert rows[0].first_seen == "2025-01-10"
        assert rows[0].last_seen == "2026-02-20"  # 跨信号 min/max 生效


def test_extract_competitor_buyer_chain():
    """竞对→买家链：tender_win evidence 采购人 → brand=竞对名落买家（幂等）。"""
    with SessionLocal() as s:
        comp = _mk_ent(
            s, "BIM链路北方华创真空设备有限公司", is_competitor=1
        )
        s.add(Signal(
            enterprise_id=comp.id, type="tender_win", title="BIM链路热处理炉项目",
            url="", pub_date="2025-06-01",
            raw_excerpt="成交价466.0万元，采购人：BIM链路买方测试有限公司",
            evidence_claim=(
                "该主体在公共资源交易平台中标「BIM链路热处理炉项目」，"
                "成交价466.0万元，采购人：BIM链路买方测试有限公司"
            ),
        ))
        s.commit()
        n = extract_brand_for(s, comp.id)
    assert n == 1  # 抢单链行（竞对自身品牌词被自提及守卫拦下，无词表行）
    with SessionLocal() as s:
        comp2 = s.query(Enterprise).filter(
            Enterprise.name == "BIM链路北方华创真空设备有限公司"
        ).first()
        # 自提及守卫：竞对自身不落词表行
        assert (
            s.query(BrandInstall)
            .filter(BrandInstall.enterprise_id == comp2.id)
            .count()
            == 0
        )
        buyer = s.query(Enterprise).filter(
            Enterprise.name == "BIM链路买方测试有限公司"
        ).first()
        assert buyer is not None and buyer.is_competitor != 1
        assert buyer.source == "ggzy_buyer"  # resolve 新建落 buyer 帧（S2 同语义）
        rows = (
            s.query(BrandInstall)
            .filter(BrandInstall.enterprise_id == buyer.id)
            .all()
        )
        assert len(rows) == 1
        r = rows[0]
        assert r.brand == comp2.name and r.model == ""
        assert r.first_seen == "2025-06-01" and r.last_seen == "2025-06-01"
        assert "采购人" in r.evidence
        # 二跑零新增（resolve 归一命中既有买家 + upsert 命中）
        assert extract_brand_for(s, comp2.id) == 0
        assert (
            s.query(BrandInstall)
            .filter(BrandInstall.enterprise_id == buyer.id)
            .count()
            == 1
        )


def test_extract_all_totals_shape():
    """extract_all totals 四键齐全；重跑 installs=0（幂等）。"""
    with SessionLocal() as s:
        totals = extract_all(s)
        assert set(totals) == {"signals", "enterprises", "brands_found", "installs"}
        assert totals["signals"] > 0 and totals["enterprises"] > 0
        brands_first = totals["brands_found"]
        assert brands_first >= 2  # 北方华创 + 莱宝 至少在列（真库校准预期）
        totals2 = extract_all(s)
        assert totals2["installs"] == 0  # 全量重跑零新增
        assert totals2["brands_found"] == brands_first


# ---- B5-1 install-map 端点 ----


def test_install_map_endpoint(client, admin_headers):
    """端点形状 + 非客户排除（研究院买方不进 rows，brand_install 数据保留）。"""
    with SessionLocal() as s:
        comp = _mk_ent(s, "BIM地图竞对设备有限公司", is_competitor=1)
        s.add(Signal(
            enterprise_id=comp.id, type="tender_win", title="BIM镀膜机采购项目",
            url="https://example.com/bim-w1", pub_date="2026-05-10",
            raw_excerpt="成交价88.5万元，采购人：BIM地图买方光学有限公司",
            evidence_claim=(
                "该主体中标「BIM镀膜机采购项目」，成交价88.5万元，"
                "采购人：BIM地图买方光学有限公司"
            ),
        ))
        s.add(Signal(
            enterprise_id=comp.id, type="tender_win", title="BIM烧结炉采购项目",
            url="https://example.com/bim-w2", pub_date="2026-04-01",
            raw_excerpt="采购人：BIM地图材料研究院",
            evidence_claim="该主体中标「BIM烧结炉采购项目」，采购人：BIM地图材料研究院",
        ))
        factory = _mk_ent(s, "BIM地图精工有限公司")
        _mk_contact(s, factory.id)
        s.add(Signal(
            enterprise_id=factory.id, type="tender", title="分子泵使用",
            url="https://example.com/bim-lb", pub_date="2026-03-15",
            raw_excerpt="该厂使用莱宝 SV300 分子泵",
            evidence_claim="该厂使用莱宝 SV300 分子泵进行镀膜",
        ))
        s.commit()
        extract_brand_for(s, comp.id)
        extract_brand_for(s, factory.id)
        factory_id = factory.id

    r = client.get("/api/competitors/install-map", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert set(d) == {"brands", "rows"}
    # brands：brand_install 聚合，buyers 降序，字段齐全
    assert d["brands"], "品牌榜不应为空"
    for b in d["brands"]:
        assert set(b) == {"brand", "buyers", "amount_wan", "latest"}
    buyers = [b["buyers"] for b in d["brands"]]
    assert buyers == sorted(buyers, reverse=True)
    lb = next(b for b in d["brands"] if b["brand"] == "莱宝")
    assert lb["buyers"] >= 1 and lb["latest"] == "2026-03-15"
    # rows：字段齐全 + 金额 + 非客户排除
    row = next(
        x for x in d["rows"] if x["buyer"] == "BIM地图买方光学有限公司"
    )
    assert set(row) == {
        "competitor", "buyer", "brand", "model",
        "amount_wan", "date", "evidence", "signal_id",
    }
    assert row["competitor"] == "BIM地图竞对设备有限公司"
    assert row["brand"] == "BIM地图竞对设备有限公司"  # 抢单链行 brand=竞对名
    assert row["amount_wan"] == 88.5
    assert row["date"] == "2026-05-10"
    assert len(d["rows"]) <= 50
    # 非客户排除：研究院名称形态买方不进地图行……
    assert all(x["buyer"] != "BIM地图材料研究院" for x in d["rows"])
    # ……但装机证据数据层保留（政采买家数据保留惯例）
    with SessionLocal() as s:
        inst = s.query(Enterprise).filter(
            Enterprise.name == "BIM地图材料研究院"
        ).first()
        assert inst is not None
        assert (
            s.query(BrandInstall)
            .filter(BrandInstall.enterprise_id == inst.id)
            .count()
            == 1
        )
    # 详情页 install_brands（B5-3）：工厂档案页在用品牌 + 溯源 url
    d2 = client.get(f"/api/enterprises/{factory_id}", headers=admin_headers).json()
    ib = [x for x in d2["install_brands"] if x["brand"] == "莱宝"]
    assert len(ib) == 1
    assert ib[0]["model"] == "SV300"
    assert ib[0]["date"] == "2026-03-15"
    assert ib[0]["url"] == "https://example.com/bim-lb"
    assert "莱宝" in ib[0]["evidence"]


# ---- B5-2 跨品牌群 count 注入 ----


def test_cross_brand_segment_count(client, admin_headers):
    """cross_brand_service 的 enterprise_count/dialable_count 走 brand_install
    去重企业（join non_buyer_conds）：政采买家源企业不计入，六字段齐全。"""
    r = client.get("/api/segments", headers=admin_headers)
    assert r.status_code == 200
    items = {i["code"]: i for i in r.json()["items"]}
    assert "cross_brand_service" in items
    cb = items["cross_brand_service"]

    # 前置测试已落词表行（BIM精工/BIM围栏/BIM地图精工 均为公司形态，计入）
    assert cb["enterprise_count"] >= 1
    # 六字段全套（附录 A1 口径）照常注入
    for k in (
        "dialable_untouched", "touched", "intent_high",
        "following", "new_7d", "last_signal",
    ):
        assert k in cb

    # 非客户排除：直接给 ggzy_buyer 源企业落 brand_install → 不计入
    with SessionLocal() as s:
        nb_ent = _mk_ent(s, "BIM围栏买方测试有限公司", source="ggzy_buyer")
        s.add(BrandInstall(
            enterprise_id=nb_ent.id, brand="BIM围栏品牌X", model="",
            first_seen="2026-01-01", last_seen="2026-01-01",
        ))
        s.commit()
    r2 = client.get("/api/segments", headers=admin_headers)
    cb2 = {i["code"]: i for i in r2.json()["items"]}["cross_brand_service"]
    assert cb2["enterprise_count"] == cb["enterprise_count"]


# ---- B4 任务端点 ----


def _wait_task(client, headers, task_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/tasks/{task_id}", headers=headers).json()
        if last.get("status") in ("done", "failed", "cancelled"):
            return last
        time.sleep(0.2)
    raise AssertionError(f"任务超时未完成：{last}")


def test_brand_extract_task_endpoint(client, admin_headers, sales_headers):
    """/tasks/brand-extract：admin 可发、sales 403；totals 四键 + failures=[]。

    自带沙粒（共享测试库在既有模块快照清理后可能为空库）：首轮 installs≥1、
    复跑 installs==0——幂等铁律对任务端点同语义成立。"""
    with SessionLocal() as s:
        ent = _mk_ent(s, "BIM任务端点精工有限公司")
        s.add(Signal(
            enterprise_id=ent.id, type="tender", title="BIM任务端点采购",
            url="https://example.com/bim-t1", pub_date="2025-08-01",
            raw_excerpt="采购北方华创真空设备",
            evidence_claim="该单位采购北方华创真空设备",
        ))
        s.commit()

    assert client.post(
        "/api/tasks/brand-extract", json={}, headers=sales_headers
    ).status_code == 403
    r = client.post("/api/tasks/brand-extract", json={}, headers=admin_headers)
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    d = _wait_task(client, admin_headers, task_id)
    assert d["status"] == "done", d
    totals = d["state"]
    # 必需键子集断言：task_runtime.finish 会按 SPEC-E 生命周期追加
    # collector_health（共享套件进程内 resilience 残留统计非空时出现）
    assert {
        "signals", "enterprises", "brands_found", "installs", "failures",
    } <= set(totals)
    assert totals["signals"] >= 1 and totals["enterprises"] >= 1
    assert totals["installs"] >= 1  # 沙粒企业首轮抽取落行
    assert totals["failures"] == []
    rr = client.get(f"/api/tasks/{task_id}/results", headers=admin_headers)
    assert rr.status_code == 200
    assert rr.json()["failures"] == []

    # 复跑：零新增（installs=0），扫描口径与品牌榜稳定
    r2 = client.post("/api/tasks/brand-extract", json={}, headers=admin_headers)
    d2 = _wait_task(client, admin_headers, r2.json()["task_id"])
    assert d2["status"] == "done", d2
    totals2 = d2["state"]
    assert totals2["installs"] == 0
    assert totals2["signals"] == totals["signals"]
    assert totals2["enterprises"] == totals["enterprises"]
    assert totals2["brands_found"] == totals["brands_found"]


# ---- B5-4 话术卡 context ----


def test_script_card_context(client, admin_headers):
    """competitor_replace 命中时 _card_dict 附 context{comp_name, evidence}；
    未命中场景无 context 键（可选键，兼容既有消费方）。"""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    with SessionLocal() as s:
        comp = _mk_ent(s, "BIM话术竞对设备有限公司", is_competitor=1)
        s.add(Signal(
            enterprise_id=comp.id, type="tender_win", title="BIM话术镀膜项目",
            url="https://example.com/bim-s1", pub_date=today,
            raw_excerpt="成交价50万元",
            evidence_claim="该主体中标「BIM话术镀膜项目」，采购人：BIM话术买方有限公司",
        ))
        cust = _mk_ent(s, "BIM话术客户机械有限公司")
        cid = _mk_contact(s, cust.id)
        s.add(Signal(
            enterprise_id=cust.id, type="tender_buy", title="BIM话术镀膜项目",
            url="", pub_date=today,
            raw_excerpt="作为采购人由「BIM话术竞对设备有限公司」中标成交",
            evidence_claim="该单位公开采购「BIM话术镀膜项目」，由BIM话术竞对设备有限公司中标",
        ))
        s.commit()
        plain = _mk_ent(s, "BIM话术普通机械有限公司")
        cid2 = _mk_contact(s, plain.id)
        s.add(Lead(enterprise_id=plain.id, status="new"))
        s.commit()

    d = client.get(f"/api/contacts/{cid}/prep", headers=admin_headers).json()
    cards = d["script_cards"]
    assert cards[0]["scene"] == "competitor_replace"
    ctx = cards[0].get("context")
    assert ctx is not None
    assert ctx["comp_name"] == "BIM话术竞对设备有限公司"
    assert "采购人" in ctx["evidence"]

    d2 = client.get(f"/api/contacts/{cid2}/prep", headers=admin_headers).json()
    assert d2["script_cards"][0]["scene"] == "first_contact"
    assert all("context" not in c for c in d2["script_cards"])
