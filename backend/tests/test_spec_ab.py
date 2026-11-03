"""SPEC-AB 回归：广东拟建/备案通道（采集器→建档→proposal 信号→幂等）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ab-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    from app.db import init_db

    init_db()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


def test_proposal_gd_scan_collect_and_idempotent(monkeypatch):
    """备案公示 → 建档 + proposal 信号；重复扫描幂等。"""
    from app.collectors import proposal_gd as pg
    from app.db import SessionLocal
    from app.jobs import run_proposal_gd_scan
    from app.models import Enterprise, Signal

    payload = {"code": "0", "data": {"list": [
        {"deptName": "拟建通道测试食品有限公司", "baTime": "2026-11-01",
         "baNum": 2, "baCode": "91440101ABTEST123-26", "workTime": 2026},
        {"deptName": "拟建通道测试设备有限公司", "baTime": "2026-11-02",
         "baNum": 1, "baCode": "91440101ABTEST456-26", "workTime": 2026},
    ], "totalRow": 2}}
    monkeypatch.setattr(pg, "fetch_proposal_page", lambda page_no=1, page_size=20,
                        client=None: [
        {"name": r["deptName"], "ba_time": r["baTime"], "ba_code": r["baCode"],
         "ba_num": r["baNum"], "work_time": r["workTime"]}
        for r in payload["data"]["list"]
    ] if page_no == 1 else [])

    stats = run_proposal_gd_scan(max_pages=1)
    assert stats["records"] == 2 and stats["new_signals"] == 2 and stats["new_ents"] == 2

    with SessionLocal() as s:
        ents = s.query(Enterprise).filter(
            Enterprise.source == "proposal_gd").all()
        assert len(ents) == 2
        sigs = s.query(Signal).filter(Signal.type == "proposal").all()
        assert all("广东项目备案" in x.title for x in sigs)
        assert all("在线审批监管平台" in (x.evidence_claim or "") for x in sigs)

        # 幂等：重扫不新增
        stats2 = run_proposal_gd_scan(max_pages=1)
        assert stats2["new_signals"] == 0 and stats2["new_ents"] == 0

        for e in ents:
            s.query(Signal).filter(Signal.enterprise_id == e.id).delete()
            from app.models import EnterpriseAlias

            s.query(EnterpriseAlias).filter(
                EnterpriseAlias.enterprise_id == e.id).delete()
            s.delete(e)
        s.commit()


def test_proposal_signals_visible_in_bulletin(client, admin_headers):
    """标讯中心 proposal 类型可筛选（TYPE_CN 映射）。"""
    from app.db import SessionLocal
    from app.models import Enterprise, Signal

    with SessionLocal() as s:
        ent = Enterprise(name="标讯备案测试有限公司", source="proposal_gd")
        s.add(ent)
        s.flush()
        s.add(Signal(enterprise_id=ent.id, type="proposal",
                     title="广东项目备案：标讯备案测试有限公司（2026年度）",
                     url="https://tzxm.gd.gov.cn/PublicityInformation/"
                         "ConsutingCpResultsList.html",
                     pub_date="2026-11-01", base_score=40))
        s.commit()
        eid = ent.id

    d = client.get("/api/signals", params={"type": "proposal", "days": 30},
                   headers=admin_headers).json()
    assert d["total"] >= 1
    row = d["items"][0]
    assert row["type_cn"] == "项目备案"
    assert row["ent_name"] == "标讯备案测试有限公司"

    with SessionLocal() as s:
        s.query(Signal).filter(Signal.enterprise_id == eid).delete()
        s.query(Enterprise).filter(Enterprise.id == eid).delete()
        s.commit()
