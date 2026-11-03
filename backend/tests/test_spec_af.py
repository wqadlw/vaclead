"""SPEC-AF 回归：邮件跟踪报表 + 跟进时机建议 + 工作台偏好 + 合同余款入预测。"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-af-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Campaign,
    Contact,
    Contract,
    EmailAccount,
    EmailMessage,
    Enterprise,
    Followup,
    Lead,
    OutreachEvent,
    PaymentMilestone,
    Sequence,
    Signal,
    utcnow,
)
import json  # noqa: E402

init_db(seed=False)

#: 共享测试库隔离：模块前后按 id 快照清理本模块新增行（教训同 W37——
#: 否则本模块的时间戳行会落进他模块的周报窗口，test_spec_o 全量跑必挂）
from app.models import (  # noqa: E402
    Campaign as _Camp,
    Contact as _Cont,
    Contract as _Contract,
    EmailAccount as _Acct,
    EmailMessage as _Emsg,
    Enterprise as _Ent,
    Followup as _Fu,
    Lead as _Lead,
    OutreachEvent as _Oev,
    PaymentMilestone as _Pm,
    Sequence as _Seq,
    SettingsKV,
    Signal as _Sig,
)

_ISOLATED_MODELS: list[type] = [
    _Oev, _Emsg, _Camp, _Pm, _Contract, _Fu, _Sig, _Cont, _Lead, _Ent, _Seq, _Acct,
]


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            snap[m.__tablename__] = (
                s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            )
            snap[m.__tablename__] = (snap[m.__tablename__] or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
        s.query(SettingsKV).filter(SettingsKV.key.like("pref:%")).delete()
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


@pytest.fixture()
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def fx(db):
    """最小触达场景（同 J4 形态，uuid 防唯一撞）。"""
    import uuid

    tag = uuid.uuid4().hex[:6]
    ent = Enterprise(name=f"恒星真空仪器{tag}有限公司", source=f"manual-{tag}")
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="following")
    db.add(lead)
    db.flush()
    contact = Contact(enterprise_id=ent.id, name="王工", role="buyer_unit",
                      email=f"wang{tag}@hengxing.cn", verify_state="verified")
    db.add(contact)
    account = EmailAccount(name=f"主发信{tag}", email=f"sales{tag}@hengxing.cn",
                           provider="smtp", smtp_host="smtp.example.com",
                           smtp_port=587, daily_cap=20)
    db.add(account)
    db.flush()
    seq = Sequence(name=f"两步{tag}", steps=json.dumps([
        {"delay_days": 0, "subject": "您好", "body": "方案介绍 https://example.com/deck"},
    ]))
    db.add(seq)
    db.flush()
    camp = Campaign(name=f"AF触达{tag}", sequence_id=seq.id, email_account_id=account.id,
                    status="running")
    db.add(camp)
    db.commit()
    return {"ent": ent, "lead": lead, "contact": contact,
            "account": account, "camp": camp, "tag": tag}


# ---------------- AF1 跟踪 ----------------
def test_track_token_roundtrip_and_tamper(db, fx):
    from app.outreach.track import track_message_id, track_token

    tok = track_token(db, 42)
    assert track_message_id(db, tok) == 42
    assert track_message_id(db, tok[:-2] + "xx") is None  # 篡改拒绝
    assert track_message_id(db, "not-a-token!!") is None


def test_open_pixel_dedup_and_click_redirect(client, db, fx):
    from app.outreach.track import track_token

    msg = EmailMessage(campaign_id=fx["camp"].id, lead_id=fx["lead"].id, step_idx=0,
                       account_email=fx["account"].email,
                       to_email=fx["contact"].email,
                       subject="您好", body="正文", status="sent", sent_at=utcnow())
    db.add(msg)
    db.commit()
    tok = track_token(db, msg.id)

    r1 = client.get(f"/api/public/track/open/{tok}")
    assert r1.status_code == 200 and r1.headers["content-type"] == "image/gif"
    r2 = client.get(f"/api/public/track/open/{tok}")
    assert r2.status_code == 200
    # MPP 预取去重：同一封只记一次唯一 open（第二次不落事件）
    assert db.query(OutreachEvent).filter(
        OutreachEvent.message_id == msg.id, OutreachEvent.type == "open").count() == 1

    r3 = client.get(f"/api/public/track/click/{tok}",
                    params={"u": "https://example.com/deck"}, follow_redirects=False)
    assert r3.status_code in (302, 307)
    assert "example.com/deck" in r3.headers["location"]
    # 非法 scheme 落 "/"，不写事件
    r4 = client.get(f"/api/public/track/click/{tok}",
                    params={"u": "javascript:alert(1)"}, follow_redirects=False)
    assert r4.headers["location"] == "/"
    clicks = db.query(OutreachEvent).filter(
        OutreachEvent.message_id == msg.id, OutreachEvent.type == "click").all()
    assert len(clicks) == 1 and json.loads(clicks[0].payload)["u"] == "https://example.com/deck"


def test_dispatch_injects_tracking_html(db, fx):
    """track_enabled=1 注入像素+链接改写；=0 不注入；纯文本正文不被改写。"""
    from app.outreach.dispatcher import run_outreach_dispatch

    msg = EmailMessage(campaign_id=fx["camp"].id, lead_id=fx["lead"].id, step_idx=0,
                       account_email=fx["account"].email,
                       to_email=fx["contact"].email,
                       subject="方案", body="详情见 https://example.com/deck 谢谢",
                       status="queued", scheduled_at=utcnow())
    db.add(msg)
    db.commit()

    captured: dict = {}

    def fake_send(acc, *, to_email, subject, body, **kw):
        captured["body"], captured["html"] = body, kw.get("html")
        return True, "<af1@x>", None

    stats = run_outreach_dispatch(db, send_fn=fake_send)
    assert stats["sent"] == 1
    # 纯文本部分保持原样（可读保底）
    assert "https://example.com/deck" in captured["body"]
    # HTML 部分含像素 + 点击改写链接
    assert "<img" in captured["html"] and "/api/public/track/open/" in captured["html"]
    assert "/api/public/track/click/" in captured["html"]
    assert 'href="' in captured["html"]

    # 关闭跟踪 → 无 html
    fx["camp"].track_enabled = 0
    db.commit()
    msg2 = EmailMessage(campaign_id=fx["camp"].id, lead_id=fx["lead"].id, step_idx=5,
                        account_email=fx["account"].email,
                        to_email=fx["contact"].email,
                        subject="方案2", body="正文2", status="queued",
                        scheduled_at=utcnow())
    db.add(msg2)
    db.commit()
    captured2: dict = {}
    run_outreach_dispatch(db, send_fn=lambda *a, **k: captured2.update(html=k.get("html")) or (True, "<af2@x>", None))
    assert captured2["html"] is None


def test_campaign_stats_unique_rates(client, db, fx, admin_headers):
    msg = EmailMessage(campaign_id=fx["camp"].id, lead_id=fx["lead"].id, step_idx=0,
                       account_email=fx["account"].email,
                       to_email=fx["contact"].email,
                       subject="s", body="b", status="sent", sent_at=utcnow())
    db.add(msg)
    db.commit()
    for t in ("open", "open", "click"):
        db.add(OutreachEvent(message_id=msg.id, type=t, payload="{}"))
    db.commit()
    d = client.get(f"/api/outreach/campaigns/{fx['camp'].id}/stats",
                   headers=admin_headers).json()
    assert d["sent"] == 1 and d["unique_open"] == 1 and d["unique_click"] == 1
    assert d["open_rate"] == 1.0 and d["click_rate"] == 1.0
    assert isinstance(d["recent_events"], list)


# ---------------- AF2 行动建议 ----------------
def test_advice_first_touch_and_at_risk(db, fx):
    from app.advice import build_lead_advice

    lead = fx["lead"]
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
    lead.created_at = old  # 3 天前未首联 → 黄金窗
    lead.lifecycle_stage = "at_risk"
    lead.health_score = 20
    db.commit()
    codes = [a["code"] for a in build_lead_advice(db, lead)]
    assert "first_touch" in codes and "at_risk_save" in codes
    # 全部建议 why 可解释
    for a in build_lead_advice(db, lead):
        assert a["why"] and a["title"]


def test_advice_hot_signal_and_switch_channel(db, fx):
    from app.advice import build_lead_advice

    lead = fx["lead"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    db.add(Signal(enterprise_id=fx["ent"].id, type="tender", title="镀膜机采购公告",
                  url="", base_score=60, created_at=now))
    for i in range(5):
        db.add(Followup(lead_id=lead.id, content=f"第{i}次电话", created_at=now))
    db.commit()
    codes = [a["code"] for a in build_lead_advice(db, lead)]
    assert "hot_signal" in codes
    assert "switch_channel" in codes  # 5 次跟进无回复
    for a in build_lead_advice(db, lead):
        if a["code"] == "hot_signal":
            assert "镀膜机" in a["title"]


def test_advice_endpoint_auth(client, db, fx, admin_headers):
    r = client.get(f"/api/leads/{fx['lead'].id}/advice", headers=admin_headers)
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


# ---------------- AF3 偏好 ----------------
def test_dashboard_prefs_roundtrip(client, admin_headers):
    r = client.put("/api/prefs/dashboard",
                   json={"order": ["trend", "kpi", "funnel"], "hidden": ["signals"]},
                   headers=admin_headers)
    assert r.status_code == 200
    d = client.get("/api/prefs/dashboard", headers=admin_headers).json()
    assert d["order"] == ["trend", "kpi", "funnel"] and d["hidden"] == ["signals"]
    # 空默认
    r2 = client.get("/api/prefs/nosuchpage", headers=admin_headers)
    assert r2.status_code == 404


# ---------------- AF4 合同余款 ----------------
def test_forecast_contract_backlog(client, db, fx, admin_headers):
    c = Contract(lead_id=fx["lead"].id, name="AF合同", amount_wan=100, status="active")
    db.add(c)
    db.flush()
    db.add(PaymentMilestone(contract_id=c.id, seq=1, label="预付款",
                            ratio=30, amount_wan=40, status="paid"))
    db.add(PaymentMilestone(contract_id=c.id, seq=2, label="验收款",
                            ratio=70, amount_wan=60, status="pending"))
    db.commit()
    d = client.get("/api/dashboard/summary", headers=admin_headers).json()
    backlog = d["forecast"]["contract_backlog"]
    assert backlog >= 60.0, f"backlog 应含 active 合同余款，实际 {backlog}"
    # 清理（避免污染同模块后续断言）
    db.query(PaymentMilestone).filter(PaymentMilestone.contract_id == c.id).delete()
    db.delete(c)
    db.commit()
