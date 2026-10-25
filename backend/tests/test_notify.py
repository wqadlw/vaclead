"""通知渠道层测试（吸收 Novu 偏好模型 + EmailEngine stub）。"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vaclead-notify-")
os.environ.setdefault("VACLEAD_DB", os.path.join(_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_channel_registry_and_default_prefs(client, admin_headers):
    r = client.get("/api/notify-preferences", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    keys = [c["key"] for c in d["channels"]]
    assert keys == ["in_app", "email", "wechat"]
    assert d["preferences"] == ["in_app"]  # 默认只站内


def test_put_prefs_persists_and_validates(client, admin_headers):
    r = client.put("/api/notify-preferences", json={"channels": ["in_app", "email"]},
                   headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["preferences"] == ["in_app", "email"]
    # 回读
    r2 = client.get("/api/notify-preferences", headers=admin_headers)
    assert r2.json()["preferences"] == ["in_app", "email"]
    # 非法渠道被滤、全非法 422
    assert client.put("/api/notify-preferences", json={"channels": ["bogus", "in_app"]},
                      headers=admin_headers).json()["preferences"] == ["in_app"]
    assert client.put("/api/notify-preferences", json={"channels": ["bogus"]},
                      headers=admin_headers).status_code == 422
    # 恢复默认
    client.put("/api/notify-preferences", json={"channels": ["in_app"]}, headers=admin_headers)


def test_dispatch_in_app_always_and_stub_channels(client, admin_headers):
    from app.db import SessionLocal
    from app.notify.channels import notify_dispatch
    from app.models import Notification, SettingsKV

    with SessionLocal() as s:
        s.query(SettingsKV).filter(SettingsKV.key == "notify_channels").delete()
        s.commit()
        results = notify_dispatch(
            s, 1, kind="test", title="渠道测试", body="内容",
            ref_type="custom", ref_id=1,
        )
        s.commit()
        assert results["in_app"] == "ok"
        n = s.query(Notification).filter(
            Notification.user_id == 1, Notification.title == "渠道测试"
        ).first()
        assert n is not None
        # 未配置 emailengine 时 email 渠道应跳过而非报错（ref_id 区分开唯一约束）
        notify_dispatch(
            s, 1, kind="test", title="t2", body="b", ref_type="custom", ref_id=2,
        )
        s.add(SettingsKV(key="notify_channels", value='["in_app","email"]'))
        s.commit()
        r3 = notify_dispatch(s, 1, kind="test", title="t3", body="b",
                             ref_type="custom", ref_id=3)
        assert r3["email"] == "skipped:not-configured"
        assert r3["in_app"] == "ok"
        # 还原
        s.query(SettingsKV).filter(SettingsKV.key == "notify_channels").delete()
        s.commit()


def test_emailengine_stub_skips_unconfigured(client, admin_headers):
    from app.db import SessionLocal
    from app.notify.emailengine_client import is_configured, list_messages, submit

    with SessionLocal() as s:
        assert is_configured(s) is False
        assert submit(s, to="x@y.z", subject="s", text="t") is False
        assert list_messages(s) == []
