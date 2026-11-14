"""SPEC-MULTI 回归：Source 注册表 / chinacheck 深解析 / 任务结果可观测 / 四省基座。

覆盖 SPEC-MULTI 验收门禁 3：
- registry 完备性（结构断言 + 真库实际 source 值全部注册）
- /api/sources 形状与 toggle 往返（SettingsKV source_disabled 生效/恢复）
- chinacheck 深解析（5 新字段 + prov_json 标记 + 只补空 + biz_dead 幂等）
- search_by_name 三路（命中/未命中/异常，MockTransport 零真网）
- GET /tasks/{id}/results（透出 + 缺键空数组 + 404）
- ggzy worker totals 携带 new_enterprises（沿用 test_api 的 monkeypatch 模式）
- recon 信号落库回归（intel.py:908 TypeError 被吞 bug 的实证反转）
- 来源停用 sweep 短路（jobs 函数入口 + enrich-gongshang）
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-multi-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import gongshang as gsh_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    EnterpriseAlias,
    SettingsKV,
    Signal,
    TaskCheckpoint,
)
from app.sources import SOURCES, channel_variants, source_label  # noqa: E402

init_db(seed=False)

_ISOLATED_MODELS = [Contact, Enterprise, Signal, EnterpriseAlias]


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
        # TaskCheckpoint 主键是文本 task_id（无 id 列）——按键集快照
        known_task_ids = {t for (t,) in s.query(TaskCheckpoint.task_id).all()}
    yield
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
        for t in {t for (t,) in s.query(TaskCheckpoint.task_id).all()} - known_task_ids:
            s.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == t).delete()
        s.query(SettingsKV).filter(SettingsKV.key == "source_disabled").delete()
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


def _mk_ent(session, name: str, source: str = "ggzy", **kw) -> Enterprise:
    ent = Enterprise(name=name, source=source, **kw)
    session.add(ent)
    session.commit()
    return ent


# ---------------------------------------------------------------------------
# S1 — 注册表完备性
# ---------------------------------------------------------------------------
def test_multi_registry_structure():
    """遍历 SOURCES：label/category 非空、aliases 不含主 key、变体可反查。"""
    assert len(SOURCES) >= 11, "SPEC 表格 11 来源必须全量注册"
    for key, d in SOURCES.items():
        assert d.label_cn and d.label_cn != key, f"{key} 缺中文标签"
        assert d.category in ("tender", "proposal", "poi", "import", "enrich")
        assert key not in d.aliases, f"{key} 的 aliases 不应含主 key"
        assert d.key == key
        variants = channel_variants(key)
        assert variants[0] == key, "通道元组主 key 必须在首位"


def test_multi_registry_label_fallback():
    """未注册值回退 key 本身（现状行为）；别名有独立展示名。"""
    assert source_label("ggzy") == "公共资源交易"
    assert source_label("ggzy_buyer") == "公采买家"
    assert source_label("ccgp_buyer") == "政采买家"
    assert source_label("poi") == "高德 POI 采集"
    assert source_label("从没见过的源") == "从没见过的源"


def test_multi_registry_covers_real_db_sources():
    """真库（只读）实际 source 值必须全部注册——S1 地基红线。"""
    real_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "vaclead.db",
    )
    if not os.path.exists(real_db):
        pytest.skip("真库不存在（干净测试环境）")
    con = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT source FROM enterprise").fetchall()
    finally:
        con.close()
    if not rows:
        pytest.skip("真库为空")
    known = set(SOURCES) | {a for d in SOURCES.values() for a in d.aliases}
    unknown = {str(r[0]) for r in rows if r[0] and str(r[0]) not in known}
    assert not unknown, f"真库存在未注册 source 值：{unknown}"


# ---------------------------------------------------------------------------
# S1 — /api/sources 形状 + toggle 往返
# ---------------------------------------------------------------------------
def test_multi_sources_endpoint_shape(client, admin_headers):
    """字段齐全 / key 唯一 / enterprise_count 与库内（含别名归并）一致。"""
    with SessionLocal() as s:
        _mk_ent(s, "MULTI来源甲真空设备有限公司", source="ggzy")
        _mk_ent(s, "MULTI来源乙机械有限公司", source="ggzy_buyer")
        expected_ggzy = (
            s.query(Enterprise)
            .filter(Enterprise.source.in_(("ggzy", "ggzy_buyer")))
            .count()
        )

    r = client.get("/api/sources", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["sources"], list) and body["sources"]
    keys = []
    for item in body["sources"]:
        assert {"key", "label", "category", "enterprise_count", "last_harvest", "enabled"} == set(item)
        assert isinstance(item["enterprise_count"], int)
        assert isinstance(item["enabled"], bool)
        keys.append(item["key"])
    assert len(keys) == len(set(keys)) and set(keys) == set(SOURCES)

    ggzy_row = next(x for x in body["sources"] if x["key"] == "ggzy")
    assert ggzy_row["label"] == "公共资源交易"
    assert ggzy_row["category"] == "tender"
    assert ggzy_row["enterprise_count"] == expected_ggzy, "别名 ggzy_buyer 应归并进主 key"
    assert ggzy_row["last_harvest"], "有企业入库的源应有 last_harvest"
    assert ggzy_row["enabled"] is True

    # 未认证拒绝
    assert client.get("/api/sources").status_code == 401


def test_multi_sources_toggle_roundtrip(client, admin_headers, sales_headers):
    """toggle 停用 → disabled 落 SettingsKV 且端点可见；再 toggle 恢复。"""
    r = client.post("/api/sources/ggzy/toggle", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "ggzy", "enabled": False, "disabled": ["ggzy"]}

    with SessionLocal() as s:
        row = s.get(SettingsKV, "source_disabled")
        assert row is not None and json.loads(row.value) == ["ggzy"]

    body = client.get("/api/sources", headers=admin_headers).json()
    assert next(x for x in body["sources"] if x["key"] == "ggzy")["enabled"] is False

    # 停用态对 is_source_disabled 的传导（主 key 与别名同停）
    from app.sources import any_source_disabled, is_source_disabled

    with SessionLocal() as s:
        assert is_source_disabled("ggzy", s) is True
        assert is_source_disabled("ggzy_buyer", s) is True
        assert is_source_disabled("ccgp", s) is False

    # 恢复
    r2 = client.post("/api/sources/ggzy/toggle", headers=admin_headers)
    assert r2.status_code == 200 and r2.json()["enabled"] is True
    with SessionLocal() as s:
        row = s.get(SettingsKV, "source_disabled")
        assert row is None or json.loads(row.value) == []
    assert any_source_disabled("ggzy") is False

    # 权限与存在性
    assert client.post("/api/sources/ccgp/toggle", headers=sales_headers).status_code == 403
    assert client.post("/api/sources/no-such-key/toggle", headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------------
# S2 — chinacheck 深解析 + 只补空 + biz_dead
# ---------------------------------------------------------------------------
_SNAP_FULL = {
    "registeredCapital": "5000万人民币",
    "businessScope": "真空设备制造",
    "industry": "通用设备制造业",
    "estDate": "2015-03-01",
    "registrationStatus": "注销",
    "legalRepresentative": "王深",
    "staffSize": "100-499人",
    "paidInCapital": "1200万人民币",
    "registeredAddress": "苏州市工业园区测试路8号",
    "formerNames": ["苏州旧名机械有限公司", ""],
}


def _rpc(snap: dict) -> dict:
    """构造 china-check MCP JSON-RPC 响应壳。"""
    return {"result": {"content": [{"text": json.dumps(snap, ensure_ascii=False)}]}}


def test_multi_parse_chinacheck_deep_fields():
    """快照 → 5 新字段全解析；原 4 字段行为不变。"""
    out = gsh_mod._parse_chinacheck(_rpc(_SNAP_FULL))
    assert out is not None
    # 原 4 字段（回归保护）
    assert out["registered_capital_wan"] == 5000.0
    assert out["business_scope"] == "真空设备制造"
    assert out["industry"] == "通用设备制造业"
    assert out["est_date"] == "2015-03-01"
    # 5 新字段（SPEC-MULTI S2）
    assert out["biz_status"] == "注销"
    assert out["legal_rep"] == "王深"
    assert out["staff_size"] == "100-499人"
    assert out["paid_in_capital_wan"] == 1200.0
    assert out["address"] == "苏州市工业园区测试路8号"
    assert out["former_names"] == ["苏州旧名机械有限公司"]
    # 坏壳/空壳
    assert gsh_mod._parse_chinacheck({"result": {"content": []}}) is None
    assert gsh_mod._parse_chinacheck(_rpc({})) is None


def test_multi_apply_gongshang_fields_fill_only_and_prov():
    """只补空不覆盖 + 每字段 PROV 打标（source=工商补数·chinacheck, by=enrich）。"""
    with SessionLocal() as s:
        ent = Enterprise(
            name="MULTI补全甲真空设备有限公司", source="csv",
            legal_rep="已有法人", registered_capital_wan=999.0,
            business_scope="已有范围",
        )
        s.add(ent)
        s.commit()
        before_intent = ent.intent_score

        filled = gsh_mod.apply_gongshang_fields(ent, gsh_mod._parse_chinacheck(_rpc(_SNAP_FULL)))
        s.commit()

        assert "legal_rep" not in filled, "已有法人不得覆盖"
        assert "registered_capital_wan" not in filled, "已有注册资本不得覆盖"
        assert "business_scope" not in filled, "已有经营范围不得覆盖"
        assert {"established_at", "biz_status", "staff_size",
                "paid_in_capital_wan", "address", "former_names",
                "industry_name"} <= set(filled)
        assert ent.established_at == "2015-03-01"
        assert ent.registered_capital_wan == 999.0 and ent.legal_rep == "已有法人"
        assert ent.paid_in_capital_wan == 1200.0 and ent.staff_size == "100-499人"
        assert ent.biz_status == "注销" and ent.biz_status_at
        assert ent.former_names == '["苏州旧名机械有限公司"]'
        prov = json.loads(ent.prov_json)
        for f in ("biz_status", "established_at", "paid_in_capital_wan", "former_names"):
            assert prov[f]["source"] == "工商补数·chinacheck"
            assert prov[f]["by"] == "enrich"
        # biz_status 死刑态 → biz_dead 打标 + intent_score 不动
        assert "biz_dead" in json.loads(ent.tags)
        assert ent.intent_score == before_intent

        # 幂等：二次写回零覆盖、标签不重复
        filled2 = gsh_mod.apply_gongshang_fields(ent, gsh_mod._parse_chinacheck(_rpc(_SNAP_FULL)))
        assert filled2 == []
        assert json.loads(ent.tags).count("biz_dead") == 1
        eid = ent.id
    with SessionLocal() as s:
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()


@pytest.mark.parametrize("status", ["注销", "吊销", "迁出", "清算"])
def test_multi_biz_dead_states(status):
    """死刑四态全打 biz_dead；存续态不打。"""
    with SessionLocal() as s:
        ent = Enterprise(name=f"MULTI死亡态{status}有限公司", source="csv")
        s.add(ent)
        s.commit()
        gsh_mod.apply_gongshang_fields(ent, {"biz_status": status})
        assert ("biz_dead" in json.loads(ent.tags)) is (status in gsh_mod.BIZ_DEAD_STATES)
        eid = ent.id
    with SessionLocal() as s:
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()


def test_multi_biz_status_exposed_in_api(client, admin_headers):
    """企业列表/详情透出 biz_status 等新字段。"""
    with SessionLocal() as s:
        ent = _mk_ent(s, "MULTI透出测试设备有限公司", source="csv",
                      biz_status="吊销", biz_status_at="2026-11-14T00:00:00",
                      staff_size="50-99人", paid_in_capital_wan=88.0,
                      former_names='["旧名一"]')
        eid = ent.id
    r = client.get("/api/enterprises", params={"q": "MULTI透出测试"}, headers=admin_headers)
    item = next(x for x in r.json()["items"] if x["id"] == eid)
    assert item["biz_status"] == "吊销"
    detail = client.get(f"/api/enterprises/{eid}", headers=admin_headers).json()
    assert detail["biz_status"] == "吊销" and detail["biz_status_at"] == "2026-11-14T00:00:00"
    assert detail["staff_size"] == "50-99人" and detail["paid_in_capital_wan"] == 88.0
    assert detail["former_names"] == ["旧名一"]
    with SessionLocal() as s:
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == eid).delete()
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()


# ---------------------------------------------------------------------------
# S2 — search_by_name 三路（MockTransport，零真网）
# ---------------------------------------------------------------------------
def _provider_with(transport) -> gsh_mod.ChinaCheckProvider:
    return gsh_mod.ChinaCheckProvider("tok-test", transport=transport)


def test_multi_search_by_name_hit():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["params"]["name"] == "search_chinese_company"
        assert payload["params"]["arguments"] == {"name": "按名命中真空设备有限公司"}
        return httpx.Response(200, json=_rpc({"list": [
            {"creditCode": "91320000MA1MULTI01", "name": "按名命中真空设备有限公司",
             "legalRepresentative": "李按"},
        ]}))

    out = _provider_with(httpx.MockTransport(handler)).search_by_name("按名命中真空设备有限公司")
    assert out == {"uscc": "91320000MA1MULTI01", "name": "按名命中真空设备有限公司", "legal_rep": "李按"}


def test_multi_search_by_name_miss():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_rpc({"list": []}))

    p = _provider_with(httpx.MockTransport(handler))
    assert p.search_by_name("查无此公司") is None
    assert p.search_by_name("") is None


def test_multi_search_by_name_error_returns_none():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    assert _provider_with(httpx.MockTransport(handler)).search_by_name("任意名") is None


# ---------------------------------------------------------------------------
# S3 — GET /tasks/{task_id}/results
# ---------------------------------------------------------------------------
def test_multi_task_results_endpoint(client, admin_headers, sales_headers):
    """透出 new_enterprises/failures/totals；缺键空数组；404 语义同状态端点。"""
    tid = "multi-results-001"
    with SessionLocal() as s:
        s.add(TaskCheckpoint(
            task_id=tid, kind="ggzy", status="done", progress=100,
            state_json=json.dumps({
                "entities": 3, "new_entities": 2,
                "new_enterprises": [
                    {"id": 11, "name": "结果页甲公司", "province": "山东省",
                     "industry_name": "检验检测"},
                ],
                "failures": [
                    {"enterprise_id": 22, "name": "结果页乙公司",
                     "stage": "C:gongshang", "error": "超时"[:200]},
                ],
            }, ensure_ascii=False),
        ))
        s.commit()

    r = client.get(f"/api/tasks/{tid}/results", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == tid and body["kind"] == "ggzy" and body["status"] == "done"
    assert body["new_enterprises"][0]["name"] == "结果页甲公司"
    assert body["failures"][0]["stage"] == "C:gongshang"
    assert body["totals"]["new_entities"] == 2

    # 缺键 → 空数组（兼容旧任务）
    tid2 = "multi-results-002"
    with SessionLocal() as s:
        s.add(TaskCheckpoint(task_id=tid2, kind="poi", status="done",
                             state_json='{"inserted": 5}'))
        s.commit()
    body2 = client.get(f"/api/tasks/{tid2}/results", headers=admin_headers).json()
    assert body2["new_enterprises"] == [] and body2["failures"] == []
    assert body2["totals"] == {"inserted": 5}

    # sales 可查（与状态端点同鉴权）；不存在 404
    assert client.get(f"/api/tasks/{tid}/results", headers=sales_headers).status_code == 200
    assert client.get("/api/tasks/definitely-not-exist/results", headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------------
# S3 — ggzy worker totals 携带 new_enterprises（沿用 test_api monkeypatch 模式）
# ---------------------------------------------------------------------------
def _poll_task(task_id: str, timeout: float = 15.0) -> str:
    import time as _time

    deadline = _time.time() + timeout
    status = "running"
    while _time.time() < deadline:
        with SessionLocal() as s:
            cp = s.get(TaskCheckpoint, task_id)
            status = cp.status if cp else "missing"
        if status != "running":
            return status
        _time.sleep(0.2)
    return status


def test_multi_ggzy_harvest_new_enterprises(client, admin_headers, monkeypatch):
    """采集任务 totals/state_json 带 new_enterprises（中标方+采购人，cap 50）。"""
    from app.collectors import ggzy as ggzy_mod

    monkeypatch.setattr(ggzy_mod, "REQUEST_INTERVAL_S", 0.0)

    def fake_search_all_entities(keyword, max_pages=20, size=200, client=None):
        if keyword == "真空镀膜机":
            return [
                {"uniscid": "MULTIUSCC0001", "entname": "MULTI中标方真空镀膜机有限公司",
                 "name": "张三", "bidCount": 1},
            ]
        return []

    def fake_list_bids(uniscid, page=1, size=50, client=None):
        return {"records": [{
            "id": "MULTIDEAL1", "projectName": "某研究院真空镀膜机采购项目成交公告",
            "bidPrice": "320.5", "areaCode": "苏州", "tos": "政府采购",
            "tenderOrgName": "MULTI某材料研究院", "createTime": "2026-10-01 09:00:00",
        }], "total": 1, "pages": 1}

    monkeypatch.setattr(ggzy_mod, "search_all_entities", fake_search_all_entities)
    monkeypatch.setattr(ggzy_mod, "list_bids", fake_list_bids)

    r = client.post("/api/tasks/ggzy-harvest",
                    json={"keywords": ["真空镀膜机"], "max_pages": 1},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    assert _poll_task(task_id) == "done"

    with SessionLocal() as s:
        cp = s.get(TaskCheckpoint, task_id)
        state = json.loads(cp.state_json or "{}")
    names = [x["name"] for x in state.get("new_enterprises", [])]
    assert "MULTI中标方真空镀膜机有限公司" in names, "中标方应在新面孔清单"
    assert "MULTI某材料研究院" in names, "采购人应在新面孔清单"
    for item in state["new_enterprises"]:
        assert {"id", "name", "province", "industry_name"} <= set(item)

    # results 端点透出同一份清单
    body = client.get(f"/api/tasks/{task_id}/results", headers=admin_headers).json()
    assert len(body["new_enterprises"]) == len(state["new_enterprises"])
    assert body["totals"]["new_entities"] >= 1


# ---------------------------------------------------------------------------
# S3 — recon 信号落库回归（intel.py Signal(source=) TypeError 被吞 bug 反转）
# ---------------------------------------------------------------------------
def test_multi_recon_signals_persist(client, admin_headers, monkeypatch):
    """侦察产出必须落 Signal 表（修复前 TypeError 被 except 吞掉 → 0 信号）。"""
    from app.collectors import ccgp as ccgp_mod

    with SessionLocal() as s:
        ent = _mk_ent(s, "MULTI侦察目标检测设备有限公司", source="csv")
        eid = ent.id

    def fake_recon(name, days=365, max_pages=3):
        assert name == "MULTI侦察目标检测设备有限公司"
        return [{
            "title": "MULTI侦察目标检测设备有限公司中标公告",
            "url": "https://example.ccgp/recon-multi-1",
            "notice_type": "中标公告",
            "pub_date": "2026-11-01",
            "summary": "该项目由MULTI侦察目标检测设备有限公司中标成交。",
            "buyer": "MULTI某采购中心",
        }]

    monkeypatch.setattr(ccgp_mod, "search_ccgp_all", fake_recon)
    monkeypatch.setattr("time.sleep", lambda _s: None)  # 跳过礼貌间隔

    r = client.post(f"/api/enterprises/{eid}/recon",
                    json={"days": 365, "max_pages": 1}, headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == [], f"侦察不应报错：{body['errors']}"
    assert body["new_signals"] == 1, "修复前 TypeError 被吞 → new_signals 恒 0"

    with SessionLocal() as s:
        sig = s.query(Signal).filter(
            Signal.enterprise_id == eid, Signal.url == "https://example.ccgp/recon-multi-1"
        ).one()
        assert sig.type == "tender_win"
    # 清理（别名/联系人级联自查：TestClient 库为本模块独享，模块隔离 fixture 兜底）


# ---------------------------------------------------------------------------
# S1 — 来源停用 sweep 短路
# ---------------------------------------------------------------------------
def _set_disabled(session, keys: list[str]) -> None:
    row = session.get(SettingsKV, "source_disabled")
    if row is None:
        row = SettingsKV(key="source_disabled", value="")
        session.add(row)
    row.value = json.dumps(keys)
    session.commit()


def test_multi_disabled_sweep_short_circuit(client, admin_headers, monkeypatch):
    """停用的源：jobs sweep 函数入口短路 + enrich-gongshang 同理。"""
    from app import jobs as jobs_mod

    with SessionLocal() as s:
        _set_disabled(s, ["proposal_gd", "ccgp", "gongshang"])

    # 四省 sweep：停用短路（不触网——fetch 若被调用会炸出可数签名错误）
    def _boom(*a, **kw):  # pragma: no cover — 被调用即失败
        raise AssertionError("停用源不应发起抓取")

    monkeypatch.setattr(jobs_mod, "run_subscription_rules", lambda session: {"matched": 0})
    assert jobs_mod.run_proposal_gd_scan(max_pages=1) == {"skipped_disabled": True}

    stats_hourly = jobs_mod.run_hourly_tender_scan()
    assert stats_hourly == {"skipped_disabled": True}

    # 未停用的省照常走采集路径（fetch 打桩验证短路只挡停用源）
    from app.collectors import proposal_hb as ph

    monkeypatch.setattr(ph, "fetch_hb_filing_page",
                        lambda page_no=1, page_size=20, client=None: [])
    stats_hb = jobs_mod.run_proposal_hb_scan(max_pages=1)
    assert "skipped_disabled" not in stats_hb and stats_hb["records"] == 0

    # enrich-gongshang 端点同样短路
    r = client.post("/api/tasks/enrich-gongshang", json={"limit": 10}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"task_id": None, "skipped_disabled": True}

    # 清理停用态（后续用例不受影响；恢复后不做真网扫描验证）
    with SessionLocal() as s:
        _set_disabled(s, [])
    from app.sources import any_source_disabled

    assert any_source_disabled("ccgp") is False
