"""SPEC-AV 回归：同行业公开案例库 + 提案接入。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-av-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Enterprise,
    SettingsKV,
    Signal,
    utcnow,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Enterprise, Signal, AuditLog]


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


def _seed(name: str, industry: str, title: str, sig_type: str = "tender_win") -> None:
    with SessionLocal() as s:
        e = Enterprise(name=name, source="ggzy", industry_key=industry,
                       industry_name=industry, city="苏州")
        s.add(e)
        s.flush()
        s.add(Signal(enterprise_id=e.id, type=sig_type, title=title,
                     url="https://example.com/gg", pub_date="2026-10-01",
                     evidence_claim="公开公告证据句", base_score=50,
                     created_at=utcnow()))
        s.commit()


def test_industry_cases_aggregation(client, admin_headers):
    """同行业三类信号聚合；他行业/非采购信号不出现。"""
    _seed("冻干食品案例甲企业", "freeze_dried", "食品冻干设备中标公告")
    _seed("冻干食品案例乙企业", "freeze_dried", "食品厂真空系统采购")
    _seed("半导体无关企业", "semiconductor", "晶圆厂设备采购")
    _seed("冻干企业环保信号不算案例", "freeze_dried", "排污许可", sig_type="other")
    d = client.get("/api/cases/industry/freeze_dried", headers=admin_headers).json()
    assert d["total"] == 2, "只有采购三类信号算案例"
    titles = " ".join(x["title"] for x in d["items"])
    assert "冻干设备中标" in titles and "真空系统采购" in titles
    assert all(x["ent_name"] for x in d["items"])
    # 他行业不串
    d2 = client.get("/api/cases/industry/semiconductor", headers=admin_headers).json()
    assert all("冻干" not in x["title"] for x in d2["items"])


def test_proposal_print_includes_cases(client, admin_headers):
    """提案打印含「同行业公开采购案例」节（有案例时）。"""
    _seed("提案案例同行企业", "freeze_dried", "同行冻干产线真空泵采购公告")
    d = client.get("/api/enterprises", params={"q": "提案案例同行企业"},
                   headers=admin_headers).json()
    eid = d["items"][0]["id"]
    r = client.get("/api/proposal/print", params={"eid": eid, "power_kw": 37,
                                                  "hours_per_day": 16},
                   headers=admin_headers)
    html = r.text
    import re as _re

    print("\n[AV debug] h2 节:", _re.findall(r"<h2>([^<]+)</h2>", html))
    with SessionLocal() as s:
        from app.models import Signal as _S

        print("[AV debug] signal rows:",
              [(x.id, x.enterprise_id, x.type) for x in s.query(_S).all()])
        print("[AV debug] ent rows:",
              [(x.id, x.industry_key, x.deleted_at) for x in s.query(Enterprise).all()])
    print("[AV debug] eid:", eid, "| ent:", d["items"][0]["name"],
          "| ind:", d["items"][0].get("id"))
    assert "同行业公开采购案例" in html, "提案应含同行案例节"
    assert "真空泵采购公告" in html
    # 无案例行业 → 节省段自动消失（编号回退）
    with SessionLocal() as s:
        e = Enterprise(name="无案例行业企业", source="manual",
                       industry_key="mining_metallurgy",
                       industry_name="mining_metallurgy")
        s.add(e)
        s.commit()
        eid2 = e.id
    r2 = client.get("/api/proposal/print", params={"eid": eid2, "power_kw": 20},
                    headers=admin_headers)
    assert "同行业公开采购案例" not in r2.text
    assert "为什么选择昊志" in r2.text
