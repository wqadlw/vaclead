"""SPEC-IMP 回归：企业 CSV 上传导入向导（preview 预演 + 显式 mapping + skipped_details）。

覆盖 SPEC 3 节：
- preview：表头同义词归一 / 映射建议（首列 name 兜底 + skip 缺省）/ delimiter 探测 /
  dup_preview 计数（预置 2 家同名库内企业）/ preview_rows 前 5 行封顶
- 正式导入带显式乱序 mapping 生效（列顺序打乱也能落对字段，skip 列跳过）
- 不带 mapping 旧语义兼容（同义词路径回归）
- skipped_details：无名称行计数 + 明细（row 从 2 起算，≤10 条封顶）
- 非 admin 拒绝（无 token 401；sales 角色 403）
"""
from __future__ import annotations

import io
import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-imp-"), "t.db")
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


@pytest.fixture(scope="module")
def sales_headers(client):
    r = client.post("/api/auth/login", json={"username": "sales", "password": "sales123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _csv_file(content: str, filename: str = "imp.csv"):
    """CSV 字节串 → multipart UploadFile（io.BytesIO 模式，TestClient files= 传法）。"""
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


def _ent(name: str):
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        return s.query(Enterprise).filter(Enterprise.name == name).first()


# ---------------- preview 预演（SPEC 1.1） ----------------

def test_preview_synonym_mapping_and_dup(client, admin_headers):
    """同义词归一 + 映射建议（skip 缺省）+ dup_preview 计数（预置 2 家库内同名）。"""
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        s.add(Enterprise(name="预演重复科技有限公司", source="csv"))
        s.add(Enterprise(name="预演老牌机械有限公司", source="csv"))
        s.commit()

    csv_text = (
        "公司名称,省份,备注\n"
        "预演重复科技有限公司,江苏省,老客户\n"
        "预演新锐半导体有限公司,广东省,新客\n"
        "预演老牌机械有限公司,山东省,\n"
        "预演新客机械厂,江苏省,x\n"
    )
    r = client.post("/api/import/enterprises/preview",
                    files=_csv_file(csv_text), headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["detected_delimiter"] == ","
    assert d["headers_raw"] == ["公司名称", "省份", "备注"]
    assert d["headers_synonymized"] == ["name", "province", ""]  # "备注"未命中 → ""
    assert d["mapping_suggestion"] == [
        {"col": 0, "field": "name"},
        {"col": 1, "field": "province"},
        {"col": 2, "field": "skip"},  # 未命中列缺省 skip
    ]
    assert d["total_rows"] == 4
    # preview_rows 按建议映射取行（skip 列不出现）
    assert d["preview_rows"][0] == {"name": "预演重复科技有限公司", "province": "江苏省"}
    assert len(d["preview_rows"]) == 4
    # dup_preview：2 家库内同名 → existing=2；空备注行无、其余 2 行 new
    assert d["dup_preview"] == {"existing": 2, "new": 2}


def test_preview_first_col_fallback_and_tab_delimiter(client, admin_headers):
    """表头未命中同义词时首列兜底 name；Tab 分隔探测（与粘贴路径同规则）。"""
    csv_text = "供应商清单\t地区\n某未命中表头真空设备厂\t浙江省\n另一家机械厂\t江苏省\n"
    r = client.post("/api/import/enterprises/preview",
                    files=_csv_file(csv_text, "imp.tsv"), headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["detected_delimiter"] == "\t"
    assert d["mapping_suggestion"] == [
        {"col": 0, "field": "name"},   # 首列兜底
        {"col": 1, "field": "skip"},
    ]
    assert d["preview_rows"][0] == {"name": "某未命中表头真空设备厂"}
    assert d["dup_preview"] == {"existing": 0, "new": 2}


def test_preview_rows_capped_at_5(client, admin_headers):
    """preview_rows 封顶 5 行；total_rows 与 dup_preview 按全量行计。"""
    csv_text = "公司名称\n" + "".join(f"批量预演{i}号有限公司\n" for i in range(8))
    r = client.post("/api/import/enterprises/preview",
                    files=_csv_file(csv_text), headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_rows"] == 8
    assert len(d["preview_rows"]) == 5
    assert d["dup_preview"] == {"existing": 0, "new": 8}


# ---------------- 正式导入（SPEC 1.2） ----------------

def test_import_explicit_mapping_out_of_order(client, admin_headers):
    """显式乱序 mapping 生效：列顺序打乱也能落对字段，skip 列跳过。"""
    csv_text = (
        "省份,随手记,公司名称,注册资本\n"
        "湖北省,忽略我,乱序映射真空设备有限公司,500\n"
        "湖南省,忽略我,湖南乱序第二轴承有限公司,1200\n"
    )
    mapping = json.dumps(["province", "skip", "name", "registered_capital_wan"])
    r = client.post(
        "/api/import/enterprises",
        files=_csv_file(csv_text),
        data={"mapping": mapping},  # SPEC-IMP 1.2：mapping 为 Form field（JSON 数组字符串）
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 2 and body["skipped"] == 0

    e1 = _ent("乱序映射真空设备有限公司")
    assert e1 is not None, "显式 mapping 后企业应存在"
    assert e1.province == "湖北省"
    assert e1.registered_capital_wan == 500.0
    assert e1.source_batch_id == body["batch_id"]
    e2 = _ent("湖南乱序第二轴承有限公司")
    assert e2 is not None and e2.province == "湖南省" and e2.registered_capital_wan == 1200.0


def test_import_default_synonym_path(client, admin_headers):
    """不带 mapping 走 _HEADER_SYNONYMS 同义词归一（旧语义兼容回归）。"""
    csv_text = (
        "公司名称,省份,经营范围\n"
        "同义词回归测试真空设备有限公司,上海市,真空泵设备制造\n"
    )
    r = client.post("/api/import/enterprises",
                    files=_csv_file(csv_text), headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 1
    ent = _ent("同义词回归测试真空设备有限公司")
    assert ent is not None
    assert ent.province == "上海市"
    assert ent.business_scope == "真空泵设备制造"


def test_import_skipped_details(client, admin_headers):
    """无名称行 skipped 计数 + 明细（row 从 2 起算含表头行；≤10 条封顶）。"""
    csv_text = (
        "公司名称,省份\n"
        "明细测试合格有限公司,江苏省\n"
        ",浙江省\n"  # 第 3 行：名称为空
        "   ,广东省\n"  # 第 4 行：空白名称
        "明细测试第二有限公司,山东省\n"
    )
    r = client.post("/api/import/enterprises",
                    files=_csv_file(csv_text), headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] == 2 and body["inserted"] == 2
    assert body["skipped_details"] == [
        {"row": 3, "reason": "缺少企业名称"},
        {"row": 4, "reason": "缺少企业名称"},
    ]

    # 明细条数封顶：12 行无名称 → 明细只记前 10 条
    many = "公司名称,省份\n" + ",某省\n" * 12
    r2 = client.post("/api/import/enterprises",
                     files=_csv_file(many), headers=admin_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["skipped"] == 12
    assert len(r2.json()["skipped_details"]) == 10
    assert r2.json()["skipped_details"][0]["row"] == 2


def test_import_mapping_invalid_json_422(client, admin_headers):
    """mapping 非法 JSON → 422（不落库）。"""
    r = client.post(
        "/api/import/enterprises",
        files=_csv_file("公司名称\n非法映射测试机械厂\n"),
        data={"mapping": "not-json"},
        headers=admin_headers,
    )
    assert r.status_code == 422
    assert _ent("非法映射测试机械厂") is None


# ---------------- 权限（SPEC 3） ----------------

def test_import_endpoints_require_admin(client, sales_headers):
    """非 admin 拒绝：无 token 401；sales 角色登录后 403（admin_only 语义）。"""
    assert client.post("/api/import/enterprises/preview",
                       files=_csv_file("公司名称\nx\n")).status_code == 401
    assert client.post("/api/import/enterprises",
                       files=_csv_file("公司名称\nx\n")).status_code == 401
    assert client.post("/api/import/enterprises/preview",
                       files=_csv_file("公司名称\nx\n"), headers=sales_headers).status_code == 403
    assert client.post("/api/import/enterprises",
                       files=_csv_file("公司名称\nx\n"), headers=sales_headers).status_code == 403
