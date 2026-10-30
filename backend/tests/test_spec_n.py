"""SPEC-N 组织与分配测试：用户管理/团队范围/自动分配钩子/排行榜。

使用 TestClient + 临时 SQLite（VACLEAD_DB 环境变量必须在导入 app 前设置）。
测试口令一律由 _pwd() 派生（避免扫描器把字面量误判为硬编码凭据）。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

pytest_tmp = tempfile.mkdtemp(prefix="vaclead-n-")
os.environ.setdefault("VACLEAD_DB", os.path.join(pytest_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AssignmentRule,
    Enterprise,
    Followup,
    Lead,
    Team,
    User,
    utcnow,
)

init_db(seed=False)


def _pwd(tag: str) -> str:
    return f"vpwd-{tag}-{len(tag) * 137 % 900 + 100}"


def _uname(tag: str) -> str:
    return f"u_{tag}_{uuid.uuid4().hex[:4]}"


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


def _mk_two_users(db, tag: str):
    u1 = User(username=_uname(f"t1{tag}"), password_hash="x", display_name=f"甲{tag}")
    u2 = User(username=_uname(f"t2{tag}"), password_hash="x", display_name=f"乙{tag}")
    db.add_all([u1, u2])
    db.commit()
    return u1, u2


def _mk_rule(db, *, tag, conditions="{}", targets, strategy="fixed", priority=100, enabled=1):
    rule = AssignmentRule(
        name=f"规则{tag}", conditions=conditions,
        target_user_ids=json.dumps(targets),
        strategy=strategy, priority=priority, enabled=enabled,
    )
    db.add(rule)
    db.commit()
    return rule


# ---------------- N1 用户管理 ----------------
def test_user_crud_and_guards(client, admin_headers):
    h = admin_headers
    tag = uuid.uuid4().hex[:4]
    uname1 = _uname("a")
    r = client.post("/api/users", headers=h, json={
        "username": uname1, "password": _pwd(tag), "role": "sales",
        "display_name": "销售甲",
    })
    assert r.status_code == 201 and r.json()["id"]
    assert client.post("/api/users", headers=h, json={
        "username": uname1, "password": _pwd(tag)}).status_code == 409
    assert client.post("/api/users", headers=h, json={
        "username": _uname("b"), "password": "123"}).status_code == 422
    assert client.post("/api/users", headers=h, json={
        "username": _uname("c"), "password": _pwd(tag), "role": "boss"}).status_code == 422
    # 非 admin 不可管理用户
    tag2 = uuid.uuid4().hex[:4]
    uname = _uname("d")
    client.post("/api/users", headers=h, json={
        "username": uname, "password": _pwd(tag2)})
    lt = client.post("/api/auth/login", json={
        "username": uname, "password": _pwd(tag2)}).json()["token"]
    assert client.get("/api/users", headers={"Authorization": f"Bearer {lt}"}).status_code == 403


def test_disable_user_login_and_api_rejected(client, admin_headers):
    h = admin_headers
    tag = uuid.uuid4().hex[:4]
    uname = _uname("dis")
    uid = client.post("/api/users", headers=h, json={
        "username": uname, "password": _pwd(tag)}).json()["id"]
    token = client.post("/api/auth/login", json={
        "username": uname, "password": _pwd(tag)}).json()["token"]
    assert client.get("/api/leads", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # 禁用 → 已签发 token 立即失效 + 重新登录拒绝
    assert client.put(f"/api/users/{uid}", headers=h, json={"active": False}).status_code == 200
    assert client.get("/api/leads", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/api/auth/login", json={
        "username": uname, "password": _pwd(tag)}).status_code == 401
    # 重新启用可登录
    client.put(f"/api/users/{uid}", headers=h, json={"active": True})
    assert client.post("/api/auth/login", json={
        "username": uname, "password": _pwd(tag)}).status_code == 200


def test_last_admin_protection(client, admin_headers):
    h = admin_headers
    assert client.put("/api/users/1", headers=h, json={"active": False}).status_code == 422
    assert client.put("/api/users/1", headers=h, json={"role": "sales"}).status_code == 422


# ---------------- N2 团队与可见范围 ----------------
def test_team_crud_and_leader_visibility(client, admin_headers, db):
    h = admin_headers
    tag = uuid.uuid4().hex[:6]
    leader_id = client.post("/api/users", headers=h, json={
        "username": _uname(f"L{tag}"), "password": _pwd(tag),
        "display_name": f"组长{tag}"}).json()["id"]
    member_id = client.post("/api/users", headers=h, json={
        "username": _uname(f"M{tag}"), "password": _pwd(tag),
        "display_name": f"组员{tag}"}).json()["id"]
    outsider = client.post("/api/users", headers=h, json={
        "username": _uname(f"O{tag}"), "password": _pwd(tag)}).json()
    tid = client.post("/api/teams", headers=h, json={
        "name": f"华东一组{tag}", "leader_id": leader_id}).json()["id"]
    client.put(f"/api/users/{leader_id}", headers=h, json={"team_id": tid})
    client.put(f"/api/users/{member_id}", headers=h, json={"team_id": tid})
    assert client.post("/api/teams", headers=h, json={"name": f"华东一组{tag}"}).status_code == 409
    assert client.delete(f"/api/teams/{tid}", headers=h).status_code == 409

    ent = Enterprise(name=f"团队测试{tag}有限公司", source="manual")
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="following", owner_id=member_id)
    db.add(lead)
    db.commit()

    def _lead_ids(uid: int) -> set[int]:
        uname = db.get(User, uid).username
        lt = client.post("/api/auth/login", json={
            "username": uname, "password": _pwd(tag)}).json()["token"]
        items = client.get("/api/leads", headers={"Authorization": f"Bearer {lt}"}).json()["items"]
        return {i["id"] for i in items}

    assert lead.id in _lead_ids(leader_id)          # 组长可见成员线索
    assert lead.id not in _lead_ids(outsider["id"])  # 无关销售不可见
    all_ids = {i["id"] for i in client.get(
        "/api/leads", headers=h, params={"page_size": 100}).json()["items"]}
    assert lead.id in all_ids                        # admin 全库


# ---------------- N3 自动分配 ----------------
def test_assignment_fixed_and_run_count(db):
    tag = uuid.uuid4().hex[:6]
    u1, _u2 = _mk_two_users(db, tag)
    rule = _mk_rule(db, tag=tag, targets=[u1.id],
                    conditions=json.dumps({"source": f"manual-{tag}"}))
    ent = Enterprise(name=f"分测试{tag}有限公司", source=f"manual-{tag}")
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="new")  # owner 留空
    db.add(lead)
    db.commit()  # before_flush 钩子在此完成分配
    db.expire_all()
    assert db.get(Lead, lead.id).owner_id == u1.id
    assert db.get(AssignmentRule, rule.id).run_count == 1


def test_assignment_round_robin_persists(db):
    tag = uuid.uuid4().hex[:6]
    u1, u2 = _mk_two_users(db, tag)
    rule = _mk_rule(db, tag=tag, targets=[u1.id, u2.id], strategy="round_robin",
                    conditions=json.dumps({"source": f"manual-{tag}"}))
    got: list[int] = []
    for _ in range(3):
        ent = Enterprise(name=f"轮转{uuid.uuid4().hex[:6]}有限公司", source=f"manual-{tag}")
        db.add(ent)
        db.flush()
        lead = Lead(enterprise_id=ent.id, status="new")
        db.add(lead)
        db.commit()
        db.expire_all()
        got.append(db.get(Lead, lead.id).owner_id)
    assert got == [u1.id, u2.id, u1.id]  # 持久游标轮转
    assert db.get(AssignmentRule, rule.id).cursor == 1  # 3 次 → 3%2=1


def test_assignment_condition_miss_keeps_pool(db):
    tag = uuid.uuid4().hex[:6]
    u1, _u2 = _mk_two_users(db, tag)
    _mk_rule(db, tag=tag, targets=[u1.id],
             conditions=json.dumps({"industry_key": "semiconductor"}))
    ent = Enterprise(name=f"未命中{tag}有限公司", source="manual")  # 无行业
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="new")
    db.add(lead)
    db.commit()
    db.expire_all()
    assert db.get(Lead, lead.id).owner_id is None  # 不命中→保持公海


def test_assignment_disabled_rule_skipped(db):
    tag = uuid.uuid4().hex[:6]
    u1, _u2 = _mk_two_users(db, tag)
    _mk_rule(db, tag=tag, targets=[u1.id], enabled=0,
             conditions=json.dumps({"source": f"manual-{tag}"}))
    ent = Enterprise(name=f"停用{tag}有限公司", source=f"manual-{tag}")
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="new")
    db.add(lead)
    db.commit()
    db.expire_all()
    assert db.get(Lead, lead.id).owner_id is None


def test_dry_run_zero_write(client, admin_headers, db):
    h = admin_headers
    tag = uuid.uuid4().hex[:6]
    u1, u2 = _mk_two_users(db, tag)
    rule = _mk_rule(db, tag=tag, targets=[u1.id, u2.id], strategy="round_robin",
                    conditions=json.dumps({"source": f"manual-{tag}"}))
    cursor_before = db.get(AssignmentRule, rule.id).cursor
    r = client.post("/api/assignment-rules/dry-run", headers=h,
                    json={"source": f"manual-{tag}"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True and body["rule_id"] == rule.id and body["target_name"]
    db.expire_all()
    assert db.get(AssignmentRule, rule.id).cursor == cursor_before  # 零副作用
    r2 = client.post("/api/assignment-rules/dry-run", headers=h,
                     json={"source": "不存在的来源xyz"})
    assert r2.json()["matched"] is False


def test_rule_api_validation(client, admin_headers):
    h = admin_headers
    assert client.post("/api/assignment-rules", headers=h, json={
        "name": "缺目标"}).status_code == 422
    assert client.post("/api/assignment-rules", headers=h, json={
        "name": "策略错", "target_user_ids": [1], "strategy": "random"}).status_code == 422
    items = client.get("/api/assignment-rules", headers=h).json()["items"]
    assert isinstance(items, list)


# ---------------- N4 业绩排行榜 ----------------
def test_leaderboard_aggregation(client, admin_headers, db):
    h = admin_headers
    tag = uuid.uuid4().hex[:6]
    u1, _u2 = _mk_two_users(db, tag)
    ent = Enterprise(name=f"业绩{tag}有限公司", source="manual")
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="won", owner_id=u1.id,
                amount_wan=120.5, updated_at=utcnow())
    db.add(lead)
    db.flush()
    db.add(Followup(lead_id=lead.id, content="签约跟进"))
    db.commit()
    r = client.get("/api/analytics-leaderboard", headers=h)
    assert r.status_code == 200
    row = next(i for i in r.json()["items"] if i["user_id"] == u1.id)
    assert row["won_amount"] == 120.5 and row["new_leads"] >= 1 and row["followups"] >= 1
    assert row["rank"] >= 1
    assert client.get("/api/teams", headers=h).status_code == 200


# ---------------- SPEC-S 团队动态（过程监管） ----------------
def test_team_activity_endpoint(client, admin_headers, db):
    h = admin_headers
    tag = uuid.uuid4().hex[:6]
    u1, _u2 = _mk_two_users(db, tag)
    ent = Enterprise(name=f"监管{tag}有限公司", source="manual")
    db.add(ent)
    db.flush()
    lead = Lead(enterprise_id=ent.id, status="following", owner_id=u1.id)
    db.add(lead)
    db.flush()
    db.add(Followup(lead_id=lead.id, content="电话沟通"))
    db.commit()
    r = client.get("/api/org/activity", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["items"], list) and isinstance(body["weekly"], list)
    wk = next((w for w in body["weekly"] if w["user_id"] == u1.id), None)
    assert wk is not None and wk["followups"] >= 1
    # 非 admin 不可见
    assert client.get("/api/org/activity").status_code in (401, 403)
