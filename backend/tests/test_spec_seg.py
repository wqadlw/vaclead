"""SPEC-SEG 回归：客户分层看板 GET /api/reports/segments（§1 契约 + §3 用例）。

覆盖：结构契约/空态不炸、intent 三档 60/30 边界精确切、by_dial 有号码未触达/
已触达计数、industry key 计数与排序、province Top、by_stage 中文标签、
软删企业不计数、401（无 token）。

并发语义：conftest 各模块 setdefault 自己的 VACLEAD_DB，但全量跑时"首个导入
模块定库"——共享库里有其他模块的数据，故本文件一律用「基线(播种前) → 播种 →
差值断言」而非精确总数（test_spec_o 2026-11-09 同款踩坑）。
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-seg-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app

# 播种企业固定 created_at：远离一切周窗口，避免污染其他模块的 created_at
# 窗口聚合（全量跑共享库语义）
_FAR_PAST = "2024-01-05T00:00:00"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _segments(client, headers) -> dict:
    r = client.get("/api/reports/segments", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _pick(items: list[dict], name: str) -> dict:
    return next(i for i in items if i["name"] == name)


def _value(items: list[dict], name: str) -> int:
    """区块内名称计数；基线（空库/Top8 外）缺失时按 0 计。"""
    return next((i["value"] for i in items if i["name"] == name), 0)


@pytest.fixture(scope="module")
def seeded(client, admin_headers):
    """基线 → 播种 4 企业（意图分边界 60/59/30/10）→ 返回 (baseline, after)。

    播种矩阵（SPEC-SEG §3）：
    - E60：intent=60（高意图下边界，切进"高意图 ≥60"）、semiconductor、江苏省、
      有号码联系人 call_result="connected"（≠pending → 已触达）
    - E59：intent=59（高意图上边界外侧，切进"中意图"）、semiconductor（与 E60
      同 industry_key 两家）、江苏省、有号码联系人 call_result="pending"（未触达）
    - E30：intent=30（中意图下边界，切进"中意图 30-59"）、machining、广东省、
      无联系人（无号码）
    - E10：intent=10（低意图）、无省份、无联系人（无号码）——后续软删验证用
    """
    baseline = _segments(client, admin_headers)

    from app.db import SessionLocal
    from app.models import Contact, Enterprise, Lead

    with SessionLocal() as s:
        e60 = Enterprise(
            name="分层回归高意图半导体有限公司", province="江苏省",
            industry_key="semiconductor", industry_name="半导体/集成电路",
            intent_score=60, created_at=_FAR_PAST,
        )
        e59 = Enterprise(
            name="分层回归中意半导体有限公司", province="江苏省",
            industry_key="semiconductor", industry_name="半导体/集成电路",
            intent_score=59, created_at=_FAR_PAST,
        )
        e30 = Enterprise(
            name="分层回归边界三十机械有限公司", province="广东省",
            industry_key="machining", industry_name="机械加工",
            intent_score=30, created_at=_FAR_PAST,
        )
        e10 = Enterprise(
            name="分层回归低意图新材料有限公司",
            industry_key="new_materials", industry_name="新材料",
            intent_score=10, created_at=_FAR_PAST,
        )
        s.add_all([e60, e59, e30, e10])
        s.flush()
        # 联系人：E60 已触达（call_result="connected"，SPEC-SEG §1 口径
        # call_result != "pending"）；E59 未触达（pending）；E30/E10 无联系人
        s.add_all([
            Contact(enterprise_id=e60.id, name="分层回归采购甲", role="buyer_unit",
                    phones_json='["010-68886688"]', call_result="connected"),
            Contact(enterprise_id=e59.id, name="分层回归采购乙", role="buyer_unit",
                    phones_json='["13800138000"]', call_result="pending"),
        ])
        # 线索：三种状态各一（by_stage 差值断言；每企业仅一条，吃唯一约束）
        s.add_all([
            Lead(enterprise_id=e60.id, status="intent", created_at=_FAR_PAST,
                 updated_at=_FAR_PAST),
            Lead(enterprise_id=e59.id, status="new", created_at=_FAR_PAST,
                 updated_at=_FAR_PAST),
            Lead(enterprise_id=e30.id, status="won", created_at=_FAR_PAST,
                 updated_at=_FAR_PAST),
        ])
        s.commit()
        ids = {"e60": e60.id, "e59": e59.id, "e30": e30.id, "e10": e10.id}

    after = _segments(client, admin_headers)
    return {"baseline": baseline, "after": after, **ids}


def _delta(seeded: dict, section: str, name: str) -> int:
    """播种前后某区块某名称的计数差（= 本文件种子贡献量；基线缺失按 0）。"""
    return _value(seeded["after"][section], name) - _value(
        seeded["baseline"][section], name
    )


# ---------------- 契约结构与空态（SPEC 3：空库全零不炸） ----------------
def test_segments_shape_contract(client, admin_headers):
    d = _segments(client, admin_headers)
    assert set(d) == {"total", "by_intent", "by_dial", "by_industry",
                      "by_province", "by_stage"}
    # by_intent 三档名称固定
    assert [b["name"] for b in d["by_intent"]] == ["高意图 ≥60", "中意图 30-59", "低意图 <30"]
    # by_dial 三档名称固定且与 total 自洽（每家在管企业恰落一桶）
    assert [b["name"] for b in d["by_dial"]] == ["有号码·未触达", "有号码·已触达", "无号码"]
    assert sum(b["value"] for b in d["by_dial"]) == d["total"]
    assert sum(b["value"] for b in d["by_intent"]) == d["total"]
    # industry：value desc、≤8、带 key；province：value desc、≤8
    assert len(d["by_industry"]) <= 8
    assert all("key" in i and i["value"] >= 1 for i in d["by_industry"])
    assert all(
        d["by_industry"][i]["value"] >= d["by_industry"][i + 1]["value"]
        for i in range(len(d["by_industry"]) - 1)
    )
    assert len(d["by_province"]) <= 8
    assert all(
        d["by_province"][i]["value"] >= d["by_province"][i + 1]["value"]
        for i in range(len(d["by_province"]) - 1)
    )
    # by_stage：六状态中文标签齐全（与 frontend LEAD_STATUS 同源）
    assert [s["name"] for s in d["by_stage"]] == [
        "新线索", "跟进中", "意向", "成交", "公海", "放弃"
    ]


# ---------------- intent 三档 60/30 边界精确切（SPEC 3） ----------------
def test_segments_intent_band_boundaries(seeded):
    # 60 → 高意图（含 60）；59 → 中意图；30 → 中意图（含 30）；10 → 低意图
    assert _delta(seeded, "by_intent", "高意图 ≥60") == 1
    assert _delta(seeded, "by_intent", "中意图 30-59") == 2
    assert _delta(seeded, "by_intent", "低意图 <30") == 1


# ---------------- by_dial：有号码未触达/已触达（SPEC 3） ----------------
def test_segments_dial_buckets(seeded):
    # E60 connected → 有号码·已触达；E59 pending → 有号码·未触达；
    # E30/E10 无联系人 → 无号码 ×2
    assert _delta(seeded, "by_dial", "有号码·已触达") == 1
    assert _delta(seeded, "by_dial", "有号码·未触达") == 1
    assert _delta(seeded, "by_dial", "无号码") == 2


# ---------------- industry key 计数与排序（SPEC 3） ----------------
def test_segments_industry_key_count_and_order(seeded):
    after = seeded["after"]
    b_key = [i for i in seeded["baseline"]["by_industry"] if i["key"] == "semiconductor"]
    a_key = [i for i in after["by_industry"] if i["key"] == "semiconductor"]
    # 同 industry_key 两家合并计数（E60+E59）；名称取 industry_name
    if b_key:
        assert a_key, "播种后 semiconductor 应仍在 Top8"
        assert a_key[0]["value"] - b_key[0]["value"] == 2
    else:
        assert a_key, "播种后 semiconductor 应进入行业 Top8"
        assert a_key[0]["value"] == 2
    assert a_key[0]["key"] == "semiconductor"
    assert a_key[0]["name"] == "半导体/集成电路"
    # machining（E30）+1
    a_mach = [i for i in after["by_industry"] if i["key"] == "machining"]
    b_mach = [i for i in seeded["baseline"]["by_industry"] if i["key"] == "machining"]
    assert (a_mach[0]["value"] - (b_mach[0]["value"] if b_mach else 0)) == 1
    # 排序：value 单调不增（semiconductor 2 家应排在 1 家行业之前）
    vals = [i["value"] for i in after["by_industry"]]
    assert vals == sorted(vals, reverse=True)


# ---------------- province / by_stage 差值 ----------------
def test_segments_province_and_stage(seeded):
    assert _delta(seeded, "by_province", "江苏省") == 2
    assert _delta(seeded, "by_province", "广东省") == 1
    assert _delta(seeded, "by_stage", "意向") == 1
    assert _delta(seeded, "by_stage", "新线索") == 1
    assert _delta(seeded, "by_stage", "成交") == 1


# ---------------- 软删企业不计入（全局口径，SPEC-AM AM1） ----------------
def test_segments_soft_delete_excluded(client, admin_headers, seeded):
    before = _segments(client, admin_headers)
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        ent = s.get(Enterprise, seeded["e10"])
        ent.deleted_at = "2024-01-06T00:00:00"
        s.commit()

    after = _segments(client, admin_headers)
    assert after["total"] == before["total"] - 1
    assert (
        _pick(after["by_intent"], "低意图 <30")["value"]
        - _pick(before["by_intent"], "低意图 <30")["value"]
        == -1
    )
    assert (
        _pick(after["by_dial"], "无号码")["value"]
        - _pick(before["by_dial"], "无号码")["value"]
        == -1
    )
    # E10 无省份/无线索：其余切面不受影响
    assert after["by_stage"] == before["by_stage"]
    assert _pick(after["by_province"], "江苏省")["value"] == _pick(
        before["by_province"], "江苏省"
    )["value"]


# ---------------- 401（无 token，SPEC 3） ----------------
def test_segments_unauthorized_401(client):
    assert client.get("/api/reports/segments").status_code == 401
