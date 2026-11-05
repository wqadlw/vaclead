"""SPEC-AT 回归：节能账计算引擎 + 提案打印视图。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-at-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    Product,
    SettingsKV,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type, AuditLog] = [Enterprise, Product, AuditLog]


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
        # audit_log 同步清（entity_id 跨模块撞库会污染审计断言，W57）
        s.query(AuditLog).filter(AuditLog.id > snap.get('audit_log', 0)).delete()
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


def test_roi_calc_math():
    """节能账数学：37kW×16h×0.8×365×0.7 = 151,866 元基线；省 25% = 37,966。"""
    from app.api.proposal import roi_calc

    r = roi_calc(power_kw=37, hours_per_day=16, load_factor=0.8,
                 price_per_kwh=0.7, saving_pct=25)
    baseline = 37 * 16 * 0.8 * 365 * 0.7
    assert r["baseline_year_cost"] == round(baseline, 0)
    assert r["annual_save"] == round(baseline * 0.25, 0)
    # 5 年累计用未取整年省计算（更精确），允许 1 元取整差
    assert abs(r["five_year_save"] - baseline * 0.25 * 5) < 1
    # 回收期：设备价 30 万 ÷ 年省
    r2 = roi_calc(37, 16, 0.8, 0.7, 25, equipment_price_wan=30)
    assert r2["payback_months"] == round(300000 / r2["annual_save"], 1)
    # 参数校验
    import pytest as _pytest

    with _pytest.raises(ValueError):
        roi_calc(-1, 16, 0.8, 0.7, 25)
    with _pytest.raises(ValueError):
        roi_calc(37, 16, 1.5, 0.7, 25)
    with _pytest.raises(ValueError):
        roi_calc(37, 16, 0.8, 0.7, 80)


def test_roi_endpoint(client, admin_headers):
    r = client.post("/api/proposal/roi",
                    json={"power_kw": 37, "hours_per_day": 16, "load_factor": 0.8,
                          "price_per_kwh": 0.7, "saving_pct": 25},
                    headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["result"]["annual_save"] > 0
    assert "formula" in r.json()["result"], "公式可解释（面对工程师质询）"


def test_proposal_print_view(client, admin_headers):
    eid = client.post(
        "/api/import/paste",
        json={"text": "名称\n提案打印测试镀膜有限公司"},
        headers=admin_headers,
    ).json().get("inserted") and None
    d = client.get("/api/enterprises", params={"q": "提案打印测试镀膜有限公司"},
                   headers=admin_headers).json()
    eid = d["items"][0]["id"]
    # 种子产品线保证选型推荐有内容
    with SessionLocal() as s:
        from app.api.units import seed_haoqing_products

        seed_haoqing_products(s)
    r = client.get("/api/proposal/print", params={
        "eid": eid, "power_kw": 37, "hours_per_day": 16,
        "load_factor": 0.8, "price_per_kwh": 0.7, "saving_pct": 25,
    }, headers=admin_headers)
    assert r.status_code == 200
    html = r.text
    assert "节能改造提案" in html and "预计年节省" in html
    assert "昊青气体装备官方销售商" in html and "选型计算书" in html
    assert "print" in html, "打印样式就位"
