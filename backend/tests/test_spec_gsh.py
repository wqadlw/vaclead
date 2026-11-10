"""SPEC-GSH 回归：工商补数批量回填（风鸟对照表 xlsx/csv → 按名称/信用代码匹配 → 只填空不覆盖）。

覆盖 SPEC：
- dry_run=1 预演零写入：计数（matched/filled 按字段/kept_existing/skipped 明细）与
  正式回填一致，但库内五要素分毫不动（红线：dry_run 必须零写入）
- 行匹配优先级：行内 uscc 18 位有效 → Enterprise.uscc 精确锚点；miss → 企业名
  归一匹配（resolve 同款，只读不建档）；均不中 → skipped 落明细（row 从 2 起算）
- 回填红线：**只填空不覆盖**——已有值保原样（kept_existing 诚实计数）；注册资本
  float 容错（"500万人民币"→500.0）；uscc 18 位强格式校验（复用 intel.py 模式）
- 匹配到的企业 apply_icp 重算（注册资本/经营范围影响 ICP 分）+ 不建档
- CSV 路径（同义词归一含 uscc 匹配键）+ 非 admin 拒绝（无 token 401；sales 403）
"""
from __future__ import annotations

import io
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-gsh-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app

# 18 位信用代码测试值（字符集不含 I/O/S/V/Z，与 _USCC_RE 强格式一致）
CODE_A = "91320000MA1GQH006W"  # 行 1 携带、库内原本不存在 → 名称匹配后回填进甲
CODE_B = "91320000MA1GQH004X"  # 乙企业预置 → 行 2 以 uscc 锚点命中
CODE_C = "91320500MA1GQH005Y"  # 丙企业预置（行 3 不携带，走名称匹配触发已有值保留）


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
def seeded():
    """预置 4 家企业：甲（全空）/ 乙（仅 uscc）/ 丙（工商已有值，验保留）/ 丁（全空）。"""
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        ent_a = Enterprise(name="GSH回填甲真空设备有限公司", source="poi")
        ent_b = Enterprise(name="GSH回填乙机械有限公司", source="poi", uscc=CODE_B)
        ent_c = Enterprise(
            name="GSH回填丙真空设备有限公司", source="csv", uscc=CODE_C,
            legal_rep="张丙", registered_capital_wan=800.0, business_scope="真空阀门生产",
        )
        ent_d = Enterprise(name="GSH回填丁阀门有限公司", source="poi")
        s.add_all([ent_a, ent_b, ent_c, ent_d])
        s.commit()
        return {"a": ent_a.id, "b": ent_b.id, "c": ent_c.id, "d": ent_d.id}


def _ent(name: str):
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        return s.query(Enterprise).filter(Enterprise.name == name).first()


def _xlsx_bytes(rows: list[list]) -> bytes:
    """openpyxl 内存构造 workbook → xlsx 字节串（SPEC-XLSX 测试构造模式）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_file(content: bytes, filename: str = "gsh.xlsx"):
    """xlsx 字节串 → multipart UploadFile（与 test_spec_imp 同构）。"""
    return {
        "file": (
            filename,
            io.BytesIO(content),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def _gsh_xlsx() -> bytes:
    """工商对照表（5 数据行）：2 行名称匹配 + 1 行 uscc 匹配 + 1 行已有值保留 + 1 行未中。"""
    return _xlsx_bytes([
        # 表头：风鸟对照表常见列名（同义词归一后 = name/uscc/legal_rep/est/cap/scope）
        ["企业名称", "统一社会信用代码", "法定代表人", "成立日期", "注册资本", "经营范围"],
        # 行 2（数据行 1）：uscc 库内不存在 → miss 后名称命中甲 → 五格全填（含 uscc 回填）
        ["GSH回填甲真空设备有限公司", CODE_A, "王甲", "2015-03-01", "500万人民币", "真空泵设备制造销售"],
        # 行 3（数据行 2）：名称查不到（未建档别名）但 uscc 精确命中乙 → 信用代码锚点优先
        ["GSH乙公司对照别名录有限公司", CODE_B, "李乙", "2018-06-15", "1200", "真空镀膜设备生产"],
        # 行 4（数据行 3）：名称命中丙，不带 uscc → 法人/资本/经营范围已有值保留，仅补成立日期
        ["GSH回填丙真空设备有限公司", "", "李丙", "2020-01-01", "800万人民币", "真空阀门生产"],
        # 行 5（数据行 4）：名称与代码都查不到 → skipped（只回填不建档）
        ["GSH对照表幽灵有限公司", "", "", "", "", ""],
        # 行 6（数据行 5）：名称命中丁（全空企业）→ 只填两格
        ["GSH回填丁阀门有限公司", "", "", "2019-09-09", "", "真空阀门销售"],
    ])


EXPECTED_FILLED = {
    "legal_rep": 2,           # 行 2（王甲）+ 行 3（李乙）
    "established_at": 4,      # 行 2/3/4（丙补成立日期）/6
    "registered_capital_wan": 2,  # 行 2（500万人民币→500.0）+ 行 3（"1200"→1200.0）
    "business_scope": 3,      # 行 2/3/6
    "uscc": 1,                # 行 2（名称命中甲后回填 CODE_A）
}


def _assert_counts(body: dict) -> None:
    """预演与正式共用的计数断言：matched=4 / filled 按字段 / kept=3 / skipped=1。"""
    assert body["matched"] == 4
    assert body["filled"] == EXPECTED_FILLED
    assert body["kept_existing"] == 3  # 行 4：丙的法人/注册资本/经营范围已有值保原样
    assert body["skipped"] == 1
    assert body["skipped_details"] == [
        {"row": 5, "reason": "未匹配到库内企业（只回填不建档）"}  # 数据行 4 → 表格第 5 行
    ]
    assert body["total_rows"] == 5


# ---------------- dry_run 预演（零写入红线） ----------------

def test_dry_run_counts_and_zero_write(client, admin_headers, seeded):
    """dry_run=1：计数与正式一致（含映射建议），但库内五要素分毫不动。"""
    r = client.post(
        "/api/import/gongshang-backfill?dry_run=1",
        files=_xlsx_file(_gsh_xlsx()),
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    _assert_counts(body)
    # 映射建议：工商同义词全命中（含 uscc 匹配键识别）
    assert body["headers_raw"] == [
        "企业名称", "统一社会信用代码", "法定代表人", "成立日期", "注册资本", "经营范围",
    ]
    assert body["mapping_suggestion"] == [
        {"col": 0, "field": "name"},
        {"col": 1, "field": "uscc"},
        {"col": 2, "field": "legal_rep"},
        {"col": 3, "field": "established_at"},
        {"col": 4, "field": "registered_capital_wan"},
        {"col": 5, "field": "business_scope"},
    ]
    assert body["preview_rows"][0]["name"] == "GSH回填甲真空设备有限公司"
    # 零写入断言：dry_run 后库内五要素原样（甲全空 / 丙已有值不变）
    a = _ent("GSH回填甲真空设备有限公司")
    assert a.legal_rep is None and a.uscc is None
    assert a.established_at is None and a.registered_capital_wan is None
    assert a.business_scope is None
    c = _ent("GSH回填丙真空设备有限公司")
    assert c.legal_rep == "张丙" and c.established_at is None


# ---------------- 正式回填（只填空不覆盖） ----------------

def test_backfill_fills_and_keeps(client, admin_headers, seeded):
    """正式回填：新字段写入 / 已有值保留 / uscc 未中不建档 / ICP 重算。"""
    r = client.post(
        "/api/import/gongshang-backfill",
        files=_xlsx_file(_gsh_xlsx()),
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    _assert_counts(body)

    # 甲（名称匹配）：五格全填，注册资本"500万人民币"→500.0，日期归一 YYYY-MM-DD
    a = _ent("GSH回填甲真空设备有限公司")
    assert a.uscc == CODE_A and a.legal_rep == "王甲"
    assert a.established_at == "2015-03-01"
    assert a.registered_capital_wan == 500.0
    assert a.business_scope == "真空泵设备制造销售"
    # ICP 重算：注册资本 500 万（100-1000 万档）+ 经营范围含制造 → 分数必为正
    assert a.icp_score > 0

    # 乙（uscc 锚点命中，名称未建档也能匹配）：四格补齐，uscc 原值不动
    b = _ent("GSH回填乙机械有限公司")
    assert b.uscc == CODE_B
    assert b.legal_rep == "李乙" and b.established_at == "2018-06-15"
    assert b.registered_capital_wan == 1200.0
    assert b.business_scope == "真空镀膜设备生产"
    assert b.icp_score > 0

    # 丙（已有值保留红线）：法人/注册资本/经营范围保原样，仅成立日期补上
    c = _ent("GSH回填丙真空设备有限公司")
    assert c.legal_rep == "张丙" and c.registered_capital_wan == 800.0
    assert c.business_scope == "真空阀门生产" and c.uscc == CODE_C
    assert c.established_at == "2020-01-01"

    # 丁（名称匹配，只补两格）
    d = _ent("GSH回填丁阀门有限公司")
    assert d.established_at == "2019-09-09" and d.business_scope == "真空阀门销售"
    assert d.legal_rep is None and d.uscc is None

    # 不建档红线：未命中行绝不新建企业
    assert _ent("GSH对照表幽灵有限公司") is None


def test_backfill_rerun_idempotent(client, admin_headers, seeded):
    """二次回填：全部字段已有值 → filled 全 0、kept_existing 诚实计数、数值不变。

    （行 2 的 uscc 此时已回填进甲 → 该行改由 uscc 锚点命中，uscc 格不计入。）"""
    r = client.post(
        "/api/import/gongshang-backfill",
        files=_xlsx_file(_gsh_xlsx()),
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] == 4 and body["skipped"] == 1
    assert body["filled"] == {f: 0 for f in EXPECTED_FILLED}
    assert body["kept_existing"] == 14  # 行2:4 + 行3:4 + 行4:4 + 行6:2
    # 数值仍原样（二次回填零覆盖）
    a = _ent("GSH回填甲真空设备有限公司")
    assert a.legal_rep == "王甲" and a.registered_capital_wan == 500.0
    c = _ent("GSH回填丙真空设备有限公司")
    assert c.legal_rep == "张丙" and c.established_at == "2020-01-01"


# ---------------- CSV 路径（同义词归一含 uscc 匹配键） ----------------

def test_backfill_csv_uscc_anchor(client, admin_headers):
    """CSV 上传：中文表头同义词归一（含信用代码列）；uscc 锚点命中补法人。"""
    from app.db import SessionLocal
    from app.models import Enterprise

    code_csv = "91320500MA1GQH007A"
    with SessionLocal() as s:
        s.add(Enterprise(name="GSH补数CSV测试有限公司", source="poi", uscc=code_csv))
        s.commit()
    csv_text = (
        "公司名称,统一社会信用代码,法人,注册资本\n"
        "GSH别名录甲公司,91320500MA1GQH007A,钱CSV,300\n"
        "GSH别名录乙公司,91320500MA1GQH007B,孙CSV,50\n"  # uscc 库内无 + 名称未命中 → skipped
    )
    r = client.post("/api/import/gongshang-backfill",
                    files={"file": ("gsh.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] == 1 and body["skipped"] == 1
    assert body["filled"] == {
        "legal_rep": 1, "established_at": 0, "registered_capital_wan": 1,
        "business_scope": 0, "uscc": 0,  # uscc 为命中锚点（值相同），不计不写
    }
    ent = _ent("GSH补数CSV测试有限公司")
    assert ent.legal_rep == "钱CSV" and ent.registered_capital_wan == 300.0
    assert _ent("GSH别名录乙公司") is None  # 未中不建档


# ---------------- 坏文件 / 权限 ----------------

def test_backfill_corrupt_xlsx_400(client, admin_headers):
    """坏 xlsx（空字节改名）→ 400 可读 detail（dry_run 与正式一致）。"""
    r1 = client.post("/api/import/gongshang-backfill?dry_run=1",
                     files=_xlsx_file(b"", "broken.xlsx"), headers=admin_headers)
    assert r1.status_code == 400
    assert "Excel" in r1.json()["detail"]
    r2 = client.post("/api/import/gongshang-backfill",
                     files=_xlsx_file(b"", "broken.xlsx"), headers=admin_headers)
    assert r2.status_code == 400


def test_backfill_requires_admin(client, sales_headers):
    """非 admin 拒绝：无 token 401；sales 角色登录后 403（admin_only 语义）。"""
    base = "/api/import/gongshang-backfill"
    assert client.post(base, files=_xlsx_file(_gsh_xlsx())).status_code == 401
    assert client.post(
        f"{base}?dry_run=1", files=_xlsx_file(_gsh_xlsx())
    ).status_code == 401
    assert client.post(
        base, files=_xlsx_file(_gsh_xlsx()), headers=sales_headers
    ).status_code == 403
    assert client.post(
        f"{base}?dry_run=1", files=_xlsx_file(_gsh_xlsx()), headers=sales_headers
    ).status_code == 403
