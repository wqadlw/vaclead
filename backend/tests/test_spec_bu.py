"""SPEC-BU 回归：竞对中标推送 + 周报雷达节。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bu-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.jobs import competitor_alert_sweep  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    Notification,
    Signal,
    SyncWatermark,
    User,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Signal, Notification, User, AuditLog]


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
        s.query(SyncWatermark).filter(
            SyncWatermark.job_name == "competitor_alert"
        ).delete()
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


def test_bu_competitor_alert(client, admin_headers):
    with SessionLocal() as s:
        comp = Enterprise(name="BU竞对泵业公司", source="poi", is_competitor=1)
        normal = Enterprise(name="BU普通中标企业", source="poi")
        s.add_all([comp, normal])
        s.flush()
        s.add_all([
            Signal(enterprise_id=comp.id, type="tender_win",
                   title="某单位旋片泵采购成交价66.5万元", url="",
                   evidence_claim="采购人：BU光电材料有限公司，成交价66.5万元"),
            Signal(enterprise_id=normal.id, type="tender_win",
                   title="普通企业成交价10万元", url=""),
        ])
        s.commit()
        comp_id, normal_id = comp.id, normal.id

    def _run():
        from app.main import _run_job_with_watermark

        _run_job_with_watermark("competitor_alert", competitor_alert_sweep)
        with SessionLocal() as s:
            row = s.get(SyncWatermark, "competitor_alert")
            return {"status": row.last_status, **__import__("json").loads(row.stats_json or "{}")}

    # 基线（无水位）：只记游标不推
    stats = _run()
    assert stats["status"] == "ok" and stats.get("baseline") is True

    # 新竞对中标 → 推 admin
    with SessionLocal() as s:
        comp = s.get(Enterprise, comp_id)
        s.add(Signal(enterprise_id=comp_id, type="tender_win",
                     title="BU竞对新中标成交价88万元", url="",
                     evidence_claim="采购人：BU买方有限公司"))
        s.commit()
    stats = _run()
    assert stats["notified"] >= 1
    with SessionLocal() as s:
        n = (
            s.query(Notification)
            .filter(Notification.ref_type == "competitor_win",
                    Notification.title.like("竞对中标：BU竞对泵业公司%"))
            .one()
        )
        assert "88 万" in n.body and "卖给了 BU买方有限公司" in n.body
        # 非竞对中标不推
        assert (
            s.query(Notification)
            .filter(Notification.ref_type == "competitor_win",
                    Notification.title.like("%普通中标企业%"))
            .count() == 0
        )

    # 幂等重扫：游标推进后 0 新通知
    stats = _run()
    assert stats["notified"] == 0

    # 失败轮丢游标 → 全量重扫：合法补推基线期漏掉的旧竞对中标（非重复），
    # 再扫一次必须零新增（这才是去重不变式）
    with SessionLocal() as s:
        row = s.get(SyncWatermark, "competitor_alert")
        row.stats_json = '{"error": "boom"}'
        s.commit()
    stats = _run()
    with SessionLocal() as s:
        total_after_recovery = (
            s.query(Notification)
            .filter(Notification.ref_type == "competitor_win")
            .count()
        )
    assert stats["notified"] >= 1  # 基线跳过的 66.5 万中标被合法补推
    stats = _run()
    assert stats["notified"] == 0
    with SessionLocal() as s:
        assert (
            s.query(Notification)
            .filter(Notification.ref_type == "competitor_win")
            .count() == total_after_recovery
        )


def test_bu_weekly_radar_section(client, admin_headers):
    r = client.get("/api/reports/weekly", headers=admin_headers)
    assert r.status_code == 200
    radar = r.json()["radar"]
    assert radar["competitor_wins_30d"] >= 1  # 上一测试种了 2 条竞对中标（30 天窗口内）
    assert radar["competitor_wins_amount_wan"] >= 88
    assert radar["proposals_total"] >= 0
    assert radar["contact_with_phone"] >= 0
