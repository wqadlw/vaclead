"""SPEC-SDGP 回归：山东政采分网直采——MockTransport 四路/job 语义映射/幂等/注册表。

契约（docs/SPEC-SDGP.md）：POST getListByCode 严格 7 参数（homePage 必填 0）；
0301→tender+buyer(sdgp_buyer, 山东省)；0302→tender_win（买方视角 evidence
如实）；无中标方名称不发明；词表=ggzy_name_terms 宽词。
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-sdgp-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import sdgp as sdgp_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Enterprise,
    EnterpriseAlias,
    ReviewQueue,
    SettingsKV,
    Signal,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Signal, EnterpriseAlias, ReviewQueue]


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


@pytest.fixture(autouse=True)
def _reset_sdgp_health():
    """resilience 域级健康度进程内单例——每测前后重置，防 502 用例的
    mark_bad 熔断毒化后续用例（进程内 _HEALTHS 共享）。"""
    from app.collectors import resilience

    resilience._HEALTHS.pop("sdgp", None)
    yield
    resilience._HEALTHS.pop("sdgp", None)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


_RECORD = {
    "date": "2026-11-05 20:22:08",
    "title": "青岛大学6英寸超高真空多靶磁控溅射仪采购公告",
    "areaName": "山东省本级",
    "areaCode": "370000",
    "id": "ENC123",
    "buyKindCode": "1",
    "projectType": "1",
}


def _mock_client(monkeypatch, handler) -> httpx.Client:
    """MockTransport 注入 + 礼貌间隔归零（限速常量在测试里不真睡）。"""
    monkeypatch.setattr(sdgp_mod, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(sdgp_mod, "_last_request_at", 0.0)
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# collector：MockTransport 四路 + normalize
# ---------------------------------------------------------------------------
def test_sdgp_fetch_notices_ok_strict_params(monkeypatch):
    """正常路径：请求体严格 7 参数（homePage=0 红线）+ result.data 解析。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json={
            "code": 200, "success": True,
            "result": {"data": {"records": [dict(_RECORD)], "total": 32, "pages": 2}},
        })

    client = _mock_client(monkeypatch, handler)
    try:
        out = sdgp_mod.fetch_notices("0301", "真空", client=client)
    finally:
        client.close()
    assert out["total"] == 32 and out["pages"] == 2 and len(out["records"]) == 1
    body = bodies[0]
    # 参数严格红线：多参数会 999/None——多一个键都不行
    assert set(body) == {
        "colCode", "area", "currentPage", "pageSize", "homePage", "mergeType", "title",
    }
    assert body["colCode"] == "0301" and body["area"] == "370000"
    assert body["homePage"] == 0 and body["mergeType"] == 0
    assert body["currentPage"] == 1 and body["pageSize"] == 20 and body["title"] == "真空"


def test_sdgp_fetch_notices_empty(monkeypatch):
    """空结果路径：空结构 + search_all 空首页不再翻页。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={
            "code": 200, "success": True,
            "result": {"data": {"records": [], "total": 0, "pages": 0}},
        })

    client = _mock_client(monkeypatch, handler)
    try:
        out = sdgp_mod.fetch_notices("0302", "真空", client=client)
        assert out == {"records": [], "total": 0, "pages": 0}
        assert list(sdgp_mod.search_all("0302", "真空", client=client)) == []
        assert calls["n"] == 2, "直接拉取 1 次 + search_all 首页 1 次；空页不再翻页"
    finally:
        client.close()


def test_sdgp_fetch_notices_upstream_502(monkeypatch):
    """非 200 路径：502 → 重试 MAX_RETRIES 次全败 → 空结构 + mark_bad。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, text="Bad Gateway")

    client = _mock_client(monkeypatch, handler)
    try:
        out = sdgp_mod.fetch_notices("0301", "真空", client=client)
        assert out == {"records": [], "total": 0, "pages": 0}
        assert calls["n"] == sdgp_mod.MAX_RETRIES + 1
        from app.collectors.resilience import get_health

        health = get_health("sdgp")
        assert health.requests_failed == sdgp_mod.MAX_RETRIES + 1
    finally:
        client.close()


def test_sdgp_fetch_notices_code_999(monkeypatch):
    """业务失败路径（乱加参数实测 999）：HTTP 200 但 code!=200 → 空结构。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"code": 999, "msg": "非法参数"})

    client = _mock_client(monkeypatch, handler)
    try:
        out = sdgp_mod.fetch_notices("0301", "真空", client=client)
        assert out == {"records": [], "total": 0, "pages": 0}
        assert calls["n"] == 1, "业务失败不重试（参数用法问题，非上游健康问题）"
    finally:
        client.close()


def test_sdgp_normalize_notice():
    """归一：date 截到日 / area_name / notice_id；无标题丢弃。"""
    n = sdgp_mod.normalize_notice(dict(_RECORD))
    assert n == {
        "title": "青岛大学6英寸超高真空多靶磁控溅射仪采购公告",
        "date": "2026-11-05",
        "area_name": "山东省本级",
        "notice_id": "ENC123",
    }
    assert sdgp_mod.normalize_notice({"title": "  "}) is None
    assert sdgp_mod.normalize_notice({}) is None


def test_sdgp_search_all_paging_guard(monkeypatch):
    """分页保险：服务端 pages=99 被 max_pages 钳住；空页即停。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.read().decode("utf-8"))["currentPage"]
        calls.append(page)
        if page == 1:
            return httpx.Response(200, json={"code": 200, "result": {"data": {
                "records": [dict(_RECORD)], "total": 40, "pages": 99}}})
        return httpx.Response(200, json={"code": 200, "result": {"data": {
            "records": [], "total": 40, "pages": 99}}})

    client = _mock_client(monkeypatch, handler)
    try:
        got = list(sdgp_mod.search_all("0301", "真空", max_pages=3, client=client))
    finally:
        client.close()
    assert len(got) == 1
    assert calls == [1, 2]


# ---------------------------------------------------------------------------
# job：语义映射 / buyer 沉淀 / 幂等 / 宽词
# ---------------------------------------------------------------------------
_TENDER_TITLE = "山东磁谷真空装备有限公司磁悬浮真空泵采购项目招标公告"
_WIN_TITLE = "山东磁谷真空装备有限公司磁悬浮真空泵采购项目中标公告"
_NO_BUYER_TITLE = "6英寸超高真空多靶磁控溅射仪采购公告"


def _fake_search_all(col_code, keyword, max_pages=3, client=None):
    if keyword != "真空" or max_pages != 3:
        return
    if col_code == sdgp_mod.COL_TENDER:
        yield {"date": "2026-11-05 20:22:08", "title": _TENDER_TITLE,
               "areaName": "山东省本级", "id": "R1"}
        yield {"date": "2026-11-06 09:00:00", "title": _NO_BUYER_TITLE,
               "areaName": "山东省本级", "id": "R2"}
    elif col_code == sdgp_mod.COL_RESULT:
        yield {"date": "2026-11-10 15:30:00", "title": _WIN_TITLE,
               "areaName": "山东省本级", "id": "R3"}


def test_sdgp_scan_semantics(monkeypatch, client, admin_headers):
    """0301→tender+buyer(sdgp_buyer, 山东省)；0302→tender_win（结果公告视角）；
    无买方不发明；二跑幂等；词表宽词使用（BS 轮教训）。"""
    from app import jobs as jobs_mod
    from app.vertical import sdgp_terms

    used_cols: list[str] = []
    used_kws: list[str] = []

    def fake(col_code, keyword, max_pages=3, client=None):
        used_cols.append(col_code)
        used_kws.append(keyword)
        yield from _fake_search_all(col_code, keyword, max_pages, client)

    monkeypatch.setattr("app.collectors.sdgp.search_all", fake)

    stats = jobs_mod.run_sdgp_scan(max_pages=3)
    assert stats["notices"] == 3
    assert stats["buyers"] == 1
    assert stats["signals"] == 2
    assert stats["no_buyer_skipped"] == 1  # 抽不到采购人如实跳过不发明

    # 词表使用：全部关键词 = sdgp_terms（品类宽词 ∪ 山东大本营行业细分，
    # 2026-11-15 扩容），且两栏目都扫了
    assert set(used_kws) == set(sdgp_terms())
    assert set(used_cols) == {"0301", "0302"}

    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(
            Enterprise.name == "山东磁谷真空装备有限公司"
        ).one()
        assert ent.source == "sdgp_buyer"
        assert ent.province == "山东省"  # 确定性映射
        tender = (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.type == "tender")
            .one()
        )
        assert tender.title == _TENDER_TITLE
        assert tender.url == "" and tender.base_score == 30.0
        assert tender.evidence_claim == _TENDER_TITLE
        assert tender.pub_date == "2026-11-05"
        win = (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.type == "tender_win")
            .one()
        )
        assert win.title == _WIN_TITLE
        assert win.base_score == 60.0
        assert win.evidence_claim == _WIN_TITLE + "（结果公告）"  # 买方视角如实
        assert win.pub_date == "2026-11-10"
        ent_id = ent.id

    # 二跑幂等：信号与买家建档均不增
    stats2 = jobs_mod.run_sdgp_scan(max_pages=3)
    assert stats2["notices"] == 3
    assert stats2["buyers"] == 0
    assert stats2["signals"] == 0

    # 清理（避免毒化本模块后续用例的矩阵行断言独立性——保留亦可，此处按 bs 同款）
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.enterprise_id == ent_id).delete()
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == ent_id).delete()
        s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
        s.commit()


def test_sdgp_scan_empty_terms_skips(monkeypatch):
    """词表为空：优雅跳过（不打任何请求）。"""
    import app.vertical as vertical_mod
    from app import jobs as jobs_mod

    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        yield from ()

    monkeypatch.setattr(vertical_mod, "sdgp_terms", lambda: [])
    monkeypatch.setattr("app.collectors.sdgp.search_all", fake)
    stats = jobs_mod.run_sdgp_scan(max_pages=3)
    assert stats == {"notices": 0, "buyers": 0, "signals": 0, "no_buyer_skipped": 0}
    assert called["n"] == 0


def test_sdgp_scan_disabled_shortcircuit(monkeypatch):
    """来源停用即入口短路（SPEC-MULTI S1），不打网络。"""
    from app import jobs as jobs_mod

    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        yield from ()

    monkeypatch.setattr("app.collectors.sdgp.search_all", fake)
    with SessionLocal() as s:
        kv = s.get(SettingsKV, "source_disabled")
        if kv is None:
            s.add(SettingsKV(key="source_disabled", value='["sdgp"]'))
        else:
            kv.value = '["sdgp"]'
        s.commit()
    try:
        from app.jobs import run_sdgp_scan

        assert run_sdgp_scan(max_pages=3) == {"skipped_disabled": True}
        assert called["n"] == 0
    finally:
        with SessionLocal() as s:
            row = s.get(SettingsKV, "source_disabled")
            if row is not None:
                s.delete(row)
                s.commit()


# ---------------------------------------------------------------------------
# registry：注册完备性 + 派生（信息中心来源矩阵 / 新鲜度仪表盘零改动接入）
# ---------------------------------------------------------------------------
def test_sdgp_registry_completeness():
    from app.sources import SOURCES, channel_variants, source_label

    d = SOURCES.get("sdgp")
    assert d is not None, "sdgp 必须注册进 SOURCES"
    assert d.label_cn == "山东政采" and d.category == "tender"
    assert d.aliases == ("sdgp_buyer",)
    assert d.sla_job == "sdgp" and d.sla_label == "山东政采采集" and d.sla_hours == 14  # 双时段后最大间隔 ~12h
    assert channel_variants("sdgp") == ("sdgp", "sdgp_buyer")
    assert source_label("sdgp_buyer") == "山东政采买家"  # ALIAS_LABELS 独立展示名


def test_sdgp_dashboard_rows(client, admin_headers):
    """新鲜度仪表盘出现「山东政采采集」；来源矩阵出现 sdgp_buyer 行。"""
    from app.jobs import mark_watermark

    with SessionLocal() as s:
        mark_watermark(s, "sdgp", "ok", {"notices": 3, "buyers": 1, "signals": 2})
        # 矩阵行需要真实企业：建一粒 sdgp_buyer 沙粒
        ent = Enterprise(name="SDGP 矩阵沙粒企业", source="sdgp_buyer", province="山东省")
        s.add(ent)
        s.flush()
        ent_id = ent.id
        s.commit()
    try:
        r = client.get("/api/quality/freshness", headers=admin_headers)
        assert r.status_code == 200
        row = next(x for x in r.json()["items"] if x["job"] == "sdgp")
        assert row["label"] == "山东政采采集" and row["sla_hours"] == 14

        r2 = client.get("/api/data/overview", headers=admin_headers)
        assert r2.status_code == 200
        labels = {x["source"]: x["label"] for x in r2.json()["sources"]}
        assert labels.get("sdgp_buyer") == "山东政采买家"
    finally:
        with SessionLocal() as s:
            s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
            s.commit()


def test_outlinks_collect(client, admin_headers):
    """SPEC-DEEPLINK 官网直查深链（用户定调"采不进来就跳出去"）：
    注册表 search_url 模板 → keyword urlencode 替换；未配置模板的源不返回。"""
    from urllib.parse import quote

    r = client.get("/api/outlinks/collect", params={"keyword": "真空镀膜"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["keyword"] == "真空镀膜"
    by_key = {x["key"]: x for x in d["items"]}
    # 三个已配模板的源全部出现
    assert {"ggzy", "ccgp", "sdgp"} <= set(by_key)
    # sdgp：title 参数带 urlencode 关键词 + 栏目/区域固定
    assert by_key["sdgp"]["label"] == "山东政采"
    assert (
        "https://www.ccgp-shandong.gov.cn/search?title="
        + quote("真空镀膜", safe="")
        + "&colCode=0301&area=370000"
    ) == by_key["sdgp"]["url"]
    # ccgp：kw 参数带 urlencode 关键词
    assert "kw=" + quote("真空镀膜", safe="") in by_key["ccgp"]["url"]
    # 未配置模板的源（poi/proposal_*）不出现
    assert "poi" not in by_key and "proposal_gd" not in by_key
    # 空关键词回退缺省
    r2 = client.get("/api/outlinks/collect", headers=admin_headers)
    assert r2.status_code == 200 and r2.json()["keyword"] == "真空"
    # 未登录 401
    assert client.get("/api/outlinks/collect").status_code == 401


def test_enterprise_external_links_bid_categories(client, admin_headers):
    """SPEC-DEEPLINK 企业导航层补标讯直查：招投标类目新增政采网/山东政采
    按企业名搜公告——{name} 位置必须替换为 urlencode 后的企业名。"""
    from urllib.parse import quote

    name = "青岛大学智能制造研究院"
    with SessionLocal() as s:
        ent = Enterprise(name=name, source="manual")
        s.add(ent)
        s.commit()
        ent_id = ent.id
    try:
        r = client.get(f"/api/enterprises/{ent_id}/external-links", headers=admin_headers)
        assert r.status_code == 200, r.text
        cats = {c["category"]: c["sources"] for c in r.json()["categories"]}
        bid = cats.get("招投标") or []
        by_key = {x["key"]: x for x in bid}
        # 新增两个标讯直查源
        assert {"ccgp_name", "sdgp_name"} <= set(by_key)
        qn = quote(name, safe="")
        assert f"kw={qn}" in by_key["ccgp_name"]["url"]
        assert f"title={qn}" in by_key["sdgp_name"]["url"]
        assert "colCode=0301" in by_key["sdgp_name"]["url"]
    finally:
        with SessionLocal() as s:
            s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
            s.commit()
