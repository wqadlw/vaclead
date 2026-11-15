"""SPEC-AL 回归：智能推荐引擎（打分/理由/排除同行/阈值）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-al-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    Lead,
    SettingsKV,
    Signal,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Contact, Signal, Lead]


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


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_ent(session, name: str, *, comp: int = 0, intent: int = 0,
              industry: str | None = "coating", city: str = "苏州") -> Enterprise:
    e = Enterprise(name=name, source="poi", is_competitor=comp,
                   intent_score=intent, industry_key=industry,
                   industry_name=industry, city=city)
    session.add(e)
    session.flush()
    return e


def _seed_phone(session, eid: int, phone: str) -> None:
    session.add(Contact(enterprise_id=eid, name="地图公开电话", role="poi",
                        title=phone, phones_json=f'["{phone}"]', source="amap"))
    session.commit()


def test_recommend_scoring_and_reasons():
    """电话+信号+未在管+tier 全命中 → 高分且理由齐全；同行被排除。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        # 满配候选：电话+新信号+高意图+未在管
        a = _seed_ent(s, "苏州推荐满配镀膜有限公司", intent=80)
        _seed_phone(s, a.id, "0512-66668888")
        s.add(Signal(enterprise_id=a.id, type="tender_win",
                     title="推荐测试中标公告", url="", base_score=60, created_at=utcnow()))
        # 同行：有电话但排除
        c = _seed_ent(s, "苏州同行打标设备制造有限公司", comp=1)
        _seed_phone(s, c.id, "0512-33333333")
        # 无电话无信号：不进候选
        _seed_ent(s, "苏州无电话无声企业", intent=0)
        # 已在管（following）：无"未分配"加分
        b = _seed_ent(s, "苏州已在管镀膜有限公司", intent=60)
        _seed_phone(s, b.id, "0512-55554444")
        s.add(Lead(enterprise_id=b.id, status="following"))
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"]: r for r in recs}
        assert a.id in ids, "满配候选应入榜"
        assert c.id not in ids, "同行必须被排除"
        assert ids[a.id]["score"] > ids[b.id]["score"], "满配 > 已在管"
        reasons_a = " ".join(ids[a.id]["reasons"])
        assert "有公开电话" in reasons_a and "尚未分配" in reasons_a
        assert ids[a.id]["phone"] == "0512-66668888"


def test_recommend_shared_phone_demoted():
    """2026-11-14 数据质量轮：号码挂 ≥3 家企业（总机/物业号）→ -15 分，
    纯"电话+未在管"基线候选（45 分）被推出阈值；独立号码候选不受影响。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        shared = "0512-77777777"
        a = _seed_ent(s, "苏州共享号甲镀膜有限公司")
        _seed_phone(s, a.id, shared)
        b = _seed_ent(s, "苏州共享号乙镀膜有限公司")
        _seed_phone(s, b.id, shared)
        c = _seed_ent(s, "苏州共享号丙镀膜有限公司")
        _seed_phone(s, c.id, shared)
        d = _seed_ent(s, "苏州独立号镀膜有限公司")
        _seed_phone(s, d.id, "0512-88889999")
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"] for r in recs}
        assert a.id not in ids and b.id not in ids and c.id not in ids, (
            "共享号码候选应被降权出榜"
        )
        assert d.id in ids, "独立号码候选不受影响"
        rec_d = next(r for r in recs if r["ent_id"] == d.id)
        assert rec_d["phone"] == "0512-88889999"


def test_recommend_excludes_public_sector():
    """2026-11-14："学校请出推荐"扩面——医疗卫生（医院）不进先打给谁。
    2026-11-15 用户拍板："分析/实验室不需要"——检测/实验室**整体出局**
    （含民营第三方公司，不再区分名称形态）。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        h = _seed_ent(s, "苏州公共人民医院", industry="medical_health", intent=80)
        _seed_phone(s, h.id, "0512-10101010")
        pub_lab = _seed_ent(s, "铜陵市食品药品检验中心", industry="lab_instrument", intent=80)
        _seed_phone(s, pub_lab.id, "0512-10101011")
        priv = _seed_ent(s, "苏州天衡检测技术有限公司", industry="lab_instrument", intent=80)
        _seed_phone(s, priv.id, "0512-10101012")
        gov = _seed_ent(s, "宿迁市产品质量监督检验所", industry=None, intent=80)
        _seed_phone(s, gov.id, "0512-10101013")
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"] for r in recs}
        assert h.id not in ids, "医疗卫生（医院）应被排除"
        assert pub_lab.id not in ids, "检验检测事业单位应被排除"
        assert priv.id not in ids, "2026-11-15 拍板：民营检测公司也整体出局"
        assert gov.id not in ids, "未分类但名称为公共机构形态（检验所）应被兜底排除"


def test_recommend_excludes_oem_role():
    """2026-11-15 用户拍板："造泵装机器不需要"——OEM 设备商（买泵装进自家
    设备）出局；end_user（无角色标注）保留。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        oem = _seed_ent(s, "苏州华腾镀膜设备有限公司", industry="coating", intent=90)
        oem.customer_role = "oem"
        _seed_phone(s, oem.id, "0512-30303030")
        end_user = _seed_ent(s, "苏州晶瑞真空科技有限公司", industry="coating", intent=90)
        _seed_phone(s, end_user.id, "0512-30303031")
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"] for r in recs}
        assert oem.id not in ids, "OEM 设备商（customer_role=oem）应被排除"
        assert end_user.id in ids, "终端用户应保留"


def test_recommend_excludes_procurement_buyers():
    """2026-11-14 用户拍板："我们不做政采买家"——*_buyer 建档源（机关/
    事业单位/学校/医院主体）整体出局推荐面；同企业改民用源后应进。"""
    from app.recommend import score_candidates

    with SessionLocal() as s:
        buyer = _seed_ent(s, "青岛大学分析测试中心", industry="lab_instrument", intent=90)
        buyer.source = "ggzy_buyer"
        _seed_phone(s, buyer.id, "0532-20202020")
        # 同名同行业但民用源（对照组：证明排除按 source 而非名称/行业）
        twin = _seed_ent(s, "青岛同力真空装备有限公司", industry="coating", intent=90)
        twin.source = "ggzy_buyer"
        _seed_phone(s, twin.id, "0532-20202021")
        normal = _seed_ent(s, "青岛恒锐真空技术有限公司", industry="coating", intent=90)
        _seed_phone(s, normal.id, "0532-20202022")
        s.commit()

        recs = score_candidates(s, limit=50)
        ids = {r["ent_id"] for r in recs}
        assert buyer.id not in ids, "政采买家建档源（*_buyer）应被排除"
        assert twin.id not in ids, "同企业民用行业但买家建档源仍应排除（按 source 判定）"
        assert normal.id in ids, "民用源同行业企业应保留"

        # is_non_buyer 直测（三参形态）
        from app.vertical import is_non_buyer
        assert is_non_buyer(None, None, "ccgp_buyer")
        assert is_non_buyer("coating", "某公司", "sdgp_buyer")
        assert not is_non_buyer("coating", "某公司", "poi")


def test_recommend_endpoint(client, admin_headers):
    d = client.get("/api/recommendations", params={"limit": 5}, headers=admin_headers).json()
    assert isinstance(d["items"], list)
    for it in d["items"]:
        assert it["score"] >= 45 and it["reasons"], "推荐项必须过阈值且带理由"
