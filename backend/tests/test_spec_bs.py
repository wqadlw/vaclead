"""SPEC-BS 回归：浙江办理结果公示通道——文号剥离/JSON 解析/事项维度/幂等。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bs-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import proposal_zj as zj_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    EnterpriseAlias,
    Signal,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Signal, EnterpriseAlias, AuditLog]


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


def test_bs_docno_strip_and_noise():
    """文号前缀剥离 + 噪音复用（湖北同款）。"""
    assert zj_mod.strip_docno_prefix(
        "富政工出【2025】16号浙江某半导体有限公司新建厂房项目"
    ) == "浙江某半导体有限公司新建厂房项目"
    assert zj_mod.strip_docno_prefix("年产2000套低温真空泵生产线建设项目") == (
        "年产2000套低温真空泵生产线建设项目"
    )
    from app.collectors.proposal_zj import is_residential_noise

    assert is_residential_noise("某村屋顶光伏发电项目") is True
    assert is_residential_noise("低温真空泵生产线建设项目") is False


def test_bs_scan_ingest_and_idempotent(monkeypatch, client, admin_headers):
    """扫描入库：单位抽取/省份补齐/事项维度 evidence/幂等/徽章 zj。"""

    def fake_fetch(keyword: str, page_no: int = 1, client=None):
        if keyword != "真空泵" or page_no > 1:
            return []
        return [
            {
                "deal_code": "2609-330226-04-01-767973",
                "project": "BS宁波低温设备有限公司年产2000套低温真空泵生产线建设项目",
                "item_name": "企业投资（含外商投资）项目备案（技术改造）",
                "deal_name": "已办结",
                "deal_time": "2026-10-22",
                "dept_name": "宁海县自然资源和规划局",
            },
            {   # 无企业名（纯产能描述）→ 如实跳过
                "deal_code": "2610-330100-04-01-000002",
                "project": "年产100万套真空计陀螺仪研发与制造配套项目",
                "item_name": "企业投资项目备案",
                "deal_name": "已办结",
                "deal_time": "2026-10-13",
                "dept_name": "某区发改局",
            },
        ]

    from app import jobs as jobs_mod

    monkeypatch.setattr(zj_mod, "fetch_zj_items", fake_fetch)

    stats = jobs_mod.run_proposal_zj_scan(max_pages=2)
    assert stats["new_signals"] == 1
    assert stats["no_unit_skipped"] == 1

    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(
            Enterprise.name == "BS宁波低温设备有限公司"
        ).first()
        assert ent is not None and ent.province == "浙江省"
        assert ent.source == "proposal_zj"
        sig = (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.type == "proposal")
            .one()
        )
        assert "项目备案" in sig.title  # 事项名入标题
        assert "备案（技术改造）" in sig.evidence_claim and "已办结" in sig.evidence_claim
        assert sig.pub_date == "2026-10-22"
        sig_id, ent_id = sig.id, ent.id

    # 幂等重扫（含 page_no=2 空页不炸）
    stats2 = jobs_mod.run_proposal_zj_scan(max_pages=2)
    assert stats2["new_signals"] == 0

    # center 徽章 zj
    r = client.get("/api/proposals/center?q=BS宁波低温", headers=admin_headers)
    items = r.json()["items"]
    assert items and items[0]["channel"] == "zj"

    with SessionLocal() as s:
        s.query(Signal).filter(Signal.id == sig_id).delete()
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == ent_id).delete()
        s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
        s.commit()
