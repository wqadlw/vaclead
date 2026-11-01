"""SPEC-V 回归：企业备注（V1）+ 标签系统（V2）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-v-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _init_db():
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


@pytest.fixture(scope="module")
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def ent_id(client, admin_headers) -> int:
    row = "name,province,经营范围\n备注标签测试有限公司,江苏省,真空设备研发\n"
    r = client.post("/api/import/enterprises",
                    files={"file": ("m.csv", row.encode(), "text/csv")}, headers=admin_headers)
    assert r.status_code == 200, r.text
    d = client.get("/api/enterprises", params={"q": "备注标签测试"}, headers=admin_headers).json()
    return d["items"][0]["id"]


# ---------------- V1 备注 ----------------

def test_notes_crud_and_permissions(client, admin_headers, sales_headers, ent_id):
    # admin 建一条
    r = client.post(f"/api/enterprises/{ent_id}/notes",
                    json={"content": "展会认识，关注蚀刻机产能"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    nid_admin = r.json()["id"]
    # sales 建一条
    r = client.post(f"/api/enterprises/{ent_id}/notes",
                    json={"content": "客户提到预算下季度到位"}, headers=sales_headers)
    nid_sales = r.json()["id"]

    # 列表倒序 + 作者落款
    items = client.get(f"/api/enterprises/{ent_id}/notes", headers=admin_headers).json()["items"]
    assert [i["content"] for i in items] == ["客户提到预算下季度到位", "展会认识，关注蚀刻机产能"]
    assert items[0]["author"] == "销售"  # display_name 落款

    # sales 删 admin 的备注 → 403；admin 可删
    r = client.delete(f"/api/enterprises/notes/{nid_admin}", headers=sales_headers)
    assert r.status_code == 403
    assert client.delete(f"/api/enterprises/notes/{nid_sales}",
                         headers=sales_headers).status_code == 200

    # 长度校验
    assert client.post(f"/api/enterprises/{ent_id}/notes", json={"content": ""},
                       headers=admin_headers).status_code == 422

    # 清理
    client.delete(f"/api/enterprises/notes/{nid_admin}", headers=admin_headers)


# ---------------- V2 标签 ----------------

def test_tags_replace_validate_filter_and_aggregate(client, admin_headers, ent_id):
    # 设置标签（去重 trim）
    r = client.put(f"/api/enterprises/{ent_id}/tags",
                   json={"tags": ["重点客户", " 圈层A ", "重点客户", "待激活"]},
                   headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["tags"] == ["重点客户", "圈层A", "待激活"]

    # 超 8 个 422
    assert client.put(f"/api/enterprises/{ent_id}/tags",
                      json={"tags": [f"标签{i}" for i in range(9)]},
                      headers=admin_headers).status_code == 422

    # 标签筛选命中（JSON 引号包裹防子串）
    d = client.get("/api/enterprises", params={"tag": "重点客户"},
                   headers=admin_headers).json()
    assert any(i["name"] == "备注标签测试有限公司" for i in d["items"])
    d2 = client.get("/api/enterprises", params={"tag": "圈层"},
                    headers=admin_headers).json()  # 子串不得误命中（引号包裹语义）
    assert not any(i["name"] == "备注标签测试有限公司" for i in d2["items"])

    # 聚合接口
    agg = client.get("/api/tags", headers=admin_headers).json()["items"]
    tags_map = {i["tag"]: i["count"] for i in agg}
    assert tags_map.get("重点客户", 0) >= 1

    # 导出含标签列
    exp = client.get("/api/enterprises/export", headers=admin_headers)
    assert "标签" in exp.content.decode("utf-8") and "重点客户" in exp.content.decode("utf-8")

    # 清理
    client.put(f"/api/enterprises/{ent_id}/tags", json={"tags": []}, headers=admin_headers)
