"""SPEC-BR 回归：江苏备案通道——解析/噪音/单位抽取/入库幂等。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-br-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.collectors import proposal_js as js_mod  # noqa: E402
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


def test_br_parse_rows():
    """HTML 行解析：项目代码剥离/日期归一/短行跳过。"""
    html = """
    <table><tr><th>项目</th><th>部门</th><th>证号</th><th>日期</th></tr>
    <tr><td>2611-320826-04-01-651808 涟水县某某公司创新创业园3.2MW项目</td>
    <td>淮安涟水县发展改革委</td><td>涟水发改备〔2026〕4472号</td><td>2026/11/06</td></tr>
    <tr><td>苏州科里菲尔光学科技有限公司新建生产真空镀膜机项目</td>
    <td>苏州市相城区渭塘镇人民政府</td><td>渭政投备〔2026〕44号</td><td>2026/11/05</td></tr>
    <tr><td></td><td>x</td><td>y</td><td>z</td></tr>
    </table>"""
    rows = js_mod._parse_rows(html)
    assert len(rows) == 2
    assert rows[0]["code"] == "2611-320826-04-01-651808"
    assert rows[0]["project"].startswith("涟水县某某公司")
    assert rows[0]["ba_date"] == "2026-11-06"
    assert rows[1]["code"] == "" and rows[1]["project"].startswith("苏州科里菲尔")


def test_br_scan_ingest_and_filters(monkeypatch, client, admin_headers):
    """入库+省份补齐+噪音/无单位跳过+幂等重扫+center 三通道徽章。"""
    calls: list[str] = []

    def fake_fetch(keyword: str, page_no: int = 1, page_size: int = 100, client=None):
        calls.append(keyword)
        if keyword != "真空泵":
            return []
        return [
            {   # 企业名开头 → 可抽取
                "code": "2611-320500-89-01-000001",
                "project": "BR苏州真空设备有限公司干式真空泵扩产项目",
                "approve_org": "苏州工业园区管理委员会",
                "beian_no": "苏园管备〔2026〕1号",
                "ba_date": "2026-11-07",
            },
            {   # 光伏使用侧噪音（采集端已滤——模拟漏网断言不成立，故此条应被上层忽略? 不，collector 已滤）
            },
        ]

    # 直接模拟 collector 输出（噪音已在 collector 滤除，这里给一条无单位的）
    def fake_fetch2(keyword: str, page_no: int = 1, page_size: int = 100, client=None):
        calls.append(keyword)
        if keyword != "真空泵":
            return []
        return [
            {
                "code": "2611-320500-89-01-000001",
                "project": "BR苏州真空设备有限公司干式真空泵扩产项目",
                "approve_org": "苏州工业园区管理委员会",
                "beian_no": "苏园管备〔2026〕1号",
                "ba_date": "2026-11-07",
            },
            {
                "code": "2611-329900-89-01-000002",
                "project": "创新创业园区基础设施提升项目",
                "approve_org": "某管委会",
                "beian_no": "某备〔2026〕2号",
                "ba_date": "2026-11-07",
            },
        ]

    from app import jobs as jobs_mod

    monkeypatch.setattr(js_mod, "fetch_js_beian", fake_fetch2)
    monkeypatch.setattr(jobs_mod, "run_subscription_rules", lambda session: {"matched": 0})

    stats = jobs_mod.run_proposal_js_scan(max_pages=1)
    assert stats["new_signals"] == 1  # 无单位的被跳过
    assert stats["no_unit_skipped"] >= 1

    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(Enterprise.name == "BR苏州真空设备有限公司").first()
        assert ent is not None and ent.province == "江苏省"
        assert ent.source == "proposal_js"
        sig = (
            s.query(Signal)
            .filter(Signal.enterprise_id == ent.id, Signal.type == "proposal")
            .first()
        )
        assert sig is not None
        assert "干式真空泵扩产" in sig.evidence_claim
        assert sig.pub_date == "2026-11-07"
        sig_id = sig.id
        ent_id = ent.id

    # 幂等重扫：不再新增
    stats2 = jobs_mod.run_proposal_js_scan(max_pages=1)
    assert stats2["new_signals"] == 0

    # center 三通道徽章
    r = client.get("/api/proposals/center?q=BR苏州真空", headers=admin_headers)
    items = r.json()["items"]
    assert items and items[0]["channel"] == "js"

    # 清理（fixture 快照只按 id>，别名需显式清）
    with SessionLocal() as s:
        s.query(Signal).filter(Signal.id == sig_id).delete()
        s.query(EnterpriseAlias).filter(EnterpriseAlias.enterprise_id == ent_id).delete()
        s.query(Enterprise).filter(Enterprise.id == ent_id).delete()
        s.commit()


def test_br_noise_filter_reuse():
    """噪音过滤器复用自湖北通道（光伏使用侧滤/制造侧保留）。"""
    from app.collectors.proposal_js import is_residential_noise

    assert is_residential_noise("某村屋顶分布式光伏发电项目") is True
    assert is_residential_noise("光伏装备制造产业园项目") is False
    assert is_residential_noise("干式真空泵扩产项目") is False
