"""SPEC-CK 回归：风鸟配额流水线——缺数清单/官方导出回灌语义。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ck-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Contact,
    Enterprise,
    EnterpriseAlias,
)
from app.phones import load_phones  # noqa: E402

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, EnterpriseAlias, AuditLog]


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
def seeded():
    with SessionLocal() as s:
        rich = Enterprise(name="CK资料齐全镀膜有限公司", source="csv",
                          uscc="91320000CKTEST01X", legal_rep="张全",
                          registered_capital_wan=1000.0, established_at="2018-05-01",
                          intent_score=90)
        poor = Enterprise(name="CK缺数机械有限公司", source="poi", intent_score=70)
        s.add_all([rich, poor])
        s.flush()
        s.add(Contact(enterprise_id=rich.id, name="张全", role="legal_rep",
                      phones_json='["13800000071"]'))
        s.commit()
        return {"rich": rich.id, "poor": poor.id}


def test_ck_lookup_export(client, admin_headers, seeded):
    """缺数清单：缺数者入选，资料齐全者不入选；CSV 表头正确。"""
    r = client.get("/api/tasks/gongshang-lookup-export", headers=admin_headers)
    assert r.status_code == 200
    text = r.text
    assert text.startswith("企业名称,地区,行业,意图分,缺失项")
    assert "CK缺数机械有限公司" in text
    assert "CK资料齐全镀膜有限公司" not in text
    assert "联系电话" in text  # poor 无电话


def test_ck_gongshang_csv_fill_and_no_overwrite(client, admin_headers, seeded):
    """回灌：补空生效/已有值不覆盖/电话建 contact/同号跳过/新企业建档。"""
    rows = [
        {   # 已有企业（rich）：只该建电话 contact，字段不覆盖
            "企业名称": "CK资料齐全镀膜有限公司",
            "法定代表人": "李别覆盖我",
            "注册资本": "8888万人民币",
            "企业电话": "13900000072",
        },
        {   # 已有企业（poor）：空位补齐
            "企业名称": "CK缺数机械有限公司",
            "统一社会信用代码": "91320000CKTEST02X",
            "法定代表人": "王补",
            "注册资本": "1,200万元",
            "成立日期": "2020-01-15",
            "企业电话": "0512-66668888",
        },
        {   # 新企业：resolve 建档
            "企业名称": "CK风鸟新查精密仪器有限公司",
            "法定代表人": "赵新",
            "注册资本": "500万人民币",
            "企业电话": "13700000073",
        },
        {   # 名字太短：跳过
            "企业名称": "某某",
        },
    ]
    r = client.post("/api/import/gongshang-csv", json={"rows": rows},
                    headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["matched"] == 3
    assert d["skipped"] == 1
    assert d["phones_added"] == 3

    with SessionLocal() as s:
        rich = s.query(Enterprise).filter(Enterprise.name == "CK资料齐全镀膜有限公司").one()
        assert rich.legal_rep == "张全"  # 不覆盖
        assert rich.registered_capital_wan == 1000.0  # 不覆盖
        rich_phones = [load_phones(c.phones_json) for c in s.query(Contact).filter(
            Contact.enterprise_id == rich.id)]

        poor = s.query(Enterprise).filter(Enterprise.name == "CK缺数机械有限公司").one()
        assert poor.uscc == "91320000CKTEST02X"
        assert poor.legal_rep == "王补"
        assert poor.registered_capital_wan == 1200.0
        assert poor.established_at == "2020-01-15"
        assert s.query(Contact).filter(
            Contact.enterprise_id == poor.id, Contact.source == "riskbird"
        ).count() == 1

        fresh = s.query(Enterprise).filter(
            Enterprise.name == "CK风鸟新查精密仪器有限公司"
        ).one()
        assert fresh.legal_rep == "赵新"
        assert fresh.registered_capital_wan == 500.0

    # 幂等：同号重灌不重复建 contact
    r2 = client.post("/api/import/gongshang-csv", json={"rows": rows},
                     headers=admin_headers)
    assert r2.json()["phones_added"] == 0


def test_ck_schools_excluded(client, admin_headers, seeded):
    """2026-11-08 用户拍板：学校/科研院所非目标客户——推荐与补数清单均排除。"""
    with SessionLocal() as s:
        school = Enterprise(name="CK师范学院", source="poi",
                            industry_key="research_edu", intent_score=300)
        s.add(school)
        s.flush()
        s.add(Contact(enterprise_id=school.id, name="CK老师", role="buyer_contact",
                      phones_json='["13800000081"]'))
        s.commit()

    # 推荐排除
    items = client.get("/api/recommendations?limit=100", headers=admin_headers).json()["items"]
    assert all(i["industry_name"] != "科研院所/高校" for i in items)

    # 补数清单排除
    text = client.get("/api/tasks/gongshang-lookup-export", headers=admin_headers).text
    assert "CK师范学院" not in text
