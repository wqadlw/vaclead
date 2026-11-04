"""SPEC-AH 回归：联系人扩池（供应商公告挖采购人）+ 电话规范化。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ah-"), "t.db")
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
    Signal,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Signal, Enterprise, Contact]


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


# ---------------- AH2 电话抽取 ----------------
def test_extract_phones_patterns():
    from app.phones import extract_phones

    text = "联系人：张工 电话：010-68290510、010—68290550；手机 13912345678；项目编号 8038"
    phones = extract_phones(text)
    assert "010-68290510" in phones and "010-68290550" in phones
    assert "13912345678" in phones
    # 分机
    assert "021-62478656-8102" in extract_phones("电话 021-62478656-8102")
    # 项目编号/纯数字不误抽
    assert extract_phones("项目编号 8038；数量 500") == []
    assert extract_phones(None) == []
    # 去重保序
    assert extract_phones("13912345678 13912345678 010-1111111") == ["13912345678", "010-1111111"]


def test_contact_phones_migration():
    """存量 title 槽位号码迁移到 phones_json（幂等）。"""
    from app.api.tasks import backfill_notice_contacts

    with SessionLocal() as s:
        ent = Enterprise(name="迁移测试研究院", source="ccgp_buyer")
        s.add(ent)
        s.flush()
        c = Contact(enterprise_id=ent.id, name="王工", role="project_contact",
                    title="010-63509799、13912345678")
        s.add(c)
        s.commit()
        cid = c.id
    # 触发迁移（回填入口自愈，池空即走迁移）
    backfill_notice_contacts(type("B", (), {"limit": 1})(), SessionLocal(), None)
    with SessionLocal() as s:
        c = s.get(Contact, cid)
        phones = __import__("json").loads(c.phones_json)
        assert "010-63509799" in phones and "13912345678" in phones


# ---------------- AH1 双池回填（注入详情，离线） ----------------
class _Resp:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200


DETAIL_HTML = """
项目联系人 李四 项目联系电话 0755-26651111、13800138000
采购单位 深圳采购管理测试研究院 行政区域 广东
采购单位地址 南山区科技园 采购单位联系方式 0755-12340001
代理机构 中招代理公司 代理机构地址 福田区 代理机构联系方式 0755-82221111
联系邮箱 buyer@test.gov.cn
"""


def test_supplier_notice_mines_buyer(monkeypatch):
    """供应商公告的采购人联系方式应归挂到采购人（建档），不挂供应商。"""
    from app.api.tasks import backfill_notice_contacts
    from app.collectors import ccgp as ccgp_mod

    def fake_detail(url: str):
        return {
            "buyer_name": "湖北矿山机械测试买家有限公司",
            "buyer_address": "武汉市东湖新技术开发区测试路 1 号",
            "buyer_contact": "采购人联系人 刘主任 027-87654321",
            "project_contact": "李四",
            "project_phone": "0755-26651111、13800138000",
            "agency_name": "中招代理公司",
            "agency_contact": "0755-82221111",
            "region": "湖北",
            "email": "buyer@test.gov.cn",
        }

    monkeypatch.setattr(ccgp_mod, "fetch_notice_detail", fake_detail)
    # 池 A 无买家待回填、池 B 一家供应商
    with SessionLocal() as s:
        sup = Enterprise(name="湖北中标供应商测试有限公司", source="ccgp")
        s.add(sup)
        s.flush()
        s.add(Signal(enterprise_id=sup.id, type="tender_win",
                     title="某单位设备采购中标公告", url="http://www.ccgp.gov.cn/notice/1.htm",
                     base_score=40, created_at=utcnow()))
        s.commit()
    result = backfill_notice_contacts(type("B", (), {"limit": 10})(), SessionLocal(), None)
    assert result["buyer_created"] == 1
    with SessionLocal() as s:
        buyer = s.query(Enterprise).filter(
            Enterprise.source == "ccgp_buyer",
            Enterprise.name.like("%矿山机械测试买家%"),
        ).first()
        assert buyer is not None, "采购人应被建档"
        # 采购人名下应有带规范号码的联系人
        from app.phones import load_phones
        contacts = s.query(Contact).filter(Contact.enterprise_id == buyer.id).all()
        all_phones = [p for c in contacts for p in load_phones(c.phones_json)]
        assert "027-87654321" in all_phones and "13800138000" in all_phones
        # evidence 可溯源到供应商公告
        assert any("中标公告" in (c.evidence_claim or "") for c in contacts)
        # 供应商自己名下不该出现这些联系人
        sup_contacts = s.query(Contact).filter(Contact.enterprise_id == sup.id).count()
        assert sup_contacts == 0
        # 幂等：重跑不新建联系人
        r2 = backfill_notice_contacts(type("B", (), {"limit": 10})(), SessionLocal(), None)
        assert r2["contacts_created"] == 0


def test_buyer_pool_direct_and_limit(client, admin_headers, monkeypatch):
    """池 A 买家直采语义不变 + 限额不再被截断为 50。"""
    from app.api.tasks import backfill_notice_contacts
    from app.collectors import ccgp as ccgp_mod

    def fake_detail(url: str):
        return {
            "buyer_name": "测试买家研究院",
            "buyer_address": "北京市海淀区测试路 2 号",
            "buyer_contact": "采购处 王工 010-63509799-803",
            "project_contact": "赵六",
            "project_phone": "010-88886666",
            "region": "北京",
            "email": "purch@test.gov.cn",
        }

    monkeypatch.setattr(ccgp_mod, "fetch_notice_detail", fake_detail)
    with SessionLocal() as s:
        b = Enterprise(name="直采测试买家中心", source="ccgp_buyer")
        s.add(b)
        s.flush()
        s.add(Signal(enterprise_id=b.id, type="tender",
                     title="设备采购招标公告", url="http://www.ccgp.gov.cn/notice/2.htm",
                     base_score=40, created_at=utcnow()))
        s.commit()
    result = backfill_notice_contacts(type("B", (), {"limit": 300})(), SessionLocal(), None)
    with SessionLocal() as s:
        b = s.query(Enterprise).filter(Enterprise.name == "直采测试买家中心").first()
        from app.phones import load_phones
        contacts = s.query(Contact).filter(Contact.enterprise_id == b.id).all()
        all_phones = [p for c in contacts for p in load_phones(c.phones_json)]
        assert "010-63509799-803" in all_phones
        assert result["pages_fetched"] <= 300, "限额应尊重 body.limit 而非 50 截断"
        # 地址/城市同步
        assert b.address and "测试路" in b.address and b.city == "北京"


# ---------------- AH3 露出 ----------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_contacts_endpoint_returns_phones(client, admin_headers):
    with SessionLocal() as s:
        e = s.query(Enterprise).filter(Enterprise.name == "直采测试买家中心").first()
        eid = e.id
    d = client.get(f"/api/enterprises/{eid}/contacts", headers=admin_headers).json()
    assert d["items"], "联系人应存在"
    assert any(it.get("phones") for it in d["items"]), "序列化应含 phones 数组"
