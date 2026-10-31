"""SPEC-O 回归：报表中心（O1 周报/CSV）+ 筛选订阅（O2 filter 规则）。"""
from __future__ import annotations

import csv as _csv
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-o-"), "t.db")
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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


@pytest.fixture(scope="module")
def seeded():
    """上周窗口种子（offset=0 即查上周）：2 企业/2 信号/2 线索/1 跟进/1 赢单；上上周为空。"""
    from app.api.reports import _dt, _week_bounds
    from app.db import SessionLocal
    from app.models import Enterprise, Followup, Lead, Signal

    start, end = _week_bounds(0)  # 上周窗口 [start, end)
    mid = start + timedelta(days=3)
    lo, hi = _iso(datetime(mid.year, mid.month, mid.day, 12)), _iso(end)
    far_before = _iso(datetime(start.year, start.month, start.day) - timedelta(days=10))

    with SessionLocal() as s:
        e_old = Enterprise(name="周报老牌机械有限公司", province="江苏省",
                           industry_key="machining", source="csv",
                           created_at=lo)
        e_new = Enterprise(name="周报新锐半导体有限公司", province="江苏省",
                           industry_key="semiconductor", source="ggzy", icp_score=80,
                           intent_score=60, created_at=lo)
        s.add_all([e_old, e_new])
        s.flush()
        for o in (e_old, e_new):
            s.add(Signal(enterprise_id=o.id, type="tender_win",
                         title=f"{o.name}真空泵中标", url="",
                         raw_excerpt="中标｜成交价100.5万元" if o is e_new else "",
                         evidence_claim="证据句", base_score=70,
                         created_at=lo))
        lead_won = Lead(enterprise_id=e_old.id, status="won", amount_wan=320.5,
                        created_at=far_before, updated_at=lo)
        lead_new = Lead(enterprise_id=e_new.id, status="intent",
                        created_at=lo, updated_at=lo)
        s.add_all([lead_won, lead_new])
        s.flush()
        s.add(Followup(lead_id=lead_new.id, content="电话初访", created_at=lo))
        s.commit()
        ids = {"lead_won": lead_won.id, "lead_new": lead_new.id}
    yield {"bounds": (lo, hi), **ids}


def test_weekly_report_kpis_and_tops(client, admin_headers, seeded):
    r = client.get("/api/reports/weekly", params={"offset": 0}, headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    kpis = {k["key"]: k for k in d["kpis"]}
    kdump = {k["key"]: (k["value"], k["prev"]) for k in d["kpis"]}
    # 上周种子 vs 上上周（空）→ 全部 prev=0、delta=1.0
    assert kpis["new_enterprises"]["value"] == 2 and kpis["new_enterprises"]["prev"] == 0, kdump
    assert kpis["new_signals"]["value"] == 2, kdump
    assert kpis["new_leads"]["value"] == 1, kdump  # lead_won 建于窗口外，仅本周赢单（按 updated_at 计）
    assert kpis["followups"]["value"] == 1
    assert kpis["won_count"]["value"] == 1
    assert kpis["won_amount_wan"]["value"] == 320.5
    assert kpis["new_enterprises"]["delta"] == 1.0

    # funnel 含赢单 1
    won = next(f for f in d["funnel"] if f["status"] == "won")
    assert won["value"] == 1
    # top_signals 有数据且含企业名；top_customers 综合分排序（80*0.4+60*0.6=68）
    assert d["top_signals"], "Top 标讯为空"
    assert d["top_customers"][0]["composite"] == 68.0
    assert d["highlights"], "highlights 为空"


def test_weekly_export_csv_parses(client, admin_headers, seeded):
    r = client.get("/api/reports/weekly/export", params={"offset": 0}, headers=admin_headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    rows = list(_csv.reader(io.StringIO(r.content.decode("utf-8"))))
    assert any("== 核心指标 ==" in c for row in rows for c in row)
    assert any("== Top 标讯 ==" in c for row in rows for c in row)


def test_weekly_empty_far_offset(client, admin_headers):
    r = client.get("/api/reports/weekly", params={"offset": 8}, headers=admin_headers)
    assert r.status_code == 200
    kpis = {k["key"]: k for k in r.json()["kpis"]}
    assert kpis["new_leads"]["value"] == 0


# ---------------- O2 筛选订阅 ----------------

def test_filter_subscription_validation_and_matching(client, admin_headers):
    # 非法 JSON / 超白名单字段 → 422
    assert client.post("/api/subscription-rules",
                       json={"kind": "filter", "value": "{bad"},
                       headers=admin_headers).status_code == 422
    assert client.post("/api/subscription-rules",
                       json={"kind": "filter", "value": json.dumps({"hacker": 1})},
                       headers=admin_headers).status_code == 422

    cond = {"type": "tender_win", "q": "分子泵"}
    r = client.post("/api/subscription-rules",
                    json={"kind": "filter", "value": json.dumps(cond)},
                    headers=admin_headers)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    # 重复 409
    assert client.post("/api/subscription-rules",
                       json={"kind": "filter", "value": json.dumps(cond)},
                       headers=admin_headers).status_code == 409

    from app.db import SessionLocal
    from app.jobs import run_subscription_rules
    from app.models import Enterprise, Notification, Signal

    with SessionLocal() as s:
        ent = Enterprise(name="筛选订阅回归真空设备有限公司", province="江苏省",
                         industry_key="machining")
        s.add(ent)
        s.flush()
        hit = Signal(enterprise_id=ent.id, type="tender_win",
                     title="分子泵机组采购中标公告", url="", base_score=70)
        miss = Signal(enterprise_id=ent.id, type="tender",
                      title="绿化养护服务公告", url="", base_score=50)
        s.add_all([hit, miss])
        s.commit()
        eid = ent.id

        out = run_subscription_rules(s)
        assert out["matched"] >= 1
        s.flush()
        notes = (
            s.query(Notification)
            .filter(Notification.ref_type == "sub_signal", Notification.user_id == 1)
            .all()
        )
        # 只有满足条件的信号被通知，且标题带条件摘要
        hit_ids = {n.ref_id for n in notes if "分子泵" in n.title or "筛选" in n.title}
        assert hit.id in hit_ids
        assert miss.id not in hit_ids
        # 幂等：重扫无新增
        run_subscription_rules(s)
        count_after = (
            s.query(Notification)
            .filter(Notification.ref_type == "sub_signal", Notification.user_id == 1)
            .count()
        )
        assert count_after == len(notes)

        s.query(Signal).filter(Signal.enterprise_id == eid).delete()
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()

    assert client.delete(f"/api/subscription-rules/{rid}",
                         headers=admin_headers).status_code == 200
