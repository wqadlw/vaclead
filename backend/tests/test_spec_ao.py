"""SPEC-AO 回归：MCP 风格工具端点 + 推荐一键转线索。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ao-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    SettingsKV,
    Todo,
    UserApiKey,
    utcnow,
)
import hashlib  # noqa: E402

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact, Todo]


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


@pytest.fixture(scope="module")
def api_key(client, admin_headers):
    """创建 rw APIKey（MCP 调用为 POST 传输，read scope 在方法层即被 J1 拦截）。"""
    r = client.post("/api/api-keys", json={"name": "ao-agent", "scopes": "rw"},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    return r.json()["key"]


def _seed(name: str, phone: str | None = None) -> int:
    with SessionLocal() as s:
        e = Enterprise(name=name, source="poi", city="苏州", industry_key="coating",
                       industry_name="coating")
        s.add(e)
        s.flush()
        if phone:
            s.add(Contact(enterprise_id=e.id, name="地图公开电话", role="poi",
                          title=phone, phones_json=f'["{phone}"]', source="amap"))
        s.commit()
        return e.id


# ---------------- AO1 MCP 工具 ----------------
def test_mcp_requires_api_key(client, admin_headers):
    r = client.get("/api/mcp/tools", headers=admin_headers)
    assert r.status_code == 401, "Agent 通道必须 X-Api-Key"


def test_mcp_tools_list_and_call(client, admin_headers, api_key):
    h = {"X-Api-Key": api_key}
    d = client.get("/api/mcp/tools", headers=h).json()
    names = [t["name"] for t in d["tools"]]
    assert set(names) >= {"search_enterprises", "get_enterprise", "list_signals",
                          "get_recommendations"}
    eid = _seed("MCP 搜索测试镀膜有限公司", "0512-99990000")
    r = client.post("/api/mcp/call",
                    json={"tool": "search_enterprises", "args": {"q": "MCP 搜索测试", "limit": 5}},
                    headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert any(x["id"] == eid for x in r.json()["data"])
    r2 = client.post("/api/mcp/call", json={"tool": "get_enterprise", "args": {"eid": eid}},
                     headers=h)
    body = r2.json()
    assert body["ok"] and body["data"]["name"].startswith("MCP")
    # rw scope 电话明文
    assert body["data"]["contacts"][0]["phone"] == "0512-99990000"
    r3 = client.post("/api/mcp/call", json={"tool": "list_signals", "args": {"limit": 5}},
                     headers=h)
    assert r3.json()["ok"] is True
    r4 = client.post("/api/mcp/call", json={"tool": "get_recommendations", "args": {}},
                     headers=h)
    assert r4.json()["ok"] is True
    # 未知工具
    r5 = client.post("/api/mcp/call", json={"tool": "nope", "args": {}}, headers=h)
    assert r5.json()["ok"] is False


# ---------------- AO2 一键转线索 ----------------
def test_convert_creates_lead_and_todo(client, admin_headers):
    eid = _seed("转化测试目标企业", "0512-88887777")
    r = client.post(f"/api/recommendations/{eid}/convert", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True and body["lead_id"] > 0
    from app.models import Lead

    with SessionLocal() as s:
        lead = s.get(Lead, body["lead_id"])
        assert lead is not None and lead.status == "following"
        todos = s.query(Todo).filter(Todo.enterprise_id == eid,
                                     Todo.done == 0).all()
        assert any("首联" in t.title for t in todos), "应自动建首联待办"
    # 幂等：再转复用
    r2 = client.post(f"/api/recommendations/{eid}/convert", headers=admin_headers)
    assert r2.json()["created"] is False and r2.json()["lead_id"] == body["lead_id"]


def _lead_ref():
    from app.models import Lead

    return Lead
