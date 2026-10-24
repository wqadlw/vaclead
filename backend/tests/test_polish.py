"""信息获取链路打磨测试（2026-10-25 体检产出）：

1. 企业名抽取两级正则（严格企业名 > 宽松机构名，黑名单过滤行政区，最长匹配）
2. CSV 编码容错（UTF-8 BOM / UTF-16 / GBK / 空行 / 缺列）
3. 抽不到归属方时落"待归属"占位而非丢弃（数据零漏损）
"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-polish-"), "t.db")
)

from app.api.imports import extract_enterprise_name  # noqa: E402
from app.collectors.tender import _decode_csv_bytes, parse_tender_csv  # noqa: E402


# ---- 企业名抽取 ----
@pytest.mark.parametrize(
    "title,content,expected",
    [
        # 严格企业名（含括号分段）
        ("无锡某半导体设备（无锡）有限公司竞争力提升项目", "询价公告", "无锡某半导体设备（无锡）有限公司"),
        ("苏州某半导体设备有限公司真空腔体采购", "磁控溅射系统", "苏州某半导体设备有限公司"),
        # 宽松机构名
        ("某区疾控中心usam真空冷冻干燥机采购项目公开招标公告", "采购冷冻干燥设备1台", "某区疾控中心"),
        ("市第一人民医院检验科设备采购", "冷冻干燥机", "市第一人民医院"),
        ("某大学材料学院平台建设", "高真空镀膜机", "某大学材料学院"),
        # 最长匹配：研究所全名优于前置头衔
        ("中国科学院某光学精密机械研究所设备购置", "真空检测装置", "某光学精密机械研究所"),
        # 标题没有则去正文找
        ("仪器购置公告", "某检测技术有限公司拟采购真空检漏仪", "某检测技术有限公司"),
    ],
)
def test_extract_enterprise_name_hit(title, content, expected):
    assert extract_enterprise_name(title, content) == expected


@pytest.mark.parametrize(
    "title,content",
    [
        # 行政区名不是采购单位
        ("某某县2026年农村产业融合示范园建设项目招标", "厂房施工"),
        ("苏州工业园区2026年02月政府采购意向", "分子泵机组"),  # 园区后缀在黑名单
    ],
)
def test_extract_enterprise_name_blacklist(title, content):
    assert extract_enterprise_name(title, content) == ""


# ---- CSV 编码容错 ----
def test_decode_utf8_bom():
    assert _decode_csv_bytes(b"\xef\xbb\xbftitle\nA\n").startswith("title")


def test_decode_utf16_excel():
    body = "title,content,pub_date\nE,zhenkong,2026-01-01\n".encode("utf-16")
    rows = parse_tender_csv(body)
    assert len(rows) == 1 and rows[0]["title"] == "E"


def test_decode_gbk_fallback():
    body = "title,content,pub_date\nB,zhenkong,2026-01-01\n".encode("gbk")
    rows = parse_tender_csv(body)
    assert len(rows) == 1 and rows[0]["title"] == "B"


def test_csv_empty_rows_and_missing_columns_skipped():
    # 两行有效（有 title+date）+ 空行（csv 模块自动吞）+ 缺 date 的行（skipped 计数）
    body = b"title,content,pub_date\nA,x,2026-01-01\n\n\nD,zhenkong,\nE,y,2026-02-01\n"
    stats = {}
    rows = parse_tender_csv(body, stats=stats)
    assert [r["title"] for r in rows] == ["A", "E"]
    assert stats == {"total": 3, "parsed": 2, "skipped": 1}


# ---- 待归属兜底（在 API 层，模拟导入一条无归属公告） ----
def test_unassigned_placeholder_on_import(client_factory=None):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        # 无企业名、无机构名的公告：命中设备词（正文），title/content 抽不出归属
        csv_body = (
            "title,content,url,pub_date\n"
            "2026年仪器设备采购项目(第三批),拟采购分子泵机组及超高真空腔体,,2026-10-20\n"
        ).encode()
        r = c.post("/api/import/tenders", files={"file": ("t.csv", csv_body, "text/csv")}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["signals_created"] == 1  # 信号没有因抽不到归属而丢失
        items = c.get("/api/enterprises?q=待归属", headers=h).json()["items"]
        assert len(items) == 1 and items[0]["is_competitor"] == 0
