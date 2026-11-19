"""SPEC-HUNAN 回归：湖南政采——MockTransport 三路/词表过滤/urlencode 红线/任务语义/注册表。

契约（docs/SPEC-HUNAN.md，2026-11-19 实弹固化）：luban 平台无关键词接口
——POST searchHome 拉当日块 → 本地 sdgp_terms 过滤 → 命中建档（hunan_buyer
+湖南省）+ tender 信号（url=luban 详情真实链接）→ 详情电话入联系人。
articleId 含 / + = 必须 quote(safe="") 全编码（红线断言）。
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-hunan-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import hunan as hunan_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
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
def _reset_hunan_health():
    """resilience 域级健康度进程内单例——每测前后重置，防非 200 用例的
    mark_bad 熔断毒化后续用例（进程内 _HEALTHS 共享）。"""
    from app.collectors import resilience

    resilience._HEALTHS.pop("hunan", None)
    yield
    resilience._HEALTHS.pop("hunan", None)


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
    monkeypatch.setattr(hunan_mod, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(hunan_mod, "_last_request_at", 0.0)
    return httpx.Client(transport=httpx.MockTransport(handler))


# 实弹抓包形态（scripts/hunan_list_cap.mjs / 2026-11-19 httpx 实弹）
_CHILD = {
    "title": "湖南省移动边界站能力建设项目公开招标招标公告",
    "articleId": "HMIZyWkowYu2W3WlOPJGBw==",
    "pubDate": 1795084828000,
    "bidOpeningTime": None,
    "publishDateString": "2026-11-19",
    "districtName": "湖南省本级",
    "purchaseMethod": "公开招标",
}


def _home_ok(children: list[dict]) -> dict:
    return {
        "success": True,
        "result": {"data": {
            "todayTotal": len(children), "total": len(children),
            "validCount": len(children), "code": "160-396330",
            "moduleName": "采购公告", "firstId": None, "children": children,
        }},
        "error": None,
    }


# ---------------------------------------------------------------------------
# collector：MockTransport 三路 + 词表过滤 + urlencode 红线
# ---------------------------------------------------------------------------
def test_hunan_fetch_home_blocks_ok_strict_body(monkeypatch):
    """正常路径：请求体严格 8 参数红线 + children 拍平四字段。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=_home_ok([dict(_CHILD)]))

    client = _mock_client(monkeypatch, handler)
    try:
        out = hunan_mod.fetch_home_blocks(client=client)
    finally:
        client.close()
    assert len(out) == 1
    assert out[0] == {
        "title": "湖南省移动边界站能力建设项目公开招标招标公告",
        "article_id": "HMIZyWkowYu2W3WlOPJGBw==",
        "pub_date": "2026-11-19",
        "district_name": "湖南省本级",
    }
    body = bodies[0]
    # 请求体严格红线（实弹固化 8 键）
    assert set(body) == {
        "code", "subCodes", "districtCode", "pageSize",
        "needTotal", "needNewCnt", "isProvince",
    }
    assert body["code"] == "160-396330"
    assert body["subCodes"] == ["160-945988"]
    assert body["districtCode"] == "430000"
    assert body["pageSize"] == 10
    assert body["needTotal"] is False and body["needNewCnt"] is False
    assert body["isProvince"] is True


def test_hunan_fetch_home_blocks_epoch_fallback(monkeypatch):
    """publishDateString 缺失 → pubDate(epoch ms) 兜底转北京日期。"""

    def handler(request: httpx.Request) -> httpx.Response:
        child = {
            "title": "长沙某大学真空镀膜设备采购公告",
            "articleId": "edBzSOX3/QwVDBH0MqH66w==",
            "pubDate": 1795084828000,
            "publishDateString": None,
            "districtName": None,
        }
        return httpx.Response(200, json=_home_ok([child]))

    client = _mock_client(monkeypatch, handler)
    try:
        out = hunan_mod.fetch_home_blocks(client=client)
    finally:
        client.close()
    assert len(out) == 1
    assert out[0]["pub_date"] == "2026-11-19"  # epoch ms → 北京日期
    assert out[0]["district_name"] is None


def test_hunan_fetch_home_blocks_empty(monkeypatch):
    """空 children：拍平空列表（不报错不重试）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_home_ok([]))

    client = _mock_client(monkeypatch, handler)
    try:
        assert hunan_mod.fetch_home_blocks(client=client) == []
        assert calls["n"] == 1
    finally:
        client.close()


def test_hunan_fetch_home_blocks_not_json(monkeypatch):
    """非 JSON（拦截页/HTML 直出）：重试 MAX_RETRIES 全败 → 空列表 + mark_bad。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<html>waf blocked</html>")

    client = _mock_client(monkeypatch, handler)
    try:
        assert hunan_mod.fetch_home_blocks(client=client) == []
        assert calls["n"] == hunan_mod.MAX_RETRIES + 1
        from app.collectors.resilience import get_health

        assert get_health("hunan").requests_failed == hunan_mod.MAX_RETRIES + 1
    finally:
        client.close()


def test_hunan_filter_by_terms():
    """本地词表过滤：title 含任一词即命中；不含不命中。"""
    blocks = [
        {"title": "湖南磁谷真空装备有限公司磁悬浮真空泵采购项目招标公告",
         "article_id": "A", "pub_date": "2026-11-19", "district_name": "湖南省本级"},
        {"title": "湖南省移动边界站能力建设项目公开招标招标公告",
         "article_id": "B", "pub_date": "2026-11-19", "district_name": "湖南省本级"},
    ]
    out = hunan_mod.filter_by_terms(blocks, ["真空", "镀膜"])
    assert [b["article_id"] for b in out] == ["A"]
    assert hunan_mod.filter_by_terms(blocks, ["不存在词"]) == []
    assert hunan_mod.filter_by_terms(blocks, []) == []
    # 空词/空串不误命中
    assert hunan_mod.filter_by_terms(blocks, [""]) == []


def test_hunan_fetch_detail_urlencode_redline(monkeypatch):
    """红线：articleId 含 / + = 必须全编码——裸拼 + 会被拆空格、/ 断路径。"""
    raw_id = "G+zK1JEEYBhxeI7K/GffJA=="
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={
            "success": True,
            "result": {"data": {
                "title": "某大学真空镀膜设备采购公告",
                "articleId": raw_id,
                "content": "<div>联系人：张三</div><p>电话：053285950922</p>",
            }},
            "error": None,
        })

    client = _mock_client(monkeypatch, handler)
    try:
        d = hunan_mod.fetch_detail(raw_id, client=client)
    finally:
        client.close()
    assert len(seen) == 1
    url = seen[0]
    assert "%2F" in url, "articleId 内的 / 必须编码为 %2F"
    assert "%2B" in url, "articleId 内的 + 必须编码为 %2B"
    assert "%3D%3D" in url, "articleId 尾部 == 必须编码"
    assert "parentId=622785" in url and "timestamp=" in url
    # 解析正确性：正文 HTML 剥标签压空白
    assert d == {
        "title": "某大学真空镀膜设备采购公告",
        "body_text": "联系人：张三 电话：053285950922",
    }


def test_hunan_fetch_detail_failure_none(monkeypatch):
    """失败路径：非 200 / 业务失败（success!=True）→ None 不重试。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="gone")

    client = _mock_client(monkeypatch, handler)
    try:
        assert hunan_mod.fetch_detail("old-id==", client=client) is None
        assert calls["n"] == 1, "detail 失败不重试（旧 id 过期机制未实测）"
    finally:
        client.close()

    calls["n"] = 0

    def handler2(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"success": False, "error": "expired"})

    client = _mock_client(monkeypatch, handler2)
    try:
        assert hunan_mod.fetch_detail("old-id==", client=client) is None
        assert calls["n"] == 1
    finally:
        client.close()


def test_hunan_detail_link_format():
    """Signal.url 溯源链接：luban/front/detail + parentId + 全编码 articleId。"""
    from urllib.parse import quote

    link = hunan_mod.detail_link("edBzSOX3/QwVDBH0MqH66w==")
    assert link == (
        "http://www.ccgp-hunan.gov.cn/luban/front/detail?parentId=622785"
        "&articleId=" + quote("edBzSOX3/QwVDBH0MqH66w==", safe="")
    )
    assert "%2F" in link and "%3D%3D" in link


# ---------------------------------------------------------------------------
# job：建档/信号/电话/幂等/停用短路/空词表
# ---------------------------------------------------------------------------
_TENDER_TITLE = "湖南磁谷真空装备有限公司磁悬浮真空泵采购项目招标公告"
_NO_BUYER_TITLE = "6英寸超高真空多靶磁控溅射仪采购公告"
_MISS_TITLE = "湖南省移动边界站能力建设项目公开招标招标公告"
_HIT_ID = "edBzSOX3/QwVDBH0MqH66w=="


def _fake_blocks() -> list[dict]:
    return [
        {"title": _TENDER_TITLE, "article_id": _HIT_ID,
         "pub_date": "2026-11-19", "district_name": "湖南省本级"},
        {"title": _MISS_TITLE, "article_id": "HMIZyWkowYu2W3WlOPJGBw==",
         "pub_date": "2026-11-19", "district_name": "湖南省本级"},
        {"title": _NO_BUYER_TITLE, "article_id": "6BPHEelnf2hrk05UWXCxFQ==",
         "pub_date": "2026-11-19", "district_name": "湘潭市"},
    ]


def _fake_detail(article_id, parent_id="622785", client=None):
    assert parent_id == "622785"
    return {
        "title": _TENDER_TITLE,
        "body_text": "项目联系人：张三 电话：053285950922 地址：长沙市",
    }


def test_hunan_scan_semantics(monkeypatch, client, admin_headers):
    """命中建档（hunan_buyer+湖南省）+ tender 信号（url=详情链）+ 详情电话
    入库；不命中跳过；抽不到买方不发明；二跑幂等零新增。"""
    from urllib.parse import quote

    from app import jobs as jobs_mod

    monkeypatch.setattr(
        "app.collectors.hunan.fetch_home_blocks",
        lambda page_size=10, client=None: _fake_blocks(),
    )
    monkeypatch.setattr("app.collectors.hunan.fetch_detail", _fake_detail)

    stats = jobs_mod.run_hunan_scan()
    assert stats["notices"] == 3
    assert stats["hits"] == 2  # 真空命中 2 条，能力建设站不命中
    assert stats["buyers"] == 1  # 仅买方可抽取的那条建档
    assert stats["signals"] == 1
    assert stats["details"] == 1
    assert stats["contacts_new"] == 1
    assert stats["contacts_updated"] == 0

    detail_url = (
        "http://www.ccgp-hunan.gov.cn/luban/front/detail?parentId=622785"
        "&articleId=" + quote(_HIT_ID, safe="")
    )
    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(
            Enterprise.name == "湖南磁谷真空装备有限公司"
        ).one()
        assert ent.source == "hunan_buyer"
        assert ent.province == "湖南省"  # 确定性映射
        sig = (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.type == "tender")
            .one()
        )
        assert sig.title == _TENDER_TITLE
        assert sig.url == detail_url  # 可溯源真实链接（比山东 url=""强）
        assert "%2F" in sig.url  # urlencode 红线二次落实
        assert sig.base_score == 30.0
        assert sig.pub_date == "2026-11-19"
        assert sig.evidence_claim == _TENDER_TITLE
        assert sig.hit_part == "title"
        assert sig.raw_excerpt == "湖南政采采购公告｜湖南省本级"
        c = (
            s.query(Contact)
            .filter(Contact.enterprise_id == ent.id, Contact.role == "notice_contact")
            .one()
        )
        assert c.source == "hunan"
        assert c.name == "公告法定公开电话"
        from app.phones import load_phones

        assert load_phones(c.phones_json) == ["053285950922"]
        ent_id = ent.id

    # 二跑幂等：信号与买家建档均不增；已有 notice_contact 不再拉详情
    stats2 = jobs_mod.run_hunan_scan()
    assert stats2["notices"] == 3 and stats2["hits"] == 2
    assert stats2["buyers"] == 0
    assert stats2["signals"] == 0
    assert stats2["details"] == 0
    assert stats2["contacts_new"] == 0
    assert stats2["contacts_updated"] == 0

    # 清理（防毒化本模块后续用例）
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.enterprise_id == ent_id).delete()
        s.query(Contact).filter(Contact.enterprise_id == ent_id).delete()
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == ent_id).delete()
        s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
        s.commit()


def test_hunan_scan_empty_terms_skips(monkeypatch):
    """词表为空：优雅跳过（不打任何请求）。"""
    import app.vertical as vertical_mod
    from app import jobs as jobs_mod

    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        return []

    monkeypatch.setattr(vertical_mod, "sdgp_terms", lambda: [])
    monkeypatch.setattr("app.collectors.hunan.fetch_home_blocks", fake)
    stats = jobs_mod.run_hunan_scan()
    assert stats == {
        "notices": 0, "hits": 0, "buyers": 0, "signals": 0,
        "details": 0, "contacts_new": 0, "contacts_updated": 0,
    }
    assert called["n"] == 0


def test_hunan_scan_disabled_shortcircuit(monkeypatch):
    """来源停用即入口短路（SPEC-MULTI S1），不打网络。"""
    from app import jobs as jobs_mod

    called = {"n": 0}

    def fake(*args, **kwargs):
        called["n"] += 1
        return []

    monkeypatch.setattr("app.collectors.hunan.fetch_home_blocks", fake)
    with SessionLocal() as s:
        kv = s.get(SettingsKV, "source_disabled")
        if kv is None:
            s.add(SettingsKV(key="source_disabled", value='["hunan"]'))
        else:
            kv.value = '["hunan"]'
        s.commit()
    try:
        from app.jobs import run_hunan_scan

        assert run_hunan_scan() == {"skipped_disabled": True}
        assert called["n"] == 0
    finally:
        with SessionLocal() as s:
            row = s.get(SettingsKV, "source_disabled")
            if row is not None:
                s.delete(row)
                s.commit()


# ---------------------------------------------------------------------------
# registry：注册完备性 + 派生
# ---------------------------------------------------------------------------
def test_hunan_registry_completeness():
    from app.sources import SOURCES, channel_variants, source_label

    d = SOURCES.get("hunan")
    assert d is not None, "hunan 必须注册进 SOURCES"
    assert d.label_cn == "湖南政采" and d.category == "tender"
    assert d.aliases == ("hunan_buyer",)
    assert d.sla_job == "hunan" and d.sla_label == "湖南政采采集"
    assert d.sla_hours == 26  # 每日 06:00 一轮 → 26h 余量
    assert channel_variants("hunan") == ("hunan", "hunan_buyer")
    assert source_label("hunan_buyer") == "湖南政采买家"  # ALIAS_LABELS 独立展示名


def test_hunan_dashboard_row(client, admin_headers):
    """新鲜度仪表盘出现「湖南政采采集」；来源矩阵出现 hunan_buyer 行。"""
    from app.jobs import mark_watermark

    with SessionLocal() as s:
        mark_watermark(s, "hunan", "ok", {"notices": 3, "signals": 1})
        ent = Enterprise(name="HUNAN 矩阵沙粒企业", source="hunan_buyer", province="湖南省")
        s.add(ent)
        s.flush()
        ent_id = ent.id
        s.commit()
    try:
        r = client.get("/api/quality/freshness", headers=admin_headers)
        assert r.status_code == 200
        row = next(x for x in r.json()["items"] if x["job"] == "hunan")
        assert row["label"] == "湖南政采采集" and row["sla_hours"] == 26

        r2 = client.get("/api/data/overview", headers=admin_headers)
        assert r2.status_code == 200
        labels = {x["source"]: x["label"] for x in r2.json()["sources"]}
        assert labels.get("hunan_buyer") == "湖南政采买家"
    finally:
        with SessionLocal() as s:
            s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
            s.commit()
