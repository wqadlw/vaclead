"""SPEC-AHUI 回归：安徽政采 portal/category 形态——Mock 三路/分页 cap/
author 直带采购人建档语义/幂等/停用短路/注册表。

契约（docs/SPEC-AHUI.md，2026-11-21 httpx 实弹五发验证）：**records 在
result.data.data（result.data.total 平级）**——不是常规顶层 records；
author=采购人单位名直接建档（source=anhui_buyer，安徽省只补空），全量
入库不过滤；publishDate 是 epoch ms 转北京日期；HTTPS 必须（HTTP 301
CWAP-waf）；详情页路由实弹 301 到 mall 首页非文章页 → Signal.url=""。
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ahui-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import anhui as anhui_mod  # noqa: E402
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

#: 共享测试库防撞标记（本模块专属——同 title 信号撞 uq 静默回滚的教训）
_MARKER = "AHUI-SPEC-2026"


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    """模块级快照清理：进前记最大 id，出后清本模块触达的行。"""
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
def _reset_anhui_health():
    """resilience 域级健康度进程内单例——每测前后重置 anhui 域，防非 200
    用例的 mark_bad 熔断毒化后续用例（进程内 _HEALTHS 共享）。"""
    from app.collectors import resilience

    resilience._HEALTHS.pop("anhui", None)
    yield
    resilience._HEALTHS.pop("anhui", None)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mock_client(monkeypatch, handler) -> httpx.Client:
    """MockTransport 注入 + 礼貌间隔归零（限速常量在测试里不真睡）。"""
    monkeypatch.setattr(anhui_mod, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(anhui_mod, "_last_request_at", 0.0)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _category_ok(records: list[dict], total: int | None = None) -> dict:
    """实弹抓包形态：records 在 result.data.data，total 与其平级。"""
    return {
        "success": True,
        "result": {"data": {
            "total": total if total is not None else len(records),
            "data": records,
            "empty": not records,
        }},
        "error": None,
    }


# 实弹形态（2026-11-21 芜湖鸠江真实公告；content 列表响应常 None）
_REC = {
    "articleId": "RNr/bSWbEjY4oGhbvmTa2w==",
    "author": "芜湖市鸠江区教育局",
    "title": "2026年秋季鸠江区义务教育阶段印制作业采购单一来源公示",
    "pathName": "单一来源公示",
    "publishDate": 1795071424000,
    "districtName": "鸠江区",
    "content": None,
}


# ---------------------------------------------------------------------------
# collector：MockTransport 三路（正常/空 data/非 JSON）
# ---------------------------------------------------------------------------
def test_anhui_fetch_category_ok(monkeypatch):
    """正常路径：HTTPS + body 五键 + data.data 拍平（author 直带采购人、
    publishDate epoch ms → 北京日期、content 原样透传）。"""
    bodies: list[dict] = []
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        bodies.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=_category_ok([dict(_REC)], total=1963))

    client = _mock_client(monkeypatch, handler)
    try:
        out = anhui_mod.fetch_category(page_no=1, client=client)
    finally:
        client.close()
    assert urls[0].startswith(
        "https://www.ccgp-anhui.gov.cn/portal/category"
    ), "必须 HTTPS（HTTP 301 CWAP-waf 实弹）"
    body = bodies[0]
    assert set(body) == {"pageNo", "pageSize", "categoryCode", "districtCode", "leaf"}
    assert body["pageNo"] == 1 and body["pageSize"] == anhui_mod.PAGE_SIZE
    assert body["categoryCode"] == "ZcyAnnouncement3012"
    assert body["districtCode"] == ["340000"]  # 实测数组形态
    assert body["leaf"] == "0"
    assert out["total"] == 1963
    assert len(out["records"]) == 1
    rec = out["records"][0]
    assert rec == {
        "title": "2026年秋季鸠江区义务教育阶段印制作业采购单一来源公示",
        "author": "芜湖市鸠江区教育局",  # 采购人单位名直带——无需标题抽取
        "article_id": "RNr/bSWbEjY4oGhbvmTa2w==",
        "pub_date": "2026-11-19",  # epoch ms → 北京日期
        "path_name": "单一来源公示",
        "district_name": "鸠江区",
        "content": None,  # 列表响应 content 常 None（A1：本 SPEC 不做详情）
    }


def test_anhui_fetch_category_empty(monkeypatch):
    """空 data（data.data=[]）：拍平空列表（不报错不重试）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_category_ok([]))

    client = _mock_client(monkeypatch, handler)
    try:
        out = anhui_mod.fetch_category(client=client)
        assert out == {"total": 0, "records": []}
        assert calls["n"] == 1
    finally:
        client.close()


def test_anhui_fetch_category_not_json(monkeypatch):
    """非 JSON（拦截页/HTML 直出）：重试 MAX_RETRIES 全败 → 空 + mark_bad。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<html>waf blocked</html>")

    client = _mock_client(monkeypatch, handler)
    try:
        out = anhui_mod.fetch_category(client=client)
        assert out == {"total": None, "records": []}
        assert calls["n"] == anhui_mod.MAX_RETRIES + 1
        from app.collectors.resilience import get_health

        assert get_health("anhui").requests_failed == anhui_mod.MAX_RETRIES + 1
    finally:
        client.close()


# ---------------------------------------------------------------------------
# job：author 直接建档/tender 信号/幂等/分页 cap/停用短路
# ---------------------------------------------------------------------------
def _fake_page(pages: dict[int, list[dict]]):
    """构造 fetch_category 假实现：按 pageNo 返回，未登记页回空。"""
    calls: list[int] = []

    def fake(page_no: int = 1, category_code=anhui_mod.CATEGORY_CODE,
             page_size=anhui_mod.PAGE_SIZE, client=None):
        calls.append(page_no)
        records = pages.get(page_no, [])
        return {"total": 9999 if records else 0, "records": records}

    return fake, calls


def _scan_records(suffix: str, author: str | None) -> list[dict]:
    return [{
        "title": f"{_MARKER} {suffix}",
        "author": author or "",
        "article_id": f"A{suffix}",
        "pub_date": "2026-11-21",
        "path_name": "采购公告",
        "district_name": "芜湖市本级",
        "content": None,
    }]


def test_anhui_scan_semantics(monkeypatch):
    """author 直接 resolve 建档（anhui_buyer+安徽省只补空）+ tender 信号
    （url=""、evidence=title、base_score=30）；author 空跳过计数；二跑幂等。"""
    from app import jobs as jobs_mod

    fake, calls = _fake_page({
        1: _scan_records("真空干燥设备采购", "安徽昊志机电设备有限公司")
        + _scan_records("无主公示", None),
    })
    monkeypatch.setattr("app.collectors.anhui.fetch_category", fake)

    # 共享库残留清理：同 title 信号撞 uq 静默回滚——tests 开头清理
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.title.like(f"%{_MARKER}%")).delete(
            synchronize_session=False
        )
        s.query(Enterprise).filter(Enterprise.name.like(f"%{_MARKER}%")).delete(
            synchronize_session=False
        )
        s.commit()

    try:
        stats = jobs_mod.run_anhui_scan()
        assert calls == [1, 2], "第 2 页空即停（服务端第 1 页=最新）"
        assert stats["pages"] == 1
        assert stats["notices"] == 2
        assert stats["buyers"] == 1, "author 直接建档（无标题抽取）"
        assert stats["signals"] == 1
        assert stats["no_author_skipped"] == 1, "author 空跳过计数"

        with SessionLocal() as s:
            ent = s.query(Enterprise).filter(
                Enterprise.name == "安徽昊志机电设备有限公司"
            ).one()
            assert ent.source == "anhui_buyer"
            assert ent.province == "安徽省", "省份确定性映射只补空"
            sig = (
                s.query(Signal)
                .filter(Signal.enterprise_id == ent.id, Signal.type == "tender")
                .one()
            )
            assert sig.title == f"{_MARKER} 真空干燥设备采购"
            assert sig.url == "", "详情路由实弹 301 mall 首页——回退空"
            assert sig.evidence_claim == sig.title
            assert sig.base_score == 30.0
            assert sig.pub_date == "2026-11-21"
            assert sig.hit_part == "title"
            assert sig.raw_excerpt.startswith("安徽政采采购公告｜")

        # 二跑幂等：信号零新增
        stats2 = jobs_mod.run_anhui_scan()
        assert stats2["notices"] == 2 and stats2["signals"] == 0
        assert stats2["buyers"] == 0
    finally:
        with SessionLocal() as s:
            s.query(Signal).filter(Signal.title.like(f"%{_MARKER}%")).delete(
                synchronize_session=False
            )
            s.query(EnterpriseAlias).filter(
                EnterpriseAlias.alias.like(f"%{_MARKER}%")
            ).delete(synchronize_session=False)
            s.query(Enterprise).filter(Enterprise.name.like(f"%{_MARKER}%")).delete(
                synchronize_session=False
            )
            s.commit()


def test_anhui_scan_pagination_cap(monkeypatch):
    """分页循环 cap 7 页/轮：页页有货也只拉 7 页（15 条/页=105 条/轮防长跑；
    pageSize 实弹上限 15——20 稳定 429）。"""
    from app import jobs as jobs_mod

    fake, calls = _fake_page({
        p: [
            rec
            for i in range(anhui_mod.PAGE_SIZE)
            for rec in _scan_records(f"cap页{p}条{i}", f"安徽{_MARKER}采购单位")
        ]
        for p in range(1, 9)
    })
    monkeypatch.setattr("app.collectors.anhui.fetch_category", fake)
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.title.like(f"%{_MARKER}%")).delete(
            synchronize_session=False
        )
        s.query(Enterprise).filter(Enterprise.name.like(f"%{_MARKER}%")).delete(
            synchronize_session=False
        )
        s.commit()
    try:
        stats = jobs_mod.run_anhui_scan()
        assert calls == [1, 2, 3, 4, 5, 6, 7], "cap 7 页/轮（SPEC-AHUI A2 防长跑）"
        assert stats["pages"] == 7
        assert stats["notices"] == 7 * anhui_mod.PAGE_SIZE  # 7 页 × 15 条
    finally:
        with SessionLocal() as s:
            s.query(Signal).filter(Signal.title.like(f"%{_MARKER}%")).delete(
                synchronize_session=False
            )
            s.query(EnterpriseAlias).filter(
                EnterpriseAlias.alias.like(f"%{_MARKER}%")
            ).delete(synchronize_session=False)
            s.query(Enterprise).filter(Enterprise.name.like(f"%{_MARKER}%")).delete(
                synchronize_session=False
            )
            s.commit()


def test_anhui_scan_disabled(monkeypatch):
    """停用短路 any_source_disabled("anhui")：入口即回 skipped，不打网络。"""
    from app import jobs as jobs_mod

    called: list[int] = []
    fake, _calls = _fake_page({1: _scan_records("停用不拉", "安徽停用测试单位")})

    def spy(page_no: int = 1, **kw):
        called.append(page_no)
        return fake(page_no=page_no, **kw)

    monkeypatch.setattr("app.collectors.anhui.fetch_category", spy)
    with SessionLocal() as s:
        kv = s.get(SettingsKV, "source_disabled")
        if kv is None:
            s.add(SettingsKV(key="source_disabled", value='["anhui"]'))
        else:
            kv.value = '["anhui"]'
        s.commit()
    try:
        assert jobs_mod.run_anhui_scan() == {"skipped_disabled": True}
        assert called == [], "停用后不应打网络"
    finally:
        with SessionLocal() as s:
            row = s.get(SettingsKV, "source_disabled")
            if row is not None:
                s.delete(row)
                s.commit()


# ---------------------------------------------------------------------------
# registry：anhui 注册完备性 + SLA 派生 + 仪表盘
# ---------------------------------------------------------------------------
def test_anhui_registry_completeness():
    from app.sources import SOURCES, channel_variants, source_label

    d = SOURCES.get("anhui")
    assert d is not None, "anhui 必须注册进 SOURCES"
    assert d.label_cn == "安徽政采" and d.category == "tender"
    assert d.aliases == ("anhui_buyer",)
    assert d.sla_job == "anhui" and d.sla_label == "安徽政采采集"
    assert d.sla_hours == 26  # 每日 06:20 一轮 → 26h 余量
    assert d.search_url == "https://www.ccgp-anhui.gov.cn/"
    assert channel_variants("anhui") == ("anhui", "anhui_buyer")
    assert channel_variants("anhui_buyer") == ("anhui", "anhui_buyer")
    assert source_label("anhui") == "安徽政采"
    assert source_label("anhui_buyer") == "安徽政采买家"  # ALIAS_LABELS 独立展示名


def test_anhui_freshness_sla():
    """sla_job="anhui" 派生仪表盘 SLA 行（registry 派生，quality 不手登记）。"""
    from app.api.quality import _FRESHNESS_SLA

    assert _FRESHNESS_SLA["anhui"] == {"label": "安徽政采采集", "hours": 26}


def test_anhui_dashboard_row(client, admin_headers):
    """新鲜度仪表盘出现「安徽政采采集」；来源矩阵出现 anhui_buyer 行。"""
    from app.jobs import mark_watermark

    ent_id = None
    with SessionLocal() as s:
        mark_watermark(s, "anhui", "ok", {"notices": 2, "signals": 1})
        ent = Enterprise(name=f"{_MARKER} 矩阵沙粒企业", source="anhui_buyer",
                         province="安徽省")
        s.add(ent)
        s.flush()
        ent_id = ent.id
        s.commit()
    try:
        r = client.get("/api/quality/freshness", headers=admin_headers)
        assert r.status_code == 200
        row = next(x for x in r.json()["items"] if x["job"] == "anhui")
        assert row["label"] == "安徽政采采集" and row["sla_hours"] == 26

        r2 = client.get("/api/data/overview", headers=admin_headers)
        assert r2.status_code == 200
        labels = {x["source"]: x["label"] for x in r2.json()["sources"]}
        assert labels.get("anhui_buyer") == "安徽政采买家"
    finally:
        with SessionLocal() as s:
            s.query(Enterprise).filter(Enterprise.id == ent_id).delete(
                synchronize_session=False
            )
            s.commit()
