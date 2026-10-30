"""SPEC-J Wave 2 回归：联系人中心(J2)/产品报价(J3)/留资表单(J5)。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-j2-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_enterprise(client, headers, name: str) -> int:
    row = f"name,province,city,business_scope,registered_capital_wan,industry_key\n{name},江苏省,苏州市,真空设备制造,800,semiconductor\n"
    r = client.post(
        "/api/import/enterprises", files={"file": ("m.csv", row.encode(), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    from urllib.parse import quote

    items = client.get(f"/api/enterprises?q={quote(name)}&page_size=5", headers=headers).json()["items"]
    return next(e["id"] for e in items if e["name"] == name)


# ---------------- J2 联系人中心 ----------------

def test_notice_email_extraction_regex():
    """公告正文邮箱抽取：正规邮箱命中、无邮箱返回 None。"""
    from app.collectors.ccgp import fetch_notice_detail, _DETAIL_FIELDS
    import re as _re

    email_pat = dict(_DETAIL_FIELDS)["email"]
    text = "项目联系人 张工 项目联系电话 0512-66668888 采购单位联系方式 zhangsan@pku.edu.cn 其他补充"
    m = _re.search(email_pat, text)
    assert m and m.group(1) == "zhangsan@pku.edu.cn"
    assert _re.search(email_pat, "项目联系电话 0512-66668888 无邮箱") is None


def test_contact_center_fields_flow(client, admin_headers):
    """回填链路写入 email/decision_role/verify_state → intel 接口带出。"""
    from app.db import SessionLocal
    from app.models import Contact, Enterprise

    with SessionLocal() as s:
        ent = Enterprise(name="联系人中心回归真空设备有限公司", source="ccgp_buyer")
        s.add(ent)
        s.flush()
        s.add(Contact(
            enterprise_id=ent.id, name="王采购", role="采购联系人",
            title="0512-66668888", email="wangcai@buyer.edu.cn",
            decision_role="decision_maker", verify_state="verified",
            source="ccgp", evidence_claim="政采公告采购单位联系方式",
        ))
        s.commit()
        eid = ent.id

    items = client.get(f"/api/enterprises/{eid}/contacts", headers=admin_headers).json()["items"]
    row = next(i for i in items if i["name"] == "王采购")
    assert row["email"] == "wangcai@buyer.edu.cn"
    assert row["decision_role"] == "decision_maker"
    assert row["verify_state"] == "verified"

    with SessionLocal() as s:
        s.query(Contact).filter(Contact.enterprise_id == eid).delete()
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()


def test_rule_condition_has_email_decision(client, admin_headers):
    """has_email_decision 条件：有验证邮箱命中、无邮箱走 else。"""
    from app.db import SessionLocal
    from app.jobs import _rule_matches
    from app.models import AutomationRule, Contact, Enterprise, Signal

    with SessionLocal() as s:
        rule = AutomationRule(
            name="邮箱决策人回归规则", cond_signal_type="tender",
            cond_has_email_decision=1, action_type="notify",
        )
        s.add(rule)
        s.flush()

        ent_with = Enterprise(name="有邮箱决策回归有限公司", source="ccgp")
        s.add(ent_with)
        s.flush()
        s.add(Contact(enterprise_id=ent_with.id, name="李决策", role="采购联系人",
                      email="li@ent.cn", verify_state="verified"))
        sig1 = Signal(enterprise_id=ent_with.id, type="tender", title="触达回归信号甲",
                      url="", base_score=50)
        s.add(sig1)

        ent_without = Enterprise(name="无邮箱决策回归有限公司", source="ccgp")
        s.add(ent_without)
        s.flush()
        sig2 = Signal(enterprise_id=ent_without.id, type="tender", title="触达回归信号乙",
                      url="", base_score=50)
        s.add(sig2)
        s.commit()

        assert _rule_matches(rule, sig1, ent_with, s) is True
        assert _rule_matches(rule, sig2, ent_without, s) is False

        eid1, eid2 = ent_with.id, ent_without.id
        s.query(Signal).filter(Signal.enterprise_id.in_([eid1, eid2])).delete()
        s.query(Contact).filter(Contact.enterprise_id == eid1).delete()
        s.query(Enterprise).filter(Enterprise.id.in_([eid1, eid2])).delete()
        s.query(AutomationRule).filter(AutomationRule.id == rule.id).delete()
        s.commit()


# ---------------- J3 产品与报价 ----------------

def test_product_crud(client, admin_headers):
    r = client.post("/api/products", json={"name": "分子泵 FF-160", "model": "FF-160/620",
                                           "spec": "极限真空 5e-8 Pa", "price": 12.8,
                                           "price_unit": "台"},
                    headers=admin_headers)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    items = client.get("/api/products", params={"q": "分子泵"}, headers=admin_headers).json()["items"]
    assert any(p["id"] == pid for p in items)
    r = client.patch(f"/api/products/{pid}", json={"name": "分子泵 FF-160", "price": 13.5,
                                                   "price_unit": "台"},
                     headers=admin_headers)
    assert r.status_code == 200
    r = client.delete(f"/api/products/{pid}", headers=admin_headers)
    assert r.status_code == 200
    items = client.get("/api/products", params={"q": "分子泵"}, headers=admin_headers).json()["items"]
    assert not any(p["id"] == pid for p in items)  # 软删后列表不见


def test_quote_lifecycle_total_and_status_machine(client, admin_headers):
    month_eid = _mk_enterprise(client, admin_headers, "报价单回归理工大学")
    lead_id = client.post(f"/api/enterprises/{month_eid}/lead", headers=admin_headers).json()["id"]

    # 报价：2×13.5 + 1×4.2 = 31.2（服务端重算，不信前端）
    r = client.post("/api/quotes", json={
        "lead_id": lead_id, "valid_until": "2026-12-31", "remark": "含安装调试",
        "items": [
            {"name": "分子泵 FF-160", "qty": 2, "price": 13.5, "price_unit": "台"},
            {"name": "前级泵 2XZ-15", "qty": 1, "price": 4.2, "price_unit": "台"},
        ],
    }, headers=admin_headers)
    assert r.status_code == 201, r.text
    qid = r.json()["id"]
    assert r.json()["total_wan"] == 31.2

    # 状态机：draft → sent → won
    r = client.patch(f"/api/quotes/{qid}/status", json={"status": "sent"}, headers=admin_headers)
    assert r.status_code == 200
    r = client.patch(f"/api/quotes/{qid}/status", json={"status": "won"}, headers=admin_headers)
    assert r.status_code == 200
    # won 终态无出边
    r = client.patch(f"/api/quotes/{qid}/status", json={"status": "draft"}, headers=admin_headers)
    assert r.status_code == 422
    # 详情快照
    detail = client.get(f"/api/quotes/{qid}", headers=admin_headers).json()
    assert detail["status"] == "won"
    assert detail["enterprise_name"] == "报价单回归理工大学"
    assert len(detail["items"]) == 2 and detail["items"][0]["subtotal"] == 27.0


# ---------------- J5 留资表单 ----------------

def test_lead_form_capture_flow(client, admin_headers):
    r = client.post("/api/lead-forms", json={"name": "官网留资"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    token = r.json()["token"]

    # 公开提交（无鉴权）→ 归一建档 + 建线索
    r = client.post(f"/api/public/lead-capture/{token}", json={
        "company": "留资捕获真空科技有限公司", "contact_name": "陈工",
        "phone": "13800138000", "need": "想了解分子泵报价",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["enterprise_id"] and body["lead_id"]

    # 撞单：同企业再提交 → 复用已有线索
    r = client.post(f"/api/public/lead-capture/{token}", json={
        "company": "留资捕获真空科技有限公司", "phone": "13900139000",
    })
    assert r.status_code == 201
    assert r.json()["lead_id"] == body["lead_id"]

    # honeypot：website 字段被填 → 静默丢弃
    r = client.post(f"/api/public/lead-capture/{token}", json={
        "company": "机器人垃圾公司", "phone": "13700137000", "website": "http://spam.io",
    })
    assert r.status_code == 201
    subs = client.get(f"/api/lead-forms/{r0 := 0}" if False else
                      f"/api/lead-forms/{_form_id(client, admin_headers, '官网留资')}/submissions",
                      headers=admin_headers).json()["items"]
    assert not any(s["company"] == "机器人垃圾公司" for s in subs)
    assert any(s["company"] == "留资捕获真空科技有限公司" for s in subs)

    # 缺电话且缺邮箱 → 422
    r = client.post(f"/api/public/lead-capture/{token}", json={"company": "无名氏公司"})
    assert r.status_code == 422


def _form_id(client, headers, name: str) -> int:
    items = client.get("/api/lead-forms", headers=headers).json()["items"]
    return next(f["id"] for f in items if f["name"] == name)
