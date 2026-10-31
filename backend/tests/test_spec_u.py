"""SPEC-U 回归：企业信息深化（法人入库/完整度/导出增列）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-u-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    """独立临时库需自建表（直连 SessionLocal 的用例在 TestClient 之前执行）。"""
    from app.db import init_db

    init_db()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_resolve_legal_rep_new_and_backfill():
    """resolve legal_rep：新建落库；已有企业空值补填、非空不覆盖。"""
    from app.db import SessionLocal
    from app.models import Enterprise, EnterpriseAlias
    from app.normalize import resolve_enterprise

    with SessionLocal() as s:
        e1, new1 = resolve_enterprise(
            s, "法人回填测试机械有限公司", source="ggzy",
            uscc="91370000U1TEST01X", legal_rep="张三", est_date="2018-06-01",
        )
        assert new1 and e1.legal_rep == "张三" and e1.established_at == "2018-06-01"
        # 同 uscc 再解析：法人换名不得覆盖已有值
        e2, new2 = resolve_enterprise(
            s, "法人回填测试机械有限公司", source="ggzy",
            uscc="91370000U1TEST01X", legal_rep="李四",
        )
        assert not new2 and e2.legal_rep == "张三"
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == e1.id).delete()
        s.delete(e1)
        s.commit()


def test_csv_legal_rep_import_and_completeness(client, admin_headers):
    row = (
        "name,法定代表人,成立日期,注册资本,经营范围\n"
        "完整度测试一号有限公司,王五,2020-01-01,500,真空设备研发\n"
        "完整度测试二号有限公司,,,,\n"
    )
    r = client.post(
        "/api/import/enterprises",
        files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers,
    )
    assert r.status_code == 200, r.text

    d = client.get("/api/enterprises", params={"q": "完整度测试"}, headers=admin_headers).json()
    by = {i["name"]: i for i in d["items"]}
    assert by["完整度测试一号有限公司"]["legal_rep"] == "王五"
    assert by["完整度测试一号有限公司"]["completeness"] == 4  # 法人/成立日期/资本/经营范围
    assert by["完整度测试二号有限公司"]["completeness"] == 0

    # 资料齐全筛选：只中一号
    d2 = client.get("/api/enterprises", params={"q": "完整度测试", "complete": 1},
                    headers=admin_headers).json()
    names2 = [i["name"] for i in d2["items"]]
    assert "完整度测试一号有限公司" in names2
    assert "完整度测试二号有限公司" not in names2


def test_detail_and_export_carry_new_fields(client, admin_headers):
    row = "name,法定代表人,成立日期,注册资本,经营范围\n详情导出测试有限公司,赵六,2019-09-09,800,真空阀门制造\n"
    client.post("/api/import/enterprises",
                files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers)
    d = client.get("/api/enterprises", params={"q": "详情导出测试"}, headers=admin_headers).json()
    eid = d["items"][0]["id"]

    detail = client.get(f"/api/enterprises/{eid}", headers=admin_headers).json()
    assert detail["legal_rep"] == "赵六" and detail["established_at"] == "2019-09-09"
    assert detail["completeness"] == 4

    exp = client.get("/api/enterprises/export", headers=admin_headers)
    assert exp.status_code == 200
    text = exp.content.decode("utf-8")
    assert "法定代表人" in text and "资料完整度" in text and "赵六" in text and "4/4" in text
