"""SPEC-BN 回归：竞对雷达——组合级竞对监测视图。

断言全部基线相对（共享测试库：其他模块可能留有 is_competitor=1 的
企业/信号，绝对计数会叠加翻车——先采基线再种数据，比差值）。
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bn-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.api.competitors import competitors_overview  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Contract,
    Enterprise,
    Lead,
    Signal,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Lead, Signal, Contract, AuditLog]


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
    """模块内只种一次：2 竞对 + 1 普通企业 + 信号矩阵 + 流失线索带合同。

    返回 (ids, base)：base 为种数据前的 overview 快照，断言比差值。
    """
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=120)).isoformat()
    with SessionLocal() as s:
        base = competitors_overview(session=s, user=None)
        c1 = Enterprise(name="BN竞对真空甲公司", source="poi", is_competitor=1)
        c2 = Enterprise(name="BN竞对泵业乙公司", source="poi", is_competitor=1)
        normal = Enterprise(name="BN普通目标机械厂", source="poi")
        s.add_all([c1, c2, normal])
        s.flush()
        s.add_all([
            # 竞对甲：30 天内中标（带金额+采购人）→ stolen_from/amount 可回抽
            Signal(enterprise_id=c1.id, type="tender_win",
                   title="某厂镀膜机采购成交价88.5万元",
                   url="https://example.com/bn-win-1", pub_date=today,
                   evidence_claim="采购人：BN光电材料有限公司，成交价88.5万元"),
            # 竞对甲：30 天内普通信号
            Signal(enterprise_id=c1.id, type="tender", title="BN竞对甲相关询价公告",
                   url="https://example.com/bn-sig-1", pub_date=today),
            # 竞对甲：120 天前旧中标 → 窗口外
            Signal(enterprise_id=c1.id, type="tender_win",
                   title="BN旧成交价12万元项目", url="https://example.com/bn-old-1",
                   pub_date=old),
            # 竞对乙：30 天内 proposal
            Signal(enterprise_id=c2.id, type="proposal", title="BN竞对乙扩建备案",
                   url="https://example.com/bn-prop-1", pub_date=today),
            # 普通企业信号 → 不应出现在任何竞对视图
            Signal(enterprise_id=normal.id, type="tender_win",
                   title="BN普通厂成交价30万元", url="https://example.com/bn-norm-1",
                   pub_date=today),
        ])
        # 流失线索：竞对原因 + 关联合同 55 万
        lost = Lead(enterprise_id=normal.id, status="lost",
                    lost_reason="competitor", owner_id=None, tenant_id=1)
        s.add(lost)
        s.flush()
        s.add(Contract(lead_id=lost.id, owner_id=None, name="BN流失合同",
                       amount_wan=55.0, status="lost", start_date=today))
        s.commit()
        return {"c1": c1.id, "c2": c2.id, "normal": normal.id}, base


def test_bn_competitor_views(client, admin_headers, seeded):
    ids, base = seeded

    r = client.get("/api/competitors/overview", headers=admin_headers)
    assert r.status_code == 200
    ov = r.json()
    assert ov["competitor_total"] == base["competitor_total"] + 2
    assert ov["wins_30d"] == base["wins_30d"] + 1  # 仅竞对甲的窗口内中标
    assert ov["signals_30d"] == base["signals_30d"] + 3
    assert ov["win_amount_30d_wan"] == pytest.approx(
        base["win_amount_30d_wan"] + 88.5, abs=0.01
    )
    assert ov["lost_to_competitor"] == base["lost_to_competitor"] + 1
    assert ov["lost_amount_wan"] == pytest.approx(
        base["lost_amount_wan"] + 55.0, abs=0.01
    )
    if base["wins_30d"] == 0:  # 基线无中标时，最活跃必是我种的甲
        assert "BN竞对真空甲" in ov["top_competitor"]

    # feed：只含竞对、窗口过滤、金额与抢单对象回抽
    r = client.get("/api/competitors/feed", headers=admin_headers)
    items = r.json()["items"]
    assert {i["enterprise_name"] for i in items if i["enterprise_name"].startswith("BN")} <= {
        "BN竞对真空甲公司", "BN竞对泵业乙公司"
    }
    win = next(i for i in items if i["title"].startswith("某厂镀膜机"))
    assert win["amount_wan"] == 88.5
    assert win["stolen_from"] == "BN光电材料有限公司"

    # feed 窗口 30 天：旧中标出窗
    r = client.get("/api/competitors/feed?days=30", headers=admin_headers)
    assert all("BN旧成交价" not in i["title"] for i in r.json()["items"])

    # feed 类型过滤
    r = client.get("/api/competitors/feed?type=proposal", headers=admin_headers)
    items = r.json()["items"]
    assert any(i["title"] == "BN竞对乙扩建备案" for i in items)
    assert all(i["type"] == "proposal" for i in items)

    # 榜单（只看本模块种的企业）：甲 30 天中标 1 次排乙前面；统计字段齐
    r = client.get("/api/competitors", headers=admin_headers)
    mine = [x for x in r.json()["items"] if x["name"].startswith("BN")]
    assert [x["name"] for x in mine] == ["BN竞对真空甲公司", "BN竞对泵业乙公司"]
    top = mine[0]
    assert top["signal_count"] == 3
    assert top["win_count"] == 2
    assert top["wins_30d"] == 1
    assert top["top_buyer"] == "BN光电材料有限公司"

    # q 过滤
    r = client.get("/api/competitors?q=泵业", headers=admin_headers)
    names = [x["name"] for x in r.json()["items"] if x["name"].startswith("BN")]
    assert names == ["BN竞对泵业乙公司"]


def test_bn_mark_toggle(client, admin_headers, seeded):
    ids, base = seeded
    # 标记普通企业为竞对 → overview +1；取消 → 恢复
    r = client.post(f"/api/competitors/{ids['normal']}/mark",
                    json={"flag": 1}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["is_competitor"] == 1
    ov = client.get("/api/competitors/overview", headers=admin_headers).json()
    assert ov["competitor_total"] == base["competitor_total"] + 3

    r = client.post(f"/api/competitors/{ids['normal']}/mark",
                    json={"flag": 0}, headers=admin_headers)
    assert r.json()["is_competitor"] == 0
    ov = client.get("/api/competitors/overview", headers=admin_headers).json()
    assert ov["competitor_total"] == base["competitor_total"] + 2

    # 非法 flag → 422；软删竞对不出现在视图
    r = client.post(f"/api/competitors/{ids['normal']}/mark",
                    json={"flag": "x"}, headers=admin_headers)
    assert r.status_code == 422
    with SessionLocal() as s:
        e = s.get(Enterprise, ids["c2"])
        e.deleted_at = utcnow()
        s.commit()
    ov = client.get("/api/competitors/overview", headers=admin_headers).json()
    assert ov["competitor_total"] == base["competitor_total"] + 1
