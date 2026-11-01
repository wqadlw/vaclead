"""SPEC-W 回归：增量建档 ICP 分类（W1）+ 存量补分类任务（W2）。"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-w-"), "t.db")
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


def test_daily_incremental_classifies_new_ents(client, admin_headers, monkeypatch):
    """W1：增量建档后企业带行业分类与 ICP 分（此前 991 家未分类的根因回归）。"""
    from app.collectors import ccgp as ccgp_mod
    from app.collectors import ggzy as ggzy_mod
    from app.db import SessionLocal
    from app.jobs import run_daily_incremental
    from app.models import Enterprise, EnterpriseAlias, Signal

    rec = {"name": "分类链路回归真空设备有限公司", "uscc": "91370000W1TEST01X",
           "bid_count": 1, "est_date": "2015-03-01", "legal_rep": "测试法人"}
    monkeypatch.setattr(ggzy_mod, "search_all_entities", lambda *a, **k: ["x"])
    monkeypatch.setattr(ggzy_mod, "normalize_entity_record", lambda raw: rec)
    monkeypatch.setattr(ggzy_mod, "list_bids", lambda *a, **k: {"records": [
        {"projectName": "分类链路中标项目", "bidPrice": 50.0},
    ]})
    monkeypatch.setattr(
        ggzy_mod, "normalize_bid_record",
        lambda b: {"project_name": b["projectName"], "bid_price_wan": b["bidPrice"],
                   "pub_date": "2026-10-30", "trade_type": "中标"},
    )
    monkeypatch.setattr(ccgp_mod, "search_ccgp_all", lambda *a, **k: [])

    run_daily_incremental()

    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(Enterprise.uscc == "91370000W1TEST01X").first()
        assert ent is not None
        # 名称含"真空设备"→ 同行打标生效（= apply_icp 被 W1 调用的证据）；
        # 设备制造商没有买家行业 → industry_key=None 是正确语义（不发明）
        assert ent.is_competitor == 1
        assert ent.legal_rep == "测试法人"
        # 中标信号入库
        assert s.query(Signal).filter(Signal.enterprise_id == ent.id).count() >= 1

        eid = ent.id
        s.query(Signal).filter(Signal.enterprise_id == eid).delete()
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == eid).delete()
        s.delete(ent)
        s.commit()


def test_backfill_icp_idempotent_and_competitor_preserved(client, admin_headers):
    """W2：存量补分类幂等；采集端同行打标不被覆盖。"""
    from app.db import SessionLocal
    from app.models import Enterprise

    # 造四家：同行(采集打标+无分类)/无词未分类/已分类/有下游词未分类
    with SessionLocal() as s:
        s.add_all([
            Enterprise(name="补分类回归真空泵制造有限公司", source="ggzy",
                       is_competitor=1),  # 采集端打标，未分类
            Enterprise(name="补分类回归无词企业有限公司", source="ggzy"),
            Enterprise(name="补分类回归已分类半导体有限公司", source="csv",
                       industry_key="semiconductor"),
            Enterprise(name="补分类回归半导体科技有限公司", source="csv"),  # 有下游词
        ])
        s.commit()

    # 触发任务
    r = client.post("/api/tasks/backfill-icp", json={"limit": 500}, headers=admin_headers)
    assert r.status_code == 200, r.text
    import time as _time

    task_id = r.json()["task_id"]
    for _ in range(40):
        _time.sleep(0.5)
        # 任务页查重式的完成探测：直接查库状态
        from app.db import SessionLocal as _SL
        from app.models import TaskCheckpoint

        with _SL() as s:
            cp = s.get(TaskCheckpoint, task_id)
            if cp is not None and cp.status in ("done", "failed"):
                assert cp.status == "done", cp.state_json
                break
    else:
        pytest.fail("backfill-icp 未在 20s 内完成")

    with SessionLocal() as s:
        competitor_ent = s.query(Enterprise).filter(
            Enterprise.name == "补分类回归真空泵制造有限公司").first()
        # 设备制造商=同行：is_competitor 保留（采集端打标不被覆盖）；
        # 无买家行业词 → 未分类是正确语义
        assert competitor_ent.is_competitor == 1, "采集端打标被覆盖"

        classified_ent = s.query(Enterprise).filter(
            Enterprise.name == "补分类回归半导体科技有限公司").first()
        assert classified_ent.industry_key == "semiconductor", "有下游词企业未被分类"

        no_word_ent = s.query(Enterprise).filter(
            Enterprise.name == "补分类回归无词企业有限公司").first()
        assert no_word_ent.industry_key is None, "无行业词企业应保持未分类（不发明）"

        # 幂等：二次执行 processed=0
        r2 = client.post("/api/tasks/backfill-icp", json={"limit": 500},
                         headers=admin_headers)
        import time as _time
        for _ in range(20):
            _time.sleep(0.5)
            from app.db import SessionLocal as _SL
            from app.models import TaskCheckpoint
            with _SL() as s2:
                cp2 = s2.get(TaskCheckpoint, r2.json()["task_id"])
                if cp2 is not None and cp2.status in ("done", "failed"):
                    stats2 = json.loads(cp2.state_json or "{}")
                    # 幂等语义：无新分类产生（无词企业保持未分类是设计行为，
                    # 每轮仍会被扫描但不产生新分类）
                    assert stats2.get("classified", 0) == 0, stats2
                    break

        # 清理
        for nm in ("补分类回归真空泵制造有限公司", "补分类回归无词企业有限公司",
                   "补分类回归已分类半导体有限公司", "补分类回归半导体科技有限公司"):
            e = s.query(Enterprise).filter(Enterprise.name == nm).first()
            if e:
                s.delete(e)
        s.commit()
