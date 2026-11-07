"""SPEC-BT 回归：客户旅程时间线——八源合流/降序/type 过滤。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bt-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Contact,
    Contract,
    Enterprise,
    EnterpriseAlias,
    EnterpriseNote,
    Followup,
    InstalledUnit,
    Lead,
    PaymentMilestone,
    Quote,
    Signal,
    User,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS = [
    Enterprise, Contact, Signal, Followup, Quote, Contract,
    PaymentMilestone, InstalledUnit, EnterpriseNote, Lead, User, AuditLog,
]


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
    """八源各一 + 一条已删企业对照。"""
    with SessionLocal() as s:
        e = Enterprise(name="BT旅程测试机械有限公司", source="poi")
        gone = Enterprise(name="BT已删企业", source="poi", deleted_at=utcnow())
        owner = User(username="bt_owner", password_hash="x", role="sales")
        s.add_all([e, gone, owner])
        s.flush()
        s.add(Signal(enterprise_id=e.id, type="tender_win", title="BT中标公告",
                     url="https://example.com/bt", pub_date="2026-11-07",
                     evidence_claim="该单位中标真空泵采购项目"))
        c1 = Contact(enterprise_id=e.id, name="BT王工", role="buyer_contact",
                     phones_json='["13800000021"]', call_result="interested",
                     called_at="2026-11-06T10:00:00")
        lead = Lead(enterprise_id=e.id, status="intent", owner_id=None, tenant_id=1)
        s.add_all([c1, lead])
        s.flush()
        s.add_all([
            Followup(lead_id=lead.id, content="BT首次电话跟进，客户有意向",
                     created_at="2026-11-06T11:00:00"),
            Quote(lead_id=lead.id, status="draft", total_wan=12.5,
                  created_at="2026-11-05T09:00:00"),
            Contract(lead_id=lead.id, name="BT一期合同", amount_wan=12.5,
                     status="active", start_date="2026-11-04"),
            InstalledUnit(enterprise_id=e.id, model="EVS-70", status="active",
                          install_date="2026-11-03", notes="首台装机"),
            EnterpriseNote(enterprise_id=e.id, user_id=owner.id, content="BT客户档案备注",
                           created_at="2026-11-02T08:00:00"),
            # 已删企业对照：时间线绝不泄露
            Signal(enterprise_id=gone.id, type="tender", title="BT已删企业信号",
                   url="", pub_date="2026-11-07"),
        ])
        s.flush()
        contract_id = s.query(Contract).filter(Contract.name == "BT一期合同").one().id
        s.add(PaymentMilestone(contract_id=contract_id, label="预付款",
                               amount_wan=5.0, status="paid",
                               paid_date="2026-11-04"))
        s.commit()
        return {"eid": e.id, "gone": gone.id}


def test_bt_timeline_merges_all_sources(client, admin_headers, seeded):
    r = client.get(f"/api/enterprises/{seeded['eid']}/timeline", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    types = {i["type"] for i in d["items"]}
    assert types == {"signal", "call", "followup", "quote", "contract",
                     "payment", "note", "unit"}
    # ts 降序
    tss = [i["ts"] for i in d["items"]]
    assert tss == sorted(tss, reverse=True)
    # 电话事件的 7 态中文化
    call = next(i for i in d["items"] if i["type"] == "call")
    assert "有意向" in call["detail"]
    # 信号带原文链
    sig = next(i for i in d["items"] if i["type"] == "signal")
    assert sig["url"] == "https://example.com/bt"


def test_bt_timeline_filter_and_404(client, admin_headers, seeded):
    # type 过滤
    r = client.get(f"/api/enterprises/{seeded['eid']}/timeline?type=call",
                   headers=admin_headers)
    items = r.json()["items"]
    assert items and all(i["type"] == "call" for i in items)

    # 404：不存在/已删
    assert client.get("/api/enterprises/999999/timeline", headers=admin_headers).status_code == 404
    assert client.get(f"/api/enterprises/{seeded['gone']}/timeline",
                      headers=admin_headers).status_code == 404
