"""SPEC-J J4 触达闭环测试：模板/退订/预算链/enqueue 幂等/回信匹配/调度全链。

使用 TestClient + 临时 SQLite（VACLEAD_DB 环境变量必须在导入 app 前设置）。
"""
from __future__ import annotations

import os
import tempfile

pytest_tmp = tempfile.mkdtemp(prefix="vaclead-j4-")
os.environ.setdefault("VACLEAD_DB", os.path.join(pytest_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Campaign,
    Contact,
    EmailAccount,
    EmailMessage,
    Enterprise,
    Followup,
    Lead,
    Sequence,
    Unsubscribe,
    utcnow,
)
from app.outreach.budget import available_budget, sent_today  # noqa: E402
from app.outreach.dispatcher import enqueue_campaign, is_auto_reply, run_outreach_dispatch  # noqa: E402
from app.outreach.templates import (  # noqa: E402
    render,
    unsubscribe_email,
    unsubscribe_token,
)

init_db(seed=False)


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
def outreach_fixture(db):
    """一套最小触达场景：企业+线索+联系人+账户+序列+活动（邮箱唯一防 UNIQUE 撞）。"""
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
    seq = Sequence(name=f"三步触达{tag}", steps=_dumps([
        {"delay_days": 0, "subject": "您好 {{contact.name}}", "body": "介绍 {{enterprise.name}} 的真空方案"},
        {"delay_days": 3, "subject": "再访", "body": "上次邮件不知是否收到"},
    ]))
    db.add(seq)
    db.flush()
    camp = Campaign(name=f"Q4触达{tag}", sequence_id=seq.id, email_account_id=account.id,
                    status="running",
                    target_filter=_dumps({"source": f"manual-{tag}"}))
    db.add(camp)
    db.commit()
    return {"ent": ent, "lead": lead, "contact": contact,
            "account": account, "seq": seq, "camp": camp, "tag": tag}


def _dumps(steps):
    import json

    return json.dumps(steps, ensure_ascii=False)


# ---------------- 模板渲染 ----------------
def test_template_render_vars(db, outreach_fixture):
    ent = outreach_fixture["ent"]
    contact = outreach_fixture["contact"]
    account = outreach_fixture["account"]
    subject, body = render(
        db, "您好 {{contact.name}}", "{{enterprise.name}}的{{unknown_var}}方案 {{sender.name}}",
        to_email="wang@hengxing.cn", enterprise=ent, contact=contact, sender=account,
    )
    assert subject == "您好 王工"
    assert f"{ent.name}的方案 {account.name}" in body  # 未知变量替换空串
    assert "/api/public/unsubscribe/" in body  # 合规：未显式写退订链接自动补页脚
    assert "{{" not in body and "{{" not in subject


def test_unsubscribe_token_roundtrip(db):
    token = unsubscribe_token(db, "Foo@Example.cn")
    assert unsubscribe_email(db, token) == "Foo@Example.cn"  # 大小写归一还原
    assert unsubscribe_email(db, token[:-2] + "xx") is None  # 篡改拒绝
    assert unsubscribe_email(db, "garbage") is None


# ---------------- 公开退订 ----------------
def test_public_unsubscribe_flow(client, db, outreach_fixture):
    email = outreach_fixture["contact"].email
    token = unsubscribe_token(db, email)
    r = client.get(f"/api/public/unsubscribe/{token}")
    assert r.status_code == 200 and email in r.text and "确认退订" in r.text
    r2 = client.post(f"/api/public/unsubscribe/{token}")
    assert r2.status_code == 200 and "已加入退订名单" in r2.text
    assert db.query(Unsubscribe).filter(Unsubscribe.email == email).count() == 1
    # 幂等：重复确认不产生重复行
    client.post(f"/api/public/unsubscribe/{token}")
    assert db.query(Unsubscribe).filter(Unsubscribe.email == email).count() == 1
    assert unsubscribe_email(db, token) == email


# ---------------- 预算链 ----------------
def test_budget_chain_layers(db, outreach_fixture):
    account = outreach_fixture["account"]
    camp = outreach_fixture["camp"]
    # 成熟账户（无预热起点）：min(cap=20, limit=50) = 20
    assert available_budget(db, account, camp) == 20
    # 活动限额收紧
    camp.daily_limit = 5
    assert available_budget(db, account, camp) == 5
    camp.daily_limit = 50
    # 当日已发扣减
    msg = EmailMessage(campaign_id=camp.id, lead_id=outreach_fixture["lead"].id,
                       account_email=account.email, to_email="x@y.cn",
                       status="sent", sent_at=utcnow())
    db.add(msg)
    db.commit()
    assert available_budget(db, account, camp) == 19
    assert sent_today(db, account.email) == 1
    # 健康态硬熔断
    account.health = "blocked"
    assert available_budget(db, account, camp) == 0
    account.health = "healthy"
    # 暂停账户
    account.status = "paused"
    assert available_budget(db, account, camp) == 0
    account.status = "active"
    # 预热第 1 天：ramp = base 2 × 日随机(<1.1) < cap
    account.warmup_started_at = utcnow()
    warm = available_budget(db, account, camp)
    assert 0 <= warm <= 2


# ---------------- enqueue 幂等/排除 ----------------
def test_enqueue_dedup_and_exclusions(db, outreach_fixture):
    camp = outreach_fixture["camp"]
    first = enqueue_campaign(db, camp)
    assert first["created"] == 2  # 一个线索 × 两步
    assert first["skipped_noemail"] == 0
    # UNIQUE 硬约束：重复行插不进
    with pytest.raises(IntegrityError):
        db.add(EmailMessage(campaign_id=camp.id, lead_id=outreach_fixture["lead"].id,
                            step_idx=0, to_email="dup@x.cn"))
        db.commit()
    db.rollback()
    # 幂等重跑：全变 dup
    second = enqueue_campaign(db, camp)
    assert second["created"] == 0 and second["skipped_dup"] == 2
    # 步骤排期：首步 now，次步 +3 天
    msgs = (db.query(EmailMessage)
            .filter(EmailMessage.campaign_id == camp.id)
            .order_by(EmailMessage.step_idx).all())
    assert msgs[1].scheduled_at > msgs[0].scheduled_at
    # 退订排除：名单内邮箱的线索不再入队
    db.add(Unsubscribe(email=outreach_fixture["contact"].email))
    db.query(EmailMessage).filter(EmailMessage.campaign_id == camp.id).delete()
    db.commit()
    third = enqueue_campaign(db, camp)
    assert third["skipped_unsub"] == 1 and third["created"] == 0


# ---------------- 回信匹配 ----------------
def _seed_sent(db, fx, *, message_id, subject, to=None, step_idx=0):
    msg = EmailMessage(campaign_id=fx["camp"].id, lead_id=fx["lead"].id, step_idx=step_idx,
                       account_email=fx["account"].email,
                       to_email=to or fx["contact"].email,
                       subject=subject, status="sent", sent_at=utcnow(),
                       message_id=message_id, thread_id=message_id)
    db.add(msg)
    db.commit()
    return msg


def test_reply_matching_in_reply_to(db, outreach_fixture, monkeypatch):
    msg = _seed_sent(db, outreach_fixture, message_id="<abc@x>", subject="您好 王工")

    def fake_list(session, limit=50):
        return [{"id": "inb-1", "inReplyTo": "<abc@x>", "subject": "Re: 您好 王工",
                 "text": "请发详细资料", "from": "wang@hengxing.cn"}]

    from app.notify import emailengine_client

    monkeypatch.setattr(emailengine_client, "list_messages", fake_list)
    monkeypatch.setattr(emailengine_client, "is_configured", lambda s: True)
    stats = run_outreach_dispatch(db, send_fn=lambda *a, **k: (True, "<m@x>", None))
    assert stats["replies"] == 1
    db.expire_all()
    assert db.get(EmailMessage, msg.id).status == "replied"
    f = db.query(Followup).filter(Followup.lead_id == msg.lead_id).first()
    assert f is not None and "请发详细资料" in f.content
    # 幂等：同一封回信重扫不重复落跟进
    run_outreach_dispatch(db, send_fn=lambda *a, **k: (True, "<m@x>", None))
    assert db.query(Followup).filter(Followup.lead_id == msg.lead_id).count() == 1


def test_reply_subject_fallback_and_auto_reply(db, outreach_fixture, monkeypatch):
    _seed_sent(db, outreach_fixture, message_id="<def@x>", subject="真空方案介绍")
    _seed_sent(db, outreach_fixture, message_id="<ghi@x>", subject="报价已发送",
               to="qian@hengxing.cn", step_idx=1)

    def fake_list(session, limit=50):
        # ① 主题 fallback（无 In-Reply-To）② auto-reply 只记事件不落跟进
        return [
            {"id": "inb-2", "subject": "Re: 真空方案介绍", "text": "有兴趣，约个时间",
             "from": "wang@hengxing.cn"},
            {"id": "inb-3", "subject": "自动回复：Re: 报价已发送",
             "text": "我正在休假 Auto Reply", "from": "qian@hengxing.cn"},
        ]

    from app.notify import emailengine_client

    monkeypatch.setattr(emailengine_client, "list_messages", fake_list)
    monkeypatch.setattr(emailengine_client, "is_configured", lambda s: True)
    stats = run_outreach_dispatch(db, send_fn=lambda *a, **k: (True, "<m@x>", None))
    assert stats["replies"] == 2
    db.expire_all()
    # 主题 fallback 命中 + 落跟进
    f = (db.query(Followup)
         .filter(Followup.content.like("%有兴趣，约个时间%")).first())
    assert f is not None
    # auto-reply：状态 replied 但无跟进
    m2 = db.query(EmailMessage).filter(EmailMessage.message_id == "<ghi@x>").first()
    assert m2.status == "replied"
    assert db.query(Followup).filter(Followup.content.like("%休假%")).first() is None


def test_is_auto_reply_words():
    assert is_auto_reply("Automatic Reply", "out of office")
    assert is_auto_reply("退订确认", "")
    assert not is_auto_reply("期待合作", "请发资料过来")


# ---------------- 调度全链（stub 注入） ----------------
def test_dispatch_full_chain_with_stub(db, outreach_fixture):
    camp = outreach_fixture["camp"]
    enqueue_campaign(db, camp)
    first_msg = (db.query(EmailMessage)
                 .filter(EmailMessage.campaign_id == camp.id, EmailMessage.step_idx == 0)
                 .first())
    calls: list[str] = []

    def stub_send(acc, *, to_email, subject, body, **kw):
        calls.append(to_email)
        assert "unsubscribe" in body or "退订" in body  # 每信必带退订
        return True, f"<sent-{len(calls)}@x>", None

    totals = run_outreach_dispatch(db, send_fn=stub_send)
    assert totals["sent"] == 1 and totals["failed"] == 0
    db.expire_all()
    db.refresh(first_msg)
    assert first_msg.status == "sent" and first_msg.message_id == "<sent-1@x>"
    assert first_msg.thread_id == "<sent-1@x>"
    # 渲染结果回写留档：客户收到的是渲染后文本，无 {{}} 残留
    assert "{{" not in first_msg.subject and "王工" in first_msg.subject
    # 第二步 scheduled_at 在未来（+3 天）→ 本次不发送
    second = (db.query(EmailMessage)
              .filter(EmailMessage.campaign_id == camp.id, EmailMessage.step_idx == 1)
              .first())
    assert second.status == "queued"
    # 重跑：预算内无到期消息 → 不重复发送
    totals2 = run_outreach_dispatch(db, send_fn=stub_send)
    assert totals2["sent"] == 0 and len(calls) == 1


def test_dispatch_skips_unsubscribed(db, outreach_fixture):
    camp = outreach_fixture["camp"]
    enqueue_campaign(db, camp)
    db.add(Unsubscribe(email=outreach_fixture["contact"].email))
    db.commit()
    totals = run_outreach_dispatch(db, send_fn=lambda *a, **k: (True, "<m@x>", None))
    assert totals["skipped_unsub"] >= 1
    db.expire_all()
    failed = (db.query(EmailMessage)
              .filter(EmailMessage.campaign_id == camp.id, EmailMessage.status == "failed")
              .first())
    assert failed is not None and failed.error == "unsubscribed"


# ---------------- API 层 ----------------
def test_outreach_api_crud_flow(client, admin_headers, db, outreach_fixture):
    h = admin_headers
    ent_name = outreach_fixture["ent"].name
    seqs = client.get("/api/outreach/sequences", headers=h).json()
    assert any(s["name"] == outreach_fixture["seq"].name for s in seqs["items"])
    camps = client.get("/api/outreach/campaigns", headers=h).json()
    row = next(c for c in camps["items"] if c["name"] == outreach_fixture["camp"].name)
    assert row["status"] == "running" and "budget" in row and "today_sent" in row
    # enqueue 端点
    r = client.post(f"/api/outreach/campaigns/{row['id']}/enqueue", headers=h)
    assert r.status_code == 200 and "created" in r.json()
    # 消息列表
    msgs = client.get(f"/api/outreach/messages?campaign_id={row['id']}", headers=h).json()
    assert msgs["items"] and msgs["items"][0]["enterprise"] == ent_name
    # 状态机：draft 不能直接 done；running → paused
    r2 = client.put(f"/api/outreach/campaigns/{row['id']}", headers=h, json={"status": "paused"})
    assert r2.status_code == 200 and r2.json()["status"] == "paused"
    # 序列删除被引用 → 409
    sid = row["sequence_id"]
    r3 = client.delete(f"/api/outreach/sequences/{sid}", headers=h)
    assert r3.status_code == 409
    # 未登录拒
    assert client.get("/api/outreach/campaigns").status_code in (401, 403)


def test_outreach_task_endpoint(client, admin_headers):
    h = admin_headers
    r = client.post("/api/tasks/outreach-dispatch", headers=h)
    assert r.status_code == 200 and r.json().get("task_id")
