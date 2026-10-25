"""SPEC-E E3：自动化规则 API + 引擎 + todos + analytics 测试补全（此前零覆盖）。"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vaclead-e3-")
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


# ---- automation CRUD ----
def test_rule_crud_and_chain_persist(client, admin_headers):
    r = client.post(
        "/api/automation-rules",
        json={
            "name": "E3链式规则",
            "cond_signal_type": "tender_win",
            "actions": ["notify", "create_todo"],
            "else_actions": ["notify"],
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["actions"] == ["notify", "create_todo"]
    assert r.json()["else_actions"] == ["notify"]

    # patch 保水位重写链
    r2 = client.patch(
        f"/api/automation-rules/{rid}",
        json={"name": "E3链式规则", "cond_signal_type": "tender_win",
              "action_type": "notify", "actions": ["notify"]},
        headers=admin_headers,
    )
    assert r2.json()["actions"] == ["notify"]

    # 非法动作 422
    bad = client.post(
        "/api/automation-rules",
        json={"name": "bad", "action_type": "explode"},
        headers=admin_headers,
    )
    assert bad.status_code == 422

    assert client.delete(f"/api/automation-rules/{rid}", headers=admin_headers).json()["ok"]


def _mk_signal(client, admin_headers, name: str, url: str) -> int:
    from app.db import SessionLocal
    from app.models import Enterprise, Signal

    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(Enterprise.name == name).first()
        if ent is None:
            ent = Enterprise(name=name, source="ggzy")
            s.add(ent)
            s.flush()
        s.add(Signal(
            enterprise_id=ent.id, type="tender_win", title="E3信号",
            url=url, pub_date="2026-10-26", base_score=70.0,
        ))
        s.commit()
        eid = ent.id
        sid = s.query(Signal).filter(Signal.url == url).first().id
    return eid, sid


def test_dry_run_zero_write(client, admin_headers):
    eid, sid = _mk_signal(client, admin_headers, "E3干跑企业有限公司", "e3-dry-1")
    from app.db import SessionLocal
    from app.models import AutomationRule, WorkflowRun

    with SessionLocal() as s:
        rule = AutomationRule(name="E3干跑", cond_signal_type="tender_win", action_type="notify")
        s.add(rule)
        s.commit()
        rid = rule.id
    r = client.post(f"/api/automation-rules/{rid}/dry-run?limit=5", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["would_hit"] >= 1
    assert any(h["enterprise_name"] == "E3干跑企业有限公司" for h in d["hits"])
    # 零写验证：无 workflow_run
    with SessionLocal() as s:
        assert s.query(WorkflowRun).filter(WorkflowRun.rule_id == rid).count() == 0
        s.query(AutomationRule).filter(AutomationRule.id == rid).delete()
        s.commit()


# ---- todos ----
def test_todo_crud_and_double_complete(client, admin_headers):
    """E1-A2 回归：同标题完成→重建→再完成 不再撞约束。"""
    r = client.post("/api/todos", json={"title": "E3待办A", "due_date": "2026-10-28"},
                    headers=admin_headers)
    assert r.status_code == 201
    tid = r.json()["id"]
    # 完成
    assert client.patch(f"/api/todos/{tid}/done", headers=admin_headers).json()["done"] == 1
    # 重建同标题
    r2 = client.post("/api/todos", json={"title": "E3待办A"}, headers=admin_headers)
    assert r2.status_code == 201
    tid2 = r2.json()["id"]
    # 再完成——修复前此处 IntegrityError
    done = client.patch(f"/api/todos/{tid2}/done", headers=admin_headers)
    assert done.status_code == 200 and done.json()["done"] == 1


def test_todo_scopes(client, admin_headers):
    open_items = client.get("/api/todos?scope=open", headers=admin_headers).json()
    assert "items" in open_items
    overdue = client.get("/api/todos?scope=overdue", headers=admin_headers).json()
    assert "items" in overdue


# ---- analytics ----
def test_dashboard_summary_shape(client, admin_headers):
    r = client.get("/api/dashboard/summary", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    for k in ("kpis", "trend", "sources", "funnel"):
        assert k in d
    assert len(d["trend"]) == 30
    assert any(f["status"] == "new" for f in d["funnel"]) or all(
        f["value"] == 0 for f in d["funnel"]
    )


# ---- engine: chain + else branch + watermark ----
def test_engine_chain_and_else_execution(client, admin_headers):
    from app.db import SessionLocal
    from app.models import AutomationRule, Enterprise, Signal, Todo, WorkflowRun

    with SessionLocal() as s:
        # 主企业：匹配主链
        ent_hit = Enterprise(name="E3链命中公司", source="ccgp_buyer")
        # else 企业：不匹配（行业不同）
        ent_else = Enterprise(name="E3链否则公司", source="csv")
        s.add_all([ent_hit, ent_else])
        s.flush()
        s.add(Signal(enterprise_id=ent_hit.id, type="tender", title="else测试",
                     url="e3-else-1", base_score=50.0))
        s.add(Signal(enterprise_id=ent_else.id, type="tender", title="else测试",
                     url="e3-else-2", base_score=50.0))
        rule = AutomationRule(
            name="E3分支规则",
            cond_signal_type="tender_win",  # 两信号都是 tender → 都走 else
            action_type="notify",
            action_params='{"actions": ["notify"], "else_actions": ["tag_competitor"]}',
        )
        s.add(rule)
        s.commit()
        rid = rule.id
        hit_eid, else_eid = ent_hit.id, ent_else.id

    from app.jobs import run_automation_rules

    with SessionLocal() as s:
        stats = run_automation_rules(s)
        assert stats["actions"] >= 2
        # 两信号走 else 链 → tag_competitor 各一行
        runs = s.query(WorkflowRun).filter(WorkflowRun.rule_id == rid).all()
        else_runs = [r for r in runs if r.action_type == "tag_competitor"]
        assert len(else_runs) >= 2
        # else 企业被打了同行标
        e = s.get(Enterprise, else_eid)
        assert e.is_competitor == 1
        # 清理
        s.query(WorkflowRun).filter(WorkflowRun.rule_id == rid).delete()
        s.query(AutomationRule).filter(AutomationRule.id == rid).delete()
        s.query(Enterprise).filter(Enterprise.id.in_([hit_eid, else_eid])).delete(
            synchronize_session=False
        )
        s.commit()
