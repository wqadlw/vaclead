"""SPEC-LUBAN 回归：政采云三省（沪黔桂）意向公开——Mock 三路/district 键红线/
全量入库任务语义/单省失败不串/注册表。

契约（docs/SPEC-LUBAN.md，luban 平台 searchHome 组件同款沪黔双省验证）：
意向标题无设备词——**全量入库**不过滤：建档（xx_buyer+省确定性）+
proposal 信号（base_score=25，url=""——detail 契约未验证）。
district 空则请求体不带 districtCode 键（上海实测空 district 才通）。
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-luban-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import luban as luban_mod  # noqa: E402
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
def _reset_luban_health():
    """resilience 域级健康度进程内单例——每测前后重置三省域，防非 200
    用例的 mark_bad 熔断毒化后续用例（进程内 _HEALTHS 共享）。"""
    from app.collectors import resilience

    for dom in ("shanghai", "guizhou", "guangxi"):
        resilience._HEALTHS.pop(dom, None)
    yield
    for dom in ("shanghai", "guizhou", "guangxi"):
        resilience._HEALTHS.pop(dom, None)


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
    monkeypatch.setattr(luban_mod, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(luban_mod, "_last_request_at", 0.0)
    return httpx.Client(transport=httpx.MockTransport(handler))


# 实弹抓包形态（luban searchHome 组件，沪黔桂同款结构）
_CHILD = {
    "title": "上海联影医疗器械有限公司磁悬浮真空泵采购意向",
    "articleId": "HMIZyWkowYu2W3WlOPJGBw==",
    "pubDate": 1795084828000,
    "publishDateString": "2026-11-21",
    "districtName": "上海市本级",
}


def _home_ok(children: list[dict]) -> dict:
    return {
        "success": True,
        "result": {"data": {
            "todayTotal": len(children), "total": len(children),
            "children": children,
        }},
        "error": None,
    }


# ---------------------------------------------------------------------------
# collector：MockTransport 三路 + district 键红线 + 假省优雅失败
# ---------------------------------------------------------------------------
def test_luban_fetch_home_ok_shanghai_no_district_key(monkeypatch):
    """正常路径（上海）：district 空 → 请求体**不带 districtCode 键**（实测
    空 district 才通）+ children 拍平四字段 + base 路由正确。"""
    bodies: list[dict] = []
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        bodies.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=_home_ok([dict(_CHILD)]))

    client = _mock_client(monkeypatch, handler)
    try:
        out = luban_mod.fetch_luban_home("shanghai", client=client)
    finally:
        client.close()
    assert len(out) == 1
    assert out[0] == {
        "title": "上海联影医疗器械有限公司磁悬浮真空泵采购意向",
        "article_id": "HMIZyWkowYu2W3WlOPJGBw==",
        "pub_date": "2026-11-21",
        "district_name": "上海市本级",
    }
    assert urls[0].startswith("https://www.zfcg.sh.gov.cn/portal/searchHome")
    body = bodies[0]
    # 严格 6 键红线：district 空 → districtCode 不带该键
    assert set(body) == {
        "code", "subCodes", "pageSize", "needTotal", "needNewCnt", "isProvince",
    }
    assert "districtCode" not in body, "上海 district 为空必须不带该键"
    assert body["code"] == "77-379845"
    assert body["subCodes"] == []
    assert body["pageSize"] == 10
    assert body["needTotal"] is False and body["needNewCnt"] is False
    assert body["isProvince"] is True


def test_luban_fetch_home_guangxi_district_and_subcodes(monkeypatch):
    """正常路径（广西）：districtCode=459900 必须带上 + 意向子码齐全。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(200, json=_home_ok([]))

    client = _mock_client(monkeypatch, handler)
    try:
        assert luban_mod.fetch_luban_home("guangxi", client=client) == []
    finally:
        client.close()
    body = bodies[0]
    assert "districtCode" in body, "广西必须带 districtCode 圈定省本级"
    assert body["districtCode"] == "459900"
    assert body["code"] == "61-266648"
    assert body["subCodes"] == ["ZcyAnnouncement10016"]


def test_luban_fetch_home_epoch_fallback(monkeypatch):
    """publishDateString 缺失 → pubDate(epoch ms) 兜底转北京日期。"""

    def handler(request: httpx.Request) -> httpx.Response:
        child = {
            "title": "上海某院镀膜设备采购意向",
            "articleId": "edBzSOX3/QwVDBH0MqH66w==",
            "pubDate": 1795084828000,
            "publishDateString": None,
            "districtName": None,
        }
        return httpx.Response(200, json=_home_ok([child]))

    client = _mock_client(monkeypatch, handler)
    try:
        out = luban_mod.fetch_luban_home("guizhou", client=client)
    finally:
        client.close()
    assert len(out) == 1
    assert out[0]["pub_date"] == "2026-11-19"  # epoch ms → 北京日期
    assert out[0]["district_name"] is None


def test_luban_fetch_home_empty(monkeypatch):
    """空 children：拍平空列表（不报错不重试）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_home_ok([]))

    client = _mock_client(monkeypatch, handler)
    try:
        assert luban_mod.fetch_luban_home("shanghai", client=client) == []
        assert calls["n"] == 1
    finally:
        client.close()


def test_luban_fetch_home_not_json(monkeypatch):
    """非 JSON（拦截页/HTML 直出）：重试 MAX_RETRIES 全败 → 空列表 + mark_bad。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="<html>waf blocked</html>")

    client = _mock_client(monkeypatch, handler)
    try:
        assert luban_mod.fetch_luban_home("shanghai", client=client) == []
        assert calls["n"] == luban_mod.MAX_RETRIES + 1
        from app.collectors.resilience import get_health

        assert get_health("shanghai").requests_failed == luban_mod.MAX_RETRIES + 1
    finally:
        client.close()


def test_luban_fetch_home_business_fail(monkeypatch):
    """业务失败（success!=True）：不重试不熔断 → []（传输层通的）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"success": False, "error": "bad code"})

    client = _mock_client(monkeypatch, handler)
    try:
        assert luban_mod.fetch_luban_home("guizhou", client=client) == []
        assert calls["n"] == 1
        from app.collectors.resilience import get_health

        assert get_health("guizhou").requests_failed == 0
    finally:
        client.close()


def test_luban_fetch_home_unknown_province(monkeypatch):
    """假省（LUBAN_PROVINCES 未登记）：优雅 [] 不打任何网络请求。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_home_ok([dict(_CHILD)]))

    client = _mock_client(monkeypatch, handler)
    try:
        assert luban_mod.fetch_luban_home("louisiana", client=client) == []
        assert calls["n"] == 0, "未知省不应打网络"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# job：全量入库建档/proposal 信号/幂等/单省炸不串/逐省停用短路
# ---------------------------------------------------------------------------
def _fake_blocks_for(suffix: str) -> dict[str, list[dict]]:
    """三省假块：每省首条买方可抽取（全量入库——不筛设备词），末条无买方。"""
    return {
        "shanghai": [
            {"title": f"上海联影医疗器械有限公司{suffix}采购意向",
             "article_id": "SHA1", "pub_date": "2026-11-21", "district_name": "上海市本级"},
            {"title": "真空获得设备采购意向",
             "article_id": "SHA2", "pub_date": "2026-11-21", "district_name": None},
        ],
        "guizhou": [
            {"title": f"贵州共赢机械设备有限公司{suffix}采购意向",
             "article_id": "GZ1", "pub_date": "2026-11-21", "district_name": "贵州省本级"},
        ],
        "guangxi": [
            {"title": f"广西绿城真空装备有限公司{suffix}采购意向",
             "article_id": "GX1", "pub_date": "2026-11-21", "district_name": "广西壮族自治区"},
        ],
    }


def test_luban_scan_semantics(monkeypatch, client, admin_headers):
    """2026-11-21 语义定案（用户拍板"不做政采买家"+意向全为政采买家形态）：
    **意向监控模式**——单位名精确命中企业库已有企业才挂 proposal 信号
    （老户最早期信号）；陌生单位跳过不建档不发明。"""
    from app import jobs as jobs_mod

    # 预置已有企业（命中路径）：三省首条标题的企业名
    seeded = {}
    with SessionLocal() as s:
        for nm, src, prov in [
            ("上海联影医疗器械有限公司", "poi", "上海市"),
            ("贵州共赢机械设备有限公司", "poi", "贵州省"),
            ("广西绿城真空装备有限公司", "poi", "广西壮族自治区"),
        ]:
            e = Enterprise(name=nm, source=src, province=prov)
            s.add(e)
            s.flush()
            seeded[nm] = e.id
        s.commit()

    blocks = _fake_blocks_for("污水处理站")
    monkeypatch.setattr(
        "app.collectors.luban.fetch_luban_home",
        lambda prov_key, page_size=10, client=None: blocks.get(prov_key, []),
    )
    # 共享测试库残留清理：旧语义跑过的同名信号会让新语义 add 撞 uq 静默回滚
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.title.like("%污水处理站采购意向%")).delete(
            synchronize_session=False
        )
        s.commit()

    stats = jobs_mod.run_luban_scan()
    assert stats["shanghai"] == {"notices": 2, "buyers": 0, "signals": 1}
    assert stats["guizhou"] == {"notices": 1, "buyers": 0, "signals": 1}
    assert stats["guangxi"] == {"notices": 1, "buyers": 0, "signals": 1}
    assert stats["total_notices"] == 4
    assert stats["total_buyers"] == 0, "新语义：不再建档（意向监控）"
    assert stats["total_signals"] == 3

    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(
            Enterprise.id == seeded["上海联影医疗器械有限公司"]
        ).one()
        assert ent.source == "poi", "命中已有企业——source 不被改写"
        assert ent.province == "上海市"
        sig = (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.type == "proposal")
            .one()
        )
        assert sig.title == "上海联影医疗器械有限公司污水处理站采购意向"
        assert sig.url == ""  # 意向详情链在前端路由内
        assert sig.base_score == 25.0
        assert sig.pub_date == "2026-11-21"
        assert sig.evidence_claim == "上海联影医疗器械有限公司污水处理站采购意向（采购意向）"
        assert sig.hit_part == "title"

    # 二跑幂等：信号零新增
    stats2 = jobs_mod.run_luban_scan()
    assert stats2["total_notices"] == 4
    assert stats2["total_signals"] == 0
    assert stats2["shanghai"] == {"notices": 2, "buyers": 0, "signals": 0}

    # 清理（防毒化本模块后续用例）
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.enterprise_id.in_(list(seeded.values()))).delete(
            synchronize_session=False
        )
        s.query(EnterpriseAlias).filter(
            EnterpriseAlias.enterprise_id.in_(list(seeded.values()))
        ).delete(synchronize_session=False)
        s.query(Enterprise).filter(Enterprise.id.in_(list(seeded.values()))).delete(
            synchronize_session=False
        )
        s.commit()


def test_luban_scan_province_failure_isolated(monkeypatch):
    """单省炸不串（2026-11-21 意向监控语义）：贵州 fetch 抛异常 → 贵州记
    error，沪桂照常——预置企业命中的才挂信号（buyers 恒 0 不建档）。"""
    from app import jobs as jobs_mod

    blocks = _fake_blocks_for("实验室改造")
    # 共享库残留清理 + 预置命中企业（新语义：命中已有企业才挂信号）
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.title.like("%实验室改造采购意向%")).delete(
            synchronize_session=False
        )
        s.commit()
        for nm, prov in [("上海联影医疗器械有限公司", "上海市"),
                         ("广西绿城真空装备有限公司", "广西壮族自治区")]:
            if not s.query(Enterprise).filter(Enterprise.name == nm).first():
                s.add(Enterprise(name=nm, source="poi", province=prov))
        s.commit()

    def boom_or_ok(prov_key, page_size=10, client=None):
        if prov_key == "guizhou":
            raise RuntimeError("guizhou upstream dead")
        return blocks.get(prov_key, [])

    monkeypatch.setattr(
        "app.collectors.luban.fetch_luban_home", boom_or_ok
    )

    stats = jobs_mod.run_luban_scan()  # 不应抛异常
    assert stats["guizhou"]["error"] is True
    assert stats["guizhou"]["signals"] == 0
    assert stats["shanghai"] == {"notices": 2, "buyers": 0, "signals": 1}
    assert stats["guangxi"] == {"notices": 1, "buyers": 0, "signals": 1}
    assert stats["total_notices"] == 3  # 只计健康省
    assert stats["total_buyers"] == 0
    assert stats["total_signals"] == 2

    with SessionLocal() as s:
        names = ["上海联影医疗器械有限公司", "广西绿城真空装备有限公司"]
        ents = s.query(Enterprise).filter(Enterprise.name.in_(names)).all()
        assert len(ents) == 2, "贵州炸档后沪桂信号不受影响"
        ids = [e.id for e in ents]
        assert s.query(Signal).filter(
            Signal.enterprise_id.in_(ids), Signal.type == "proposal"
        ).count() == 2, "两省各挂一条意向信号"
        s.query(Signal).filter(Signal.enterprise_id.in_(ids)).delete(
            synchronize_session=False
        )
        s.query(EnterpriseAlias).filter(
            EnterpriseAlias.enterprise_id.in_(ids)
        ).delete(synchronize_session=False)
        s.query(Enterprise).filter(Enterprise.id.in_(ids)).delete(
            synchronize_session=False
        )
        s.commit()


def test_luban_scan_disabled_per_province(monkeypatch):
    """逐省停用短路：停 guangxi → 该省 skipped_disabled 不打网络，沪黔照常。
    （2026-11-21 意向监控语义：预置命中企业才挂信号——buyers 恒 0。）"""
    from app import jobs as jobs_mod

    blocks = _fake_blocks_for("数据中心")
    called: list[str] = []

    def spy(prov_key, page_size=10, client=None):
        called.append(prov_key)
        return blocks.get(prov_key, [])

    monkeypatch.setattr("app.collectors.luban.fetch_luban_home", spy)
    with SessionLocal() as s:
        for nm in ["上海联影医疗器械有限公司", "贵州共赢机械设备有限公司"]:
            if not s.query(Enterprise).filter(Enterprise.name == nm).first():
                s.add(Enterprise(name=nm, source="poi"))
        s.commit()
        kv = s.get(SettingsKV, "source_disabled")
        if kv is None:
            s.add(SettingsKV(key="source_disabled", value='["guangxi"]'))
        else:
            kv.value = '["guangxi"]'
        s.commit()
    try:
        stats = jobs_mod.run_luban_scan()
        assert stats["guangxi"]["skipped_disabled"] is True
        assert stats["guangxi"]["notices"] == 0
        assert "guangxi" not in called, "停用省不应打网络"
        assert stats["shanghai"]["signals"] == 1
        assert stats["guizhou"]["signals"] == 1
        assert stats["total_signals"] == 2
    finally:
        with SessionLocal() as s:
            row = s.get(SettingsKV, "source_disabled")
            if row is not None:
                s.delete(row)
                s.commit()
            ids = [
                e.id for e in s.query(Enterprise)
                .filter(Enterprise.name.in_([
                    "上海联影医疗器械有限公司", "贵州共赢机械设备有限公司",
                ])).all()
            ]
            s.query(Signal).filter(Signal.enterprise_id.in_(ids)).delete(
                synchronize_session=False
            )
            s.query(EnterpriseAlias).filter(
                EnterpriseAlias.enterprise_id.in_(ids)
            ).delete(synchronize_session=False)
            s.query(Enterprise).filter(Enterprise.id.in_(ids)).delete(
                synchronize_session=False
            )
            s.commit()


# ---------------------------------------------------------------------------
# registry：三源注册完备性 + SLA 派生 + 仪表盘
# ---------------------------------------------------------------------------
def test_luban_registry_completeness():
    from app.sources import SOURCES, channel_variants, source_label

    expected = {
        "shanghai": ("上海政采", "shanghai_buyer", "上海市",
                     "https://www.zfcg.sh.gov.cn/"),
        "guizhou": ("贵州政采", "guizhou_buyer", "贵州省",
                    "http://www.ccgp-guizhou.gov.cn/"),
        "guangxi": ("广西政采", "guangxi_buyer", "广西壮族自治区",
                    "https://zfcg.gxzf.gov.cn/"),
    }
    for key, (label, alias, _prov, search_url) in expected.items():
        d = SOURCES.get(key)
        assert d is not None, f"{key} 必须注册进 SOURCES"
        assert d.label_cn == label and d.category == "tender"
        assert d.aliases == (alias,)
        # 三源共用单 job luban（单水位，SLA 表按 sla_job 分组共一条）
        assert d.sla_job == "luban" and d.sla_label == "政采云意向采集"
        assert d.sla_hours == 26  # 每日 06:10 一轮 → 26h 余量
        assert d.search_url == search_url
        assert channel_variants(key) == (key, alias)
        assert source_label(alias) == f"{label}买家"  # ALIAS_LABELS 独立展示名


def test_luban_freshness_sla_single_row():
    """三源 sla_job="luban" 派生只共一条 SLA（setdefault 首登记者生效）。"""
    from app.api.quality import _FRESHNESS_SLA

    assert _FRESHNESS_SLA["luban"] == {"label": "政采云意向采集", "hours": 26}


def test_luban_dashboard_row(client, admin_headers):
    """新鲜度仪表盘出现「政采云意向采集」；来源矩阵出现 xx_buyer 行
    （矩阵按实际落库行分组——三省沙粒各一行）。"""
    from app.jobs import mark_watermark

    with SessionLocal() as s:
        mark_watermark(s, "luban", "ok", {"total_notices": 4, "total_signals": 3})
        ent_ids = []
        for alias, prov in (
            ("shanghai_buyer", "上海市"),
            ("guizhou_buyer", "贵州省"),
            ("guangxi_buyer", "广西壮族自治区"),
        ):
            ent = Enterprise(name=f"LUBAN 矩阵沙粒企业-{alias}", source=alias,
                             province=prov)
            s.add(ent)
            s.flush()
            ent_ids.append(ent.id)
        s.commit()
    try:
        r = client.get("/api/quality/freshness", headers=admin_headers)
        assert r.status_code == 200
        row = next(x for x in r.json()["items"] if x["job"] == "luban")
        assert row["label"] == "政采云意向采集" and row["sla_hours"] == 26

        r2 = client.get("/api/data/overview", headers=admin_headers)
        assert r2.status_code == 200
        labels = {x["source"]: x["label"] for x in r2.json()["sources"]}
        assert labels.get("shanghai_buyer") == "上海政采买家"
        assert labels.get("guizhou_buyer") == "贵州政采买家"
        assert labels.get("guangxi_buyer") == "广西政采买家"
    finally:
        with SessionLocal() as s:
            s.query(Enterprise).filter(Enterprise.id.in_(ent_ids)).delete(
                synchronize_session=False
            )
            s.commit()
