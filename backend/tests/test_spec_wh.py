"""SPEC-WH 回归：群机器人 Webhook 渠道（渲染截断 / 三格式 payload / skip 语义 / 端点校验 / 脱敏）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-wh-"), "t.db")
)

import pytest  # noqa: E402
import requests as requests_mod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

TEST_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + "k" * 32

# 本模块专用 ref_type 前缀（全 suite 共享一库，防与其他模块 dedup 撞键）
RT_FMT, RT_SKIP, RT_DEDUP, RT_STATE = "wh-fmt", "wh-skip", "wh-dedup", "wh-state"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


class _FakeResp:
    """requests.post 的最小替身（只需 .json()）。"""

    def __init__(self, data: dict):
        self._data = data

    def json(self) -> dict:
        return self._data


def _set_webhook(s, url: str | None, typ: str | None = None) -> None:
    """直写 settings_kv 两键（notify_webhook_url / notify_webhook_type）。"""
    from app.models import SettingsKV

    for key, value in (("notify_webhook_url", url), ("notify_webhook_type", typ)):
        row = s.get(SettingsKV, key)
        if row is None:
            if value is not None:
                s.add(SettingsKV(key=key, value=value))
        else:
            if value is None:
                s.delete(row)
            else:
                row.value = value
    s.commit()


def _deliver(session, ref_type: str, ref_id: int, title="中标：某某机械", body="成交价 100.5 万元"):
    from app.notify.channels import _deliver_webhook

    return _deliver_webhook(
        session, 1, kind="signal", title=title, body=body,
        ref_type=ref_type, ref_id=ref_id,
    )


# ---------------- 渲染 ----------------

def test_render_group_message_format_and_truncation():
    from app.notify.channels import render_group_message

    msg = render_group_message("中标：某某机械", "成交价 100.5 万元")
    assert msg.startswith("**[vaclead] 中标：某某机械**\n")
    assert "成交价 100.5 万元" in msg
    # 超 3800 字符截断（企微 markdown content 4096 字节限留余量）
    long = render_group_message("t", "b" * 5000)
    assert len(long) == 3800
    assert render_group_message("短", "消息") == "**[vaclead] 短**\n消息"


# ---------------- prefs：未配置默认态（须在配置前断言） ----------------

def test_prefs_webhook_unconfigured_default(client, admin_headers):
    r = client.get("/api/notify-preferences", headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    ch = {c["key"]: c for c in d["channels"]}
    assert "webhook" in ch
    assert ch["webhook"]["label"] == "群机器人"
    assert ch["webhook"]["available"] is False  # 未配置 → available=False
    # 其他渠道保持注册表 enabled 逻辑
    assert ch["in_app"]["available"] is True
    assert d["webhook"] == {
        "url": None, "type": "wecom", "configured": False,
        "platform_base_url": "", "competitor_url": "",
    }


# ---------------- payload 三格式 + 出站参数 ----------------

def test_webhook_payload_three_formats(monkeypatch):
    from app.db import SessionLocal

    captured: list[dict] = []

    def fake_post(url, json=None, timeout=None, proxies=None, **kw):
        captured.append({"url": url, "payload": json,
                         "timeout": timeout, "proxies": proxies})
        return _FakeResp({"errcode": 0, "code": 0})

    monkeypatch.setattr(requests_mod, "post", fake_post)
    msg = "**[vaclead] 中标：某某机械**\n成交价 100.5 万元"
    want = [
        ("wecom", {"msgtype": "markdown", "markdown": {"content": msg}}),
        ("dingtalk", {"msgtype": "markdown",
                      "markdown": {"title": "中标：某某机械", "text": msg}}),
        ("feishu", {"msg_type": "text", "content": {"text": msg}}),
    ]
    with SessionLocal() as s:
        for i, (typ, payload) in enumerate(want):
            _set_webhook(s, TEST_URL, typ)
            out = _deliver(s, RT_FMT, 4410 + i)
            assert out == "ok", (typ, out)
            got = captured[-1]
            assert got["payload"] == payload, typ
            assert got["url"] == TEST_URL
            # SPEC-WH：显式禁代理 + 10s 超时
            assert got["proxies"] == {"http": None, "https": None}
            assert got["timeout"] == 10
        _set_webhook(s, None, None)


# ---------------- skip 语义 ----------------

def test_webhook_skip_unconfigured(monkeypatch):
    from app.db import SessionLocal

    def boom(*a, **kw):  # 未配置应在出站前返回，绝不触网
        raise AssertionError("未配置时不应发起出站请求")

    monkeypatch.setattr(requests_mod, "post", boom)
    with SessionLocal() as s:
        _set_webhook(s, None, None)
        assert _deliver(s, RT_SKIP, 4420) == "skipped:not-configured"


def test_webhook_skip_duplicate(monkeypatch):
    from app.db import SessionLocal
    from app.models import Notification

    monkeypatch.setattr(requests_mod, "post", lambda *a, **kw: _FakeResp({"errcode": 0}))
    with SessionLocal() as s:
        _set_webhook(s, TEST_URL, "wecom")
        # 预置同 (ref_type, ref_id) 通知行（群消息不可轰炸：水位之上再兜一层）
        s.add(Notification(user_id=1, kind="signal", title="已有站内通知",
                           body="b", ref_type=RT_DEDUP, ref_id=4430))
        s.commit()
        assert _deliver(s, RT_DEDUP, 4430) == "skipped:duplicate"
        s.query(Notification).filter(Notification.ref_type == RT_DEDUP).delete()
        s.commit()
        _set_webhook(s, None, None)


# ---------------- monkeypatch requests.post 三态 ----------------

def test_webhook_post_success_failed_timeout(monkeypatch):
    from app.db import SessionLocal
    from app.models import Notification, SettingsKV
    from app.notify.channels import notify_dispatch

    with SessionLocal() as s:
        _set_webhook(s, TEST_URL, "wecom")

        # 1) errcode==0 → ok
        monkeypatch.setattr(requests_mod, "post",
                            lambda *a, **kw: _FakeResp({"errcode": 0, "errmsg": "ok"}))
        assert _deliver(s, RT_STATE, 4441) == "ok"

        # 2) errcode!=0 → failed:{errcode}
        monkeypatch.setattr(requests_mod, "post",
                            lambda *a, **kw: _FakeResp({"errcode": 301006}))
        assert _deliver(s, RT_STATE, 4442) == "failed:301006"

        # 2b) feishu 分支看 code
        _set_webhook(s, TEST_URL, "feishu")
        monkeypatch.setattr(requests_mod, "post",
                            lambda *a, **kw: _FakeResp({"code": 19021}))
        assert _deliver(s, RT_STATE, 4443) == "failed:19021"
        _set_webhook(s, TEST_URL, "wecom")

        # 3) 超时异常直接抛 → notify_dispatch per-channel try 兜为 error: 前缀
        def timeout_post(*a, **kw):
            raise requests_mod.Timeout("dead proxy chain")

        monkeypatch.setattr(requests_mod, "post", timeout_post)
        row = s.get(SettingsKV, "notify_channels")
        if row is None:
            s.add(SettingsKV(key="notify_channels", value='["webhook"]'))
        else:
            row.value = '["webhook"]'
        s.commit()
        results = notify_dispatch(s, 1, kind="signal", title="超时回归", body="b",
                                  ref_type=RT_STATE, ref_id=4444)
        s.commit()
        assert results["webhook"].startswith("error:"), results
        assert results["in_app"] == "ok"  # 其他渠道不受影响

        # 清理（还原偏好 + 通知行 + 配置）
        s.query(SettingsKV).filter(SettingsKV.key == "notify_channels").delete()
        s.query(Notification).filter(Notification.ref_type == RT_STATE).delete()
        _set_webhook(s, None, None)


# ---------------- 端点：PUT 校验 / GET 脱敏 / POST test ----------------

def test_put_webhook_validation_and_prefs_masking(client, admin_headers):
    # 非 https → 422
    assert client.put("/api/notify-webhook", json={"url": "http://qyapi.example.com/hook", "type": "wecom"},
                      headers=admin_headers).status_code == 422
    # type 白名单外 → 422
    assert client.put("/api/notify-webhook", json={"url": TEST_URL, "type": "slack"},
                      headers=admin_headers).status_code == 422

    # 合法写入 → ok
    r = client.put("/api/notify-webhook", json={"url": TEST_URL, "type": "dingtalk"},
                   headers=admin_headers)
    assert r.status_code == 200 and r.json() == {"ok": True}

    # GET prefs：available 翻绿 + URL 脱敏（头 60 尾 12，中段 ***）
    d = client.get("/api/notify-preferences", headers=admin_headers).json()
    ch = {c["key"]: c for c in d["channels"]}
    assert ch["webhook"]["available"] is True
    assert d["webhook"]["configured"] is True
    assert d["webhook"]["type"] == "dingtalk"
    masked = d["webhook"]["url"]
    assert masked == TEST_URL[:60] + "***" + TEST_URL[-12:]
    assert masked != TEST_URL and TEST_URL not in masked  # key 不泄漏

    # 恢复未配置（后续模块/重跑依赖默认态）
    from app.db import SessionLocal
    from app.models import SettingsKV

    with SessionLocal() as s:
        _set_webhook(s, None, None)
    d2 = client.get("/api/notify-preferences", headers=admin_headers).json()
    assert d2["webhook"]["configured"] is False


def test_post_webhook_test_endpoint(client, admin_headers, monkeypatch):
    # 成功：ok True（带 body url/type，先测后存）
    monkeypatch.setattr(requests_mod, "post",
                        lambda *a, **kw: _FakeResp({"errcode": 0}))
    r = client.post("/api/notify-webhook/test", json={"url": TEST_URL, "type": "wecom"},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # 业务失败：仍 200，ok False + failed 前缀
    monkeypatch.setattr(requests_mod, "post",
                        lambda *a, **kw: _FakeResp({"errcode": 301006}))
    r2 = client.post("/api/notify-webhook/test", json={"url": TEST_URL, "type": "wecom"},
                     headers=admin_headers)
    assert r2.status_code == 200
    assert r2.json() == {"ok": False, "detail": "failed:301006"}

    # 网络异常：仍 200 不 500
    def timeout_post(*a, **kw):
        raise requests_mod.Timeout("boom")

    monkeypatch.setattr(requests_mod, "post", timeout_post)
    r3 = client.post("/api/notify-webhook/test", json={"url": TEST_URL, "type": "wecom"},
                     headers=admin_headers)
    assert r3.status_code == 200
    assert r3.json()["ok"] is False

    # 未配置（不带 body、库里无 url）→ ok False
    r4 = client.post("/api/notify-webhook/test", headers=admin_headers)
    assert r4.status_code == 200
    assert r4.json() == {"ok": False, "detail": "skipped:not-configured"}


COMP_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + "c" * 32


def test_webhook_competitor_routing(client, monkeypatch):
    """SPEC-WH3 分群路由：_deliver_webhook 按 ref_type 选群——
    competitor_win → 竞对群；未配竞对群 → 落回默认群。"""
    from app.db import SessionLocal
    from app.models import SettingsKV
    from app.notify.channels import _deliver_webhook

    sent: list[str] = []

    def fake_post(url, json=None, timeout=None, proxies=None, allow_redirects=False):
        sent.append(url)
        return _FakeResp({"errcode": 0})

    monkeypatch.setattr(requests_mod, "post", fake_post)

    def set_kv(s, key: str, value: str | None) -> None:
        row = s.get(SettingsKV, key)
        if row is None:
            if value is not None:
                s.add(SettingsKV(key=key, value=value))
        else:
            if value is None:
                s.delete(row)
            else:
                row.value = value

    with SessionLocal() as s:
        _set_webhook(s, TEST_URL, "wecom")
        set_kv(s, "notify_webhook_competitor_url", COMP_URL)
        s.commit()
        # 竞对 ref → 竞对群
        r1 = _deliver(s, "competitor_win", 91001)
        assert r1 == "ok"
        assert sent[-1].endswith("c" * 32)
        # 普通信号 → 默认群
        r2 = _deliver(s, "signal", 91002)
        assert r2 == "ok"
        assert sent[-1].endswith("k" * 32)
        # 清除竞对群 → 竞对 ref 落回默认群
        set_kv(s, "notify_webhook_competitor_url", None)
        s.commit()
        r3 = _deliver(s, "competitor_win", 91003)
        assert r3 == "ok"
        assert sent[-1].endswith("k" * 32)
