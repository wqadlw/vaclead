"""SPEC-P Webhooks 出站测试：事件采集/签名投递/退避停用/SSRF 防护/端点。

使用 TestClient + 临时 SQLite（VACLEAD_DB 环境变量必须在导入 app 前设置）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import uuid

pytest_tmp = tempfile.mkdtemp(prefix="vaclead-p-")
os.environ.setdefault("VACLEAD_DB", os.path.join(pytest_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Enterprise,
    Lead,
    Webhook,
    WebhookDelivery,
    utcnow,
)
from app.webhookbus import _url_allowed, flush_deliveries, install, sign  # noqa: E402

init_db(seed=False)
install()  # 直连 session 的用例不经过 TestClient lifespan，需显式挂钩子（幂等）


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


def _mk_webhook(db, *, events="[]", active=1, url="https://hooks.example.com/x"):
    w = Webhook(
        name=f"wh-{uuid.uuid4().hex[:6]}", url=url,
        event_types=json.dumps(events), secret=uuid.uuid4().hex, active=active,
    )
    db.add(w)
    db.commit()
    return w


# ---------------- SSRF 防护 ----------------
def test_url_allowed_rules(db):
    assert not _url_allowed("http://127.0.0.1:3000/hook", db)
    assert not _url_allowed("http://localhost/hook", db)
    assert not _url_allowed("http://192.168.1.8/hook", db)
    assert not _url_allowed("http://169.254.1.2/hook", db)
    assert not _url_allowed("ftp://example.com", db)
    assert _url_allowed("https://hooks.example.com/x", db)
    assert _url_allowed("http://8.8.8.8/x", db)  # 公网 IP 形态
    # 显式放行内网（settings_kv 白名单，本机联调 emailengine 场景）
    from app.models import SettingsKV

    db.add(SettingsKV(key="webhook" + "_allow_private", value="1"))
    db.commit()
    assert _url_allowed("http://127.0.0.1:3000/hook", db)
    row = db.get(SettingsKV, "webhook" + "_allow_private")
    row.value = "0"
    db.commit()


# ---------------- 事件采集钩子 ----------------
def test_hook_emits_on_enterprise_create(db):
    w = _mk_webhook(db, events=[])  # 空订阅=接收全部
    ent = Enterprise(name=f"钩子{uuid.uuid4().hex[:6]}有限公司", source="manual")
    db.add(ent)
    db.commit()
    row = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == w.id, WebhookDelivery.event == "enterprise.create")
        .order_by(WebhookDelivery.id.desc())
        .first()
    )
    assert row is not None and row.status == "pending"
    payload = json.loads(row.payload)
    assert payload["entity"] == "enterprise" and payload["data"]["name"] == ent.name


def test_update_event_changes_and_machine_fields(db):
    w = _mk_webhook(db, events=[])
    ent = Enterprise(name=f"变更{uuid.uuid4().hex[:6]}有限公司", source="manual")
    db.add(ent)
    db.commit()
    # 机器字段变更：不发事件
    ent.last_followed_at = utcnow()
    db.commit()
    n0 = (db.query(WebhookDelivery)
          .filter(WebhookDelivery.webhook_id == w.id, WebhookDelivery.event == "enterprise.update")
          .count())
    # 业务字段变更：发事件且带 diff
    ent.name = ent.name[:-6] + "股份公司"
    db.commit()
    row = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == w.id, WebhookDelivery.event == "enterprise.update")
        .order_by(WebhookDelivery.id.desc())
        .first()
    )
    n1 = (db.query(WebhookDelivery)
          .filter(WebhookDelivery.webhook_id == w.id, WebhookDelivery.event == "enterprise.update")
          .count())
    assert n0 == 0 and n1 == 1
    payload = json.loads(row.payload)
    assert "name" in payload["changes"]


def test_subscription_filter(db):
    w = _mk_webhook(db, events=["lead.create"])  # 只订阅 lead.create
    before = (db.query(WebhookDelivery)
              .filter(WebhookDelivery.webhook_id == w.id).count())
    ent = Enterprise(name=f"订阅{uuid.uuid4().hex[:6]}有限公司", source="manual")
    db.add(ent)
    db.commit()  # enterprise.create 不应入队到该 webhook
    after = (db.query(WebhookDelivery)
             .filter(WebhookDelivery.webhook_id == w.id).count())
    assert before == after


# ---------------- 投递引擎 ----------------
def test_flush_success_body_and_signature(db):
    w = _mk_webhook(db, events=[])
    secret = w.secret
    ent = Enterprise(name=f"签名{uuid.uuid4().hex[:6]}有限公司", source="manual")
    db.add(ent)
    db.commit()
    delivery = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == w.id, WebhookDelivery.status == "pending")
        .order_by(WebhookDelivery.id.desc())
        .first()
    )
    captured: dict = {}

    def fake_post(url, headers, body_text):
        captured.update({"url": url, "headers": headers, "body": body_text})
        return 200, None

    stats = flush_deliveries(db, post_fn=fake_post)
    assert stats["sent"] >= 1
    db.expire_all()
    assert db.get(WebhookDelivery, delivery.id).status == "ok"
    # body 是数组（EspoCRM 批量语义）且签名可验证
    body = captured["body"]
    assert body.startswith("[") and body.endswith("]")
    assert captured["headers"]["X-Vaclead-Signature"] == sign(secret, body)
    assert json.loads(body)[0]["event"].startswith("enterprise.")


def test_flush_410_disables_webhook(db):
    w = _mk_webhook(db, events=[])
    ent = Enterprise(name=f"410{uuid.uuid4().hex[:6]}有限公司", source="manual")
    db.add(ent)
    db.commit()
    stats = flush_deliveries(db, post_fn=lambda *a: (410, None))
    db.expire_all()
    assert db.get(Webhook, w.id).active == 0  # 远端永久移除 → 自动停用
    assert stats["retired"] >= 1


def test_flush_retry_backoff_then_terminal(db):
    from datetime import datetime, timedelta, timezone

    w = _mk_webhook(db, events=[])
    ent = Enterprise(name=f"退避{uuid.uuid4().hex[:6]}有限公司", source="manual")
    db.add(ent)
    db.commit()
    base = datetime.now(timezone.utc)
    for i in range(4):
        # now 每轮前推 1 小时，跨过 2^attempts 分钟退避窗
        stats = flush_deliveries(
            db, post_fn=lambda *a: (500, "boom"),
            now=(base + timedelta(hours=i + 1)).isoformat(timespec="seconds"),
        )
        if i < 3:
            assert stats["requeued"] >= 1, f"第 {i + 1} 轮应重排"
    db.expire_all()
    d = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == w.id, WebhookDelivery.event == "enterprise.create")
        .order_by(WebhookDelivery.id.desc())
        .first()
    )
    assert d.status == "failed" and d.attempts == 4 and "500" in (d.last_error or "")
    assert d.next_attempt_at is None or d.next_attempt_at <= utcnow()


# ---------------- 端点 ----------------
def test_webhook_api_crud_and_masking(client, admin_headers):
    h = admin_headers
    r = client.post("/api/webhooks", headers=h, json={
        "name": "集成测试", "url": "https://hooks.example.com/vaclead",
        "event_types": ["lead.create", "signal.create"],
    })
    assert r.status_code == 201
    secret = r.json()["secret"]
    assert len(secret) >= 32
    wid = r.json()["id"]
    items = client.get("/api/webhooks", headers=h).json()["items"]
    row = next(i for i in items if i["id"] == wid)
    assert row["secret_masked"] == secret[:4] + "…"  # 明文不再出现
    assert row["event_types"] == ["lead.create", "signal.create"]
    # 非法 URL / 未知事件
    assert client.post("/api/webhooks", headers=h, json={
        "name": "内网", "url": "http://192.168.0.1/h"}).status_code == 422
    assert client.post("/api/webhooks", headers=h, json={
        "name": "坏事件", "url": "https://a.example.com/h",
        "event_types": ["user.delete"]}).status_code == 422
    # 启停
    assert client.put(f"/api/webhooks/{wid}", headers=h,
                      json={"active": False}).status_code == 200
    assert client.delete(f"/api/webhooks/{wid}", headers=h).status_code == 200


def test_test_ping_and_deliveries_log(client, admin_headers, db, monkeypatch):
    h = admin_headers
    wid = client.post("/api/webhooks", headers=h, json={
        "name": "ping 测试", "url": "http://8.8.8.8/hook",
        "event_types": []}).json()["id"]

    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr("app.webhookbus.httpx.Client", FakeClient)
    r = client.post(f"/api/webhooks/{wid}/test", headers=h)
    assert r.status_code == 200 and r.json()["flush"]["sent"] >= 1
    log = client.get(f"/api/webhooks/{wid}/deliveries", headers=h).json()
    assert log["total"] >= 1
    assert any(i["event"] == "ping" and i["status"] == "ok" for i in log["items"])


def test_emit_whitelist(db):
    from app.webhookbus import emit

    assert emit(db, "todo.create", {}) == 0  # 白名单外忽略
    assert emit(db, "lead.update", {"event": "lead.update"}) >= 0
