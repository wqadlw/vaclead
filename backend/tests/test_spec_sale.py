"""SPEC-SALE 回归：话术卡系统（S1）+ ggzy_buyer 省份落库（S2）+ 导出联系人列修复（S4）。

覆盖面（docs/SPEC-SALE.md）：
- S1 script_card：CRUD 往返（admin 建改删 + sales 只读 + 未登录 401）+ seed 幂等
  （跑两次数量不变）+ scene 枚举 422 + prep 场景映射（new/won/lost/proposal/
  竞对中标优先级/role_hint 优先/generic 兜底）
- S2 ggzy_buyer 省份：harvest 建档 buyer.province=areaCode（只补空不覆盖，
  bidder 不落）+ daily_incremental 同款段 + 回填任务补齐与幂等
- S4 导出：联系人列改读 phones_json + email 拼接（title 槽位旧号码不再出现）
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-sale-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import time as _time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.api.script_cards import SEED_MARKER, seed_script_cards  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    EnterpriseAlias,
    Followup,
    Lead,
    ScriptCard,
    Signal,
    SyncWatermark,
    TaskCheckpoint,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Lead, Signal, ScriptCard, EnterpriseAlias]


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
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wait_task(task_id: str, timeout: float = 20.0) -> str:
    deadline = _time.time() + timeout
    status = "running"
    while _time.time() < deadline:
        with SessionLocal() as s:
            cp = s.get(TaskCheckpoint, task_id)
            status = cp.status if cp is not None else "missing"
        if status != "running":
            break
        _time.sleep(0.2)
    return status


# ---------------------------------------------------------------------------
# S1 话术卡
# ---------------------------------------------------------------------------


def test_seed_script_cards_idempotent():
    """种子幂等（SPEC-SALE S1）：首种 5 张覆盖全场景；跑两次数量不变。"""
    from app.api.script_cards import SCENES

    with SessionLocal() as s:
        # 清场（标记+卡全删），验证"首种"语义
        s.query(ScriptCard).delete()
        wm = s.get(SyncWatermark, SEED_MARKER)
        if wm is not None:
            s.delete(wm)
        s.commit()

        n1 = seed_script_cards(s)
        assert n1 == 5
        cards = s.query(ScriptCard).all()
        assert len(cards) == 5
        assert {c.scene for c in cards} == set(SCENES)
        titles = {c.title for c in cards}
        assert "首联·官方授权开场" in titles
        assert "确定性收尾" in titles
        assert all("@" not in c.body_md for c in cards)  # 占位符误粘断言兜底

        # 第二遍：DB 标记命中 → 零写入（跑两次数量不变）
        n2 = seed_script_cards(s)
        assert n2 == 0
        assert s.query(ScriptCard).count() == 5

        # 标记丢失但卡还在 → 按标题查重不重复插（admin 建卡先于首种的边界）
        s.delete(s.get(SyncWatermark, SEED_MARKER))
        s.commit()
        n3 = seed_script_cards(s)
        assert n3 == 0
        assert s.query(ScriptCard).count() == 5


def test_script_card_crud_and_permissions(client, admin_headers, sales_headers):
    """CRUD 往返：admin 建改删 + sales 只读 GET + 未登录 401 + scene 枚举 422。"""
    # 未登录
    assert client.get("/api/script-cards").status_code == 401
    assert client.post(
        "/api/script-cards", json={"title": "x", "scene": "generic"}
    ).status_code == 401
    # sales 只读放行、写拒绝
    r = client.get("/api/script-cards", headers=sales_headers)
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)
    assert client.post(
        "/api/script-cards", json={"title": "销售无权建卡", "scene": "generic"},
        headers=sales_headers,
    ).status_code == 403

    # admin 建
    r = client.post(
        "/api/script-cards",
        json={"title": "SALE CRUD 测试卡", "scene": "repeat_purchase",
              "role_hint": "oem", "body_md": "话术正文", "sort": 7},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # 读回 + 过滤
    items = client.get(
        "/api/script-cards?scene=repeat_purchase", headers=admin_headers
    ).json()["items"]
    assert any(i["id"] == cid and i["sort"] == 7 for i in items)
    # role 过滤：命中该角色或通用卡；本卡 role_hint=oem 不应出现在 role=end_user
    items_oem = client.get("/api/script-cards?role=oem", headers=admin_headers).json()["items"]
    assert any(i["id"] == cid for i in items_oem)
    items_eu = client.get(
        "/api/script-cards?role=end_user", headers=admin_headers
    ).json()["items"]
    assert all(i["id"] != cid for i in items_eu)

    # 枚举校验 422
    assert client.post(
        "/api/script-cards", json={"title": "坏场景", "scene": "bogus"},
        headers=admin_headers,
    ).status_code == 422
    assert client.post(
        "/api/script-cards", json={"title": "坏角色", "scene": "generic",
                                   "role_hint": "boss"},
        headers=admin_headers,
    ).status_code == 422
    assert client.post(
        "/api/script-cards", json={"scene": "generic"},  # 缺 title
        headers=admin_headers,
    ).status_code == 422

    # 改
    r = client.put(
        f"/api/script-cards/{cid}",
        json={"title": "SALE CRUD 测试卡（改）", "sort": 8, "enabled": 0},
        headers=admin_headers,
    )
    assert r.status_code == 200
    with SessionLocal() as s:
        c = s.get(ScriptCard, cid)
        assert c.title == "SALE CRUD 测试卡（改）" and c.sort == 8
        assert c.enabled == 0 and c.scene == "repeat_purchase"
    assert client.put(
        f"/api/script-cards/{cid}", json={"scene": "bogus"}, headers=admin_headers
    ).status_code == 422
    assert client.put("/api/script-cards/999999", json={"sort": 1},
                      headers=admin_headers).status_code == 404

    # 删 + 404
    assert client.delete(f"/api/script-cards/{cid}", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/script-cards/{cid}", headers=admin_headers).status_code == 404
    items = client.get("/api/script-cards", headers=admin_headers).json()["items"]
    assert all(i["id"] != cid for i in items)


def _mk_ent_contact(s, name: str, **ent_kw) -> tuple[int, int]:
    e = Enterprise(name=name, source="csv", **ent_kw)
    s.add(e)
    s.flush()
    c = Contact(enterprise_id=e.id, name=f"{name}联系人", role="buyer_contact",
                phones_json='["13800000999"]')
    s.add(c)
    s.flush()
    return e.id, c.id


def test_prep_scene_mapping(client, admin_headers):
    """prep 接线（SPEC-SALE S1）：lead 状态 → 场景映射 + generic 兜底。"""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    with SessionLocal() as s:
        e1, c1 = _mk_ent_contact(s, "SALE新户机械有限公司")
        s.add(Lead(enterprise_id=e1, status="new"))
        e2, c2 = _mk_ent_contact(s, "SALE老户食品厂有限公司")
        s.add(Lead(enterprise_id=e2, status="won"))
        e3, c3 = _mk_ent_contact(s, "SALE输单机械有限公司")
        s.add(Lead(enterprise_id=e3, status="lost", lost_reason="competitor"))
        e4, c4 = _mk_ent_contact(s, "SALE拟建化工有限公司")
        lead4 = Lead(enterprise_id=e4, status="following")
        s.add(lead4)
        e7, c7 = _mk_ent_contact(s, "SALE跟进中机械有限公司")
        lead7 = Lead(enterprise_id=e7, status="following")
        s.add(lead7)
        s.flush()
        s.add(Signal(enterprise_id=e4, type="proposal", title="SALE拟建备案公示",
                     url="https://example.com/sale-p1", pub_date=today,
                     evidence_claim="该单位完成扩建项目备案"))
        s.add(Followup(lead_id=lead7.id, content="SALE跟进记录：已首联"))
        s.commit()
        ids = {"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c7": c7}

    # new → first_contact；尾附 generic 兜底
    d = client.get(f"/api/contacts/{ids['c1']}/prep", headers=admin_headers).json()
    assert d["script_cards"][0]["scene"] == "first_contact"
    assert d["script_cards"][-1]["scene"] == "generic"

    # won → repeat_purchase
    d = client.get(f"/api/contacts/{ids['c2']}/prep", headers=admin_headers).json()
    assert d["script_cards"][0]["scene"] == "repeat_purchase"

    # lost_reason=competitor → competitor_replace（优先级最高，压过 first_contact）
    d = client.get(f"/api/contacts/{ids['c3']}/prep", headers=admin_headers).json()
    assert d["script_cards"][0]["scene"] == "competitor_replace"

    # 90 天内 proposal → proposal_window（压过 first_contact）
    d = client.get(f"/api/contacts/{ids['c4']}/prep", headers=admin_headers).json()
    assert d["script_cards"][0]["scene"] == "proposal_window"

    # 跟进中且有跟进记录、无紧急信号 → 无主场景，仅 generic 兜底
    d = client.get(f"/api/contacts/{ids['c7']}/prep", headers=admin_headers).json()
    assert [c["scene"] for c in d["script_cards"]] == ["generic"]


def test_prep_priority_competitor_beats_won(client, admin_headers):
    """优先级（SPEC-SALE S1）：won 老户 + 30 天内竞对中标 → competitor_replace 最紧急。"""
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    stale = (datetime.now(timezone.utc).date() - timedelta(days=60)).isoformat()
    with SessionLocal() as s:
        comp = Enterprise(name="SALE竞对真空设备有限公司", source="ggzy",
                          is_competitor=1)
        s.add(comp)
        s.flush()
        s.add(Signal(enterprise_id=comp.id, type="tender_win",
                     title="SALE竞对中标项目P1", url="https://example.com/sale-w1",
                     pub_date=today, evidence_claim="竞对中标"))
        e, c = _mk_ent_contact(s, "SALE被抢户机械有限公司")
        s.add(Lead(enterprise_id=e, status="won"))
        s.add(Signal(enterprise_id=e, type="tender_buy",
                     title="SALE竞对中标项目P1", url="https://example.com/sale-w1",
                     pub_date=today, evidence_claim="该单位公开采购，由竞对中标"))
        # 60 天前的旧竞对中标：不在 30 天窗口，不触发
        e_old, c_old = _mk_ent_contact(s, "SALE旧战况机械有限公司")
        s.add(Lead(enterprise_id=e_old, status="won"))
        s.add(Signal(enterprise_id=e_old, type="tender_buy",
                     title="SALE旧竞对中标项目P2", url="https://example.com/sale-w2",
                     pub_date=stale, evidence_claim="该单位公开采购，由竞对中标"))
        s.commit()
        ids = {"hot": c, "cold": c_old}

    d = client.get(f"/api/contacts/{ids['hot']}/prep", headers=admin_headers).json()
    assert d["script_cards"][0]["scene"] == "competitor_replace"
    # 30 天窗口外不触发 → 回落 repeat_purchase（won 老户）
    d = client.get(f"/api/contacts/{ids['cold']}/prep", headers=admin_headers).json()
    assert d["script_cards"][0]["scene"] == "repeat_purchase"


def test_prep_role_hint_priority(client, admin_headers):
    """role_hint 优先（SPEC-SALE S1）：与企业 customer_role 相等的卡排最前。"""
    with SessionLocal() as s:
        e, c = _mk_ent_contact(s, "SALE终端用户机械有限公司", customer_role="end_user")
        s.add(Lead(enterprise_id=e, status="new"))
        s.commit()
        # 两张测试卡：匹配角色（sort 大）与不匹配角色（sort 小）——匹配优先于 sort
        mk = client.post(
            "/api/script-cards",
            json={"title": "SALE角色优先卡-enduser", "scene": "first_contact",
                  "role_hint": "end_user", "body_md": "对终端用户说", "sort": 50},
            headers=admin_headers,
        )
        assert mk.status_code == 201
        mk2 = client.post(
            "/api/script-cards",
            json={"title": "SALE角色错配卡-oem", "scene": "first_contact",
                  "role_hint": "oem", "body_md": "对 OEM 说", "sort": 1},
            headers=admin_headers,
        )
        mid, mid2 = mk.json()["id"], mk2.json()["id"]

    d = client.get(f"/api/contacts/{c}/prep", headers=admin_headers).json()
    scenes_titles = [c2["title"] for c2 in d["script_cards"]]
    assert scenes_titles[0] == "SALE角色优先卡-enduser"
    # 错配卡仍在列表但排尾（压不过空 role_hint 的种子通用首联卡）
    assert "SALE角色错配卡-oem" in scenes_titles
    assert scenes_titles.index("SALE角色优先卡-enduser") < scenes_titles.index(
        "SALE角色错配卡-oem"
    )
    # 清理测试卡
    client.delete(f"/api/script-cards/{mid}", headers=admin_headers)
    client.delete(f"/api/script-cards/{mid2}", headers=admin_headers)


# ---------------------------------------------------------------------------
# S2 ggzy_buyer 省份落库
# ---------------------------------------------------------------------------


def test_ggzy_harvest_buyer_province(client, admin_headers, monkeypatch):
    """harvest 建 buyer 帧时 province=areaCode（只补空不覆盖）；bidder 不落。"""
    from app.collectors import ggzy as ggzy_mod

    monkeypatch.setattr(ggzy_mod, "REQUEST_INTERVAL_S", 0.0)

    def fake_search_all_entities(keyword, max_pages=20, size=200, client=None):
        if keyword == "真空镀膜机":
            return [{"uniscid": "91370000SALEHPX1", "entname": "SALE州达真空镀膜机有限公司",
                     "name": "王五", "bidCount": 2}]
        return []

    def fake_list_bids(uniscid, page=1, size=50, client=None):
        return {"records": [
            {"id": "SALEDEAL1", "projectName": "SALE真空镀膜机采购项目甲成交公告",
             "bidPrice": "320.5", "areaCode": "山东省", "tos": "政府采购",
             "tenderOrgName": "SALE鲁材料研究院", "createTime": "2026-10-01 09:00:00"},
            {"id": "SALEDEAL2", "projectName": "SALE真空镀膜机采购项目乙成交公告",
             "bidPrice": "120", "areaCode": "广东省", "tos": "政府采购",
             "tenderOrgName": "SALE豫预存研究院", "createTime": "2026-10-02 09:00:00"},
        ], "total": 2, "pages": 1}

    monkeypatch.setattr(ggzy_mod, "search_all_entities", fake_search_all_entities)
    monkeypatch.setattr(ggzy_mod, "list_bids", fake_list_bids)

    # 预存一家已有省份的 buyer（"只补空不覆盖"：广东 area 不得覆盖江苏）
    with SessionLocal() as s:
        s.add(Enterprise(name="SALE豫预存研究院", source="ggzy_buyer", province="江苏省"))
        s.commit()

    r = client.post("/api/tasks/ggzy-harvest",
                    json={"keywords": ["真空镀膜机"], "max_pages": 1},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    assert _wait_task(task_id) == "done"

    with SessionLocal() as s:
        buyer1 = s.query(Enterprise).filter(
            Enterprise.name == "SALE鲁材料研究院").one()
        assert buyer1.source == "ggzy_buyer"
        assert buyer1.province == "山东省"  # 实锤 areaCode=省名 → 落库
        buyer2 = s.query(Enterprise).filter(
            Enterprise.name == "SALE豫预存研究院").one()
        assert buyer2.province == "江苏省"  # 只补空，已有省份不覆盖
        bidder = s.query(Enterprise).filter(
            Enterprise.uscc == "91370000SALEHPX1").one()
        assert bidder.source == "ggzy"
        assert not (bidder.province or "")  # 中标方不落：area 是项目地非注册地，不发明
        assert s.query(Signal).filter(
            Signal.enterprise_id == buyer1.id, Signal.type == "tender_buy").count() == 1

    # 本任务 checkpoint 清理（字符串主键，模块快照法不适用）
    with SessionLocal() as s:
        s.query(TaskCheckpoint).filter(TaskCheckpoint.task_id == task_id).delete()
        s.commit()


def test_daily_incremental_buyer_province(monkeypatch):
    """daily_incremental ggzy 段同款语义：建 buyer（ggzy_buyer）帧 + province=area。"""
    from app.collectors import ccgp as ccgp_mod
    from app.collectors import ggzy as ggzy_mod
    from app.jobs import run_daily_incremental

    rec = {"name": "SALE增量测试真空设备有限公司", "uscc": "91370000SALEDIX1",
           "bid_count": 1}
    monkeypatch.setattr(ggzy_mod, "search_all_entities", lambda *a, **k: ["x"])
    monkeypatch.setattr(ggzy_mod, "normalize_entity_record", lambda raw: rec)
    monkeypatch.setattr(ggzy_mod, "list_bids", lambda *a, **k: {"records": [
        {"projectName": "SALE增量中标项目成交公告", "bidPrice": "88",
         "areaCode": "山东省", "tos": "政府采购",
         "tenderOrgName": "SALE增量采购研究院", "createTime": "2026-10-03 08:00:00"},
    ], "total": 1, "pages": 1})
    monkeypatch.setattr(ccgp_mod, "search_ccgp_all", lambda *a, **k: [])

    stats = run_daily_incremental()
    assert stats.get("ggzy_buyers", 0) >= 1

    with SessionLocal() as s:
        buyer = s.query(Enterprise).filter(
            Enterprise.name == "SALE增量采购研究院").one()
        assert buyer.source == "ggzy_buyer"
        assert buyer.province == "山东省"
        assert s.query(Signal).filter(
            Signal.enterprise_id == buyer.id, Signal.type == "tender_buy").count() == 1
        # 清理
        bidder = s.query(Enterprise).filter(
            Enterprise.uscc == "91370000SALEDIX1").one()
        for ent in (buyer, bidder):
            s.query(Signal).filter(Signal.enterprise_id == ent.id).delete()
            s.query(EnterpriseAlias).filter(
                EnterpriseAlias.enterprise_id == ent.id).delete()
            s.delete(ent)
        s.commit()


def test_backfill_buyer_province(client, admin_headers, monkeypatch):
    """回填任务（SPEC-SALE S2）：无省份 buyer 补齐；幂等二遍零变更；已有省份不碰。"""
    from app.collectors import ggzy as ggzy_mod

    calls: list[str] = []

    def fake_list_bids(uniscid, page=1, size=50, client=None):
        calls.append(uniscid)
        if uniscid == "91370000SALEBF01":
            return {"records": [
                {"projectName": "SALE回填项目一成交公告", "areaCode": "",
                 "tenderOrgName": "x", "createTime": "2026-09-01"},
                {"projectName": "SALE回填项目二成交公告", "areaCode": "山东省",
                 "tenderOrgName": "x", "createTime": "2026-10-01"},
            ], "total": 2, "pages": 1}
        return {"records": [  # 无省名记录 → no_area
            {"projectName": "SALE回填项目三成交公告", "areaCode": " ",
             "tenderOrgName": "x", "createTime": "2026-10-01"},
        ], "total": 1, "pages": 1}

    monkeypatch.setattr(ggzy_mod, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(ggzy_mod, "list_bids", fake_list_bids)

    with SessionLocal() as s:
        a = Enterprise(name="SALE回填甲研究院", source="ggzy_buyer",
                       uscc="91370000SALEBF01")
        b = Enterprise(name="SALE回填乙研究院", source="ggzy_buyer",
                       uscc="91370000SALEBF02")
        c = Enterprise(name="SALE回填丙研究院", source="ggzy_buyer",
                       uscc="91370000SALEBF03", province="浙江省")
        s.add_all([a, b, c])
        s.commit()
        ids = (a.id, b.id, c.id)

    r = client.post("/api/tasks/backfill-buyer-province", headers=admin_headers)
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    assert _wait_task(task_id) == "done"
    with SessionLocal() as s:
        cp = s.get(TaskCheckpoint, task_id)
        import json as _json

        stats1 = _json.loads(cp.state_json or "{}")
    assert stats1["filled"] == 1 and stats1["no_area"] == 1
    assert "91370000SALEBF01" in calls and "91370000SALEBF02" in calls
    assert "91370000SALEBF03" not in calls  # 已有省份不在待回填集

    with SessionLocal() as s:
        assert s.get(Enterprise, ids[0]).province == "山东省"  # 取最近有 area 的记录
        assert not (s.get(Enterprise, ids[1]).province or "")
        assert s.get(Enterprise, ids[2]).province == "浙江省"

    # 幂等二遍：甲已补齐不再命中，丙不碰 → 零变更
    r2 = client.post("/api/tasks/backfill-buyer-province", headers=admin_headers)
    assert r2.status_code == 200
    task_id2 = r2.json()["task_id"]
    assert _wait_task(task_id2) == "done"
    with SessionLocal() as s:
        import json as _json

        cp2 = s.get(TaskCheckpoint, task_id2)
        stats2 = _json.loads(cp2.state_json or "{}")
        assert stats2["filled"] == 0
        assert s.get(Enterprise, ids[0]).province == "山东省"
        assert not (s.get(Enterprise, ids[1]).province or "")
        assert s.get(Enterprise, ids[2]).province == "浙江省"
        s.query(TaskCheckpoint).filter(
            TaskCheckpoint.task_id.in_([task_id, task_id2])).delete()
        for eid in ids:
            ent = s.get(Enterprise, eid)
            s.query(EnterpriseAlias).filter(
                EnterpriseAlias.enterprise_id == eid).delete()
            s.delete(ent)
        s.commit()


# ---------------------------------------------------------------------------
# S4 导出联系人列修复
# ---------------------------------------------------------------------------


def test_export_contacts_from_phones_json(client, admin_headers):
    """导出「联系人」列改读 phones_json + email 拼接；title 槽位旧号码不再出现。"""
    with SessionLocal() as s:
        e = Enterprise(name="SALE导出机械有限公司", source="csv")
        s.add(e)
        s.flush()
        s.add(Contact(
            enterprise_id=e.id, name="SALE导出联系人一", role="buyer_unit",
            title="旧槽位号码13900000002",  # SPEC-CU 后 title 不再是号码槽位
            phones_json='["13800000001", "010-88886666"]', email="wangs@buyer.cn",
        ))
        s.add(Contact(
            enterprise_id=e.id, name="SALE导出联系人二", role="tender_agency",
            phones_json="[]", email="sale2@buyer.cn",
        ))
        s.commit()
        eid = e.id

    r = client.get("/api/enterprises/export?q=SALE导出机械", headers=admin_headers)
    assert r.status_code == 200, r.text
    text = r.text
    assert "13800000001" in text and "010-88886666" in text  # phones_json 落列
    assert "wangs@buyer.cn" in text  # email 拼入（"姓名(角色):电话 邮箱"）
    assert "SALE导出联系人二(代理机构):sale2@buyer.cn" in text  # 无电话仅邮箱
    assert "13900000002" not in text  # title 槽位旧号码不再导出
    assert "SALE导出联系人一(采购单位):13800000001/010-88886666 wangs@buyer.cn" in text

    # 清理
    with SessionLocal() as s:
        s.query(Contact).filter(Contact.enterprise_id == eid).delete()
        ent = s.get(Enterprise, eid)
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == eid).delete()
        s.delete(ent)
        s.commit()
