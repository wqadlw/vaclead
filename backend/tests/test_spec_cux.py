"""SPEC-CUX 回归：联系人中心深度强化——排序切换 / 通话时间线（键盘流为纯前端）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cux-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    Followup,
    Lead,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, Lead, Followup]


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
def seeded():
    """三企业三联系人：id 递增、意图分 90/95/70、拨打时间混合——四态排序可区分。

    B 意图分最高（95）但无号码（不可拨）：默认序把它垫尾（可拨优先），
    intent 序把它排头——四态互不相同：
    默认=[A, C, B] ≠ intent=[B, A, C] ≠ newest=[C, B, A]
    ≠ recent_called=[C, A, B]（已拨倒序，B 未拨垫尾）。
    """
    with SessionLocal() as s:
        eA = Enterprise(name="CUX甲高意图公司", source="poi", intent_score=90)
        eB = Enterprise(name="CUX乙最高意图无号码", source="poi", intent_score=95)
        eC = Enterprise(name="CUX丙中意图贸易", source="poi", intent_score=70)
        s.add_all([eA, eB, eC])
        s.flush()
        # A：高意图已拨（11-01）；B：最高意图但无号码；C：中意图最近拨（11-05）
        cA = Contact(
            enterprise_id=eA.id, name="CUX甲王工", role="buyer_contact",
            phones_json='["13911110001"]', call_result="connected",
            called_at="2026-11-01T09:00:00",
        )
        cB = Contact(enterprise_id=eB.id, name="CUX乙李总", role="legal_rep")
        cC = Contact(
            enterprise_id=eC.id, name="CUX丙张经理", role="buyer_contact",
            phones_json='["13911110003"]', call_result="no_answer",
            called_at="2026-11-05T09:00:00",
        )
        s.add_all([cA, cB, cC])
        s.commit()
        return {"cA": cA.id, "cB": cB.id, "cC": cC.id}


def test_cux_worklist_order_modes(client, admin_headers, seeded):
    """order 白名单三态 + 非法回退默认：四态排序互不相同，逐一断言。"""
    ids = {seeded["cA"], seeded["cB"], seeded["cC"]}

    def mine(params: str) -> list[int]:
        r = client.get(f"/api/contacts?{params}", headers=admin_headers)
        assert r.status_code == 200
        return [i["id"] for i in r.json()["items"] if i["id"] in ids]

    _default = [seeded["cA"], seeded["cC"], seeded["cB"]]
    # 默认智能推荐序（order 缺省）：可拨 → 意图分 → 未拨 → id
    # B 意图分 95 最高但无号码 → 可拨优先把它垫尾
    assert mine("has_phone=0&limit=200") == _default
    # 显式空串与缺省一致；非法值回退默认（白名单外不覆写）
    assert mine("has_phone=0&limit=200&order=") == _default
    assert mine("has_phone=0&limit=200&order=bogus") == _default
    # newest：id 降序（最近入库先打）
    assert mine("has_phone=0&limit=200&order=newest") == [
        seeded["cC"], seeded["cB"], seeded["cA"],
    ]
    # intent：企业意图分降序（B 95 分排头，与默认序分化）
    assert mine("has_phone=0&limit=200&order=intent") == [
        seeded["cB"], seeded["cA"], seeded["cC"],
    ]
    # recent_called：已拨在前按拨打时间倒序（C 11-05 > A 11-01），未拨 B 垫尾
    assert mine("has_phone=0&limit=200&order=recent_called") == [
        seeded["cC"], seeded["cA"], seeded["cB"],
    ]


def test_cux_timeline_merge(client, admin_headers):
    """timeline 合流：called_at + 2 条跟进 → 3 项按时间倒序（字符串 ISO 可比）。"""
    with SessionLocal() as s:
        e = Enterprise(name="CUX时间线科技有限公司", source="poi", intent_score=60)
        s.add(e)
        s.flush()
        c = Contact(
            enterprise_id=e.id, name="CUX时间线周工", role="buyer_contact",
            phones_json='["13922220001"]', call_result="connected",
            called_at="2026-11-03T10:00:00",
        )
        lead = Lead(enterprise_id=e.id, status="following")
        s.add_all([c, lead])
        s.flush()
        s.add_all([
            Followup(lead_id=lead.id, content="首聊：了解现状", created_at="2026-11-01T08:00:00"),
            Followup(lead_id=lead.id, content="二聊：发报价", created_at="2026-11-05T09:00:00"),
        ])
        s.commit()
        cid = c.id
    r = client.get(f"/api/contacts/{cid}/prep", headers=admin_headers)
    assert r.status_code == 200
    tl = r.json()["timeline"]
    # 拨打事件夹在两条跟进之间 → 合流（非分组拼接）
    assert [(t["kind"], t["at"]) for t in tl] == [
        ("followup", "2026-11-05T09:00"),
        ("call", "2026-11-03T10:00"),
        ("followup", "2026-11-01T08:00"),
    ]
    assert tl[0]["content"] == "二聊：发报价"
    assert tl[1]["label"] == "已接通"  # call_result 中文标签
    assert tl[2]["content"] == "首聊：了解现状"


def test_cux_timeline_no_lead_call_only(client, admin_headers):
    """无线索：只有拨打事件（label=结果中文）；连拨打也没有 → 空时间线。"""
    with SessionLocal() as s:
        e = Enterprise(name="CUX无线索实业", source="poi", intent_score=40)
        s.add(e)
        s.flush()
        c_called = Contact(
            enterprise_id=e.id, name="CUX无线索打过", role="legal_rep",
            phones_json='["13933330001"]', call_result="no_answer",
            called_at="2026-11-06T14:30:00",
        )
        c_fresh = Contact(
            enterprise_id=e.id, name="CUX无线索没打过", role="legal_rep",
            phones_json='["13933330002"]',
        )
        s.add_all([c_called, c_fresh])
        s.commit()
        called_id, fresh_id = c_called.id, c_fresh.id
    r = client.get(f"/api/contacts/{called_id}/prep", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["timeline"] == [
        {"kind": "call", "at": "2026-11-06T14:30", "label": "无人接"}
    ]
    r = client.get(f"/api/contacts/{fresh_id}/prep", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["timeline"] == []
