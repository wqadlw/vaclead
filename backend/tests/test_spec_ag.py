"""SPEC-AG 回归：湖北拟建/备案通道（extoken 公式→采集解析→建档→幂等）。"""
from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-ag-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Enterprise,
    SettingsKV,
    Signal,
)

init_db(seed=False)

_ISOLATED_MODELS: list[type] = [Signal, Enterprise]


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


# ---------------- AG1 extoken 公式 ----------------
def test_extoken_formula_decodable():
    """extoken 应可逆解码回当前时间窗：((ms%1e9+k*1e9)*911+20170706)。"""
    from app.collectors.proposal_hb import _CE, _WE, _XE, b36, compute_extoken

    now_ms = int(time.time() * 1000)
    for k in (0, 3, 9):
        tok = compute_extoken(now_ms=now_ms, k=k)
        assert 6 <= len(tok) <= 12 and tok.isalnum()
        # base36 反解
        n = int(tok, 36)
        raw = n - _WE
        assert raw % _XE == 0, "解出的数应满足 ×911+20170706 关系"
        base = raw // _XE
        assert base % _CE == now_ms % _CE, "低位应等于 ms%1e9"
        assert 0 <= base // _CE <= 9, "高位应∈{0..9}"

    assert b36(0) == "0" and b36(35) == "z" and b36(36) == "10"


def test_extoken_freshness_window():
    """两次调用（间隔真实毫秒）解出的时间都在近窗内。"""
    from app.collectors.proposal_hb import _CE, _WE, _XE, compute_extoken

    def decode(tok: str) -> int:
        return ((int(tok, 36) - _WE) // _XE) % _CE

    t0 = decode(compute_extoken())
    time.sleep(0.05)
    t1 = decode(compute_extoken())
    now_ms = int(time.time() * 1000) % _CE
    assert abs(t1 - now_ms) < 5000, "extoken 应贴近当前毫秒（服务端按窗校验）"
    assert t1 >= t0 - 1000


# ---------------- AG2 采集解析 + 入库 ----------------
class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._p = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._p


class _FakeClient:
    def __init__(self, payload: dict):
        self._p = payload
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(self._p)


def test_fetch_hb_filing_page_parses(monkeypatch):
    from app.collectors import proposal_hb as ph

    # W51 噪音校准：使用侧光伏过滤、制造侧保留
    assert ph.is_residential_noise("吕家河村2组姚中有（交流侧30KW）庭院分布式光伏发电项目")
    assert ph.is_residential_noise("谷城县超凡纺织有限公司400kw分布式光伏发电项目")
    assert ph.is_residential_noise("肖松柏房屋40kW屋顶分布式光伏发电项目")
    assert not ph.is_residential_noise("光伏装备制造产业园项目")  # 制造侧=真前兆
    assert not ph.is_residential_noise("年产500台真空获得设备扩建项目")

    payload = {
        "res": 0,
        "data": {
            "total": 2,
            "list": [
                {
                    "unit_name": "湖北示例机械制造有限公司",
                    "project_name": "年产 500 台真空获得设备扩建项目",
                    "declaration_date": "2026-11-04 09:00:00",
                    "project_code": "2611-420100-04-01-000001",
                },
                {"unit_name": "ab", "project_name": "过短单位跳过"},
                {"unit_name": "某某新能源开发有限公司", "project_name": "庭院分布式光伏发电项目"},
            ],
        },
    }
    fc = _FakeClient(payload)
    rows = ph.fetch_hb_filing_page(1, client=fc)
    assert len(rows) == 1  # 过短单位 + 光伏使用侧被滤
    assert rows[0]["unit"] == "湖北示例机械制造有限公司"
    assert "真空获得设备" in rows[0]["project"]
    # 请求头带 extoken/compkey
    h = fc.calls[0]["headers"]
    assert "extoken" in h and h["compkey"] == "hb" and h["authtoken"] == "null"
    # 错误码返回空列表
    assert ph.fetch_hb_filing_page(1, client=_FakeClient({"res": 400007, "data": {}})) == []


def test_proposal_hb_scan_idempotent(monkeypatch):
    """备案查询 → 建档 + 项目级 proposal 信号；重复扫描幂等（SPEC-AG AG2）。"""
    from app.collectors import proposal_hb as ph
    from app.jobs import run_proposal_hb_scan

    records = [
        {
            "unit": "鄂州示例真空装备有限公司",
            "project": "高频真空镀膜生产线扩建项目",
            "declared_at": "2026-11-04 08:30:00",
            "project_code": "2611-420700-04-01-000002",
        },
        {
            "unit": "湖北食品机械有限公司",
            "project": "杀菌釜生产线智能化改造项目",
            "declared_at": "2026-11-03 17:10:00",
            "project_code": "2611-420600-04-05-000003",
        },
    ]
    monkeypatch.setattr(
        ph, "fetch_hb_filing_page",
        lambda page_no=1, page_size=20, client=None: records if page_no == 1 else [],
    )
    stats = run_proposal_hb_scan(max_pages=1)
    assert stats["records"] == 2 and stats["new_ents"] == 2 and stats["new_signals"] == 2
    with SessionLocal() as s:
        ents = s.query(Enterprise).filter(Enterprise.source == "proposal_hb").all()
        assert len(ents) == 2
        sigs = s.query(Signal).filter(Signal.type == "proposal").all()
        titles = [x.title for x in sigs]
        assert any("高频真空镀膜生产线扩建项目" in t and t.startswith("湖北项目备案") for t in titles)
        assert any("杀菌釜" in t for t in titles)  # 项目级标题（广东 P2 边界在湖北可得）
        for sig in sigs:
            assert sig.evidence_claim and "湖北省投资项目" in sig.evidence_claim
    # 幂等
    stats2 = run_proposal_hb_scan(max_pages=1)
    assert stats2["new_ents"] == 0 and stats2["new_signals"] == 0


def test_proposal_chains_apply_icp(monkeypatch):
    """W40 教训回归：GD/HB 建档链必须接 ICP 分类（industry_key 空才调）。"""
    from app.collectors import proposal_hb as ph
    from app.db import SessionLocal
    from app.jobs import run_proposal_hb_scan
    from app.models import Enterprise

    calls: list[str] = []

    real_apply = None
    from app.api import imports as imports_mod

    real_apply = imports_mod.apply_icp

    def spy(ent):
        calls.append(ent.name)
        return real_apply(ent)

    monkeypatch.setattr(imports_mod, "apply_icp", spy)
    records = [{
        "unit": "宜昌测试检测技术有限公司",
        "project": "检验检测实验室建设项目",
        "declared_at": "2026-11-04 10:00:00",
        "project_code": "2611-420500-04-06-000009",
    }]
    monkeypatch.setattr(
        ph, "fetch_hb_filing_page",
        lambda page_no=1, page_size=20, client=None: records if page_no == 1 else [],
    )
    run_proposal_hb_scan(max_pages=1)
    assert len(calls) == 1, "新企业建档必须触发 apply_icp（W40 教训）"
    with SessionLocal() as s:
        ent = s.query(Enterprise).filter(Enterprise.name == "宜昌测试检测技术有限公司").first()
        assert ent is not None and ent.industry_key is not None, "检测类企业应被归类"


# ---------------- 标讯中心可见性（沿用 AB 的 proposal chip） ----------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_hb_signals_in_bulletin(client, admin_headers):
    d = client.get("/api/signals", params={"type": "proposal"}, headers=admin_headers).json()
    assert any("湖北项目备案" in it["title"] for it in d["items"]), "湖北项目级备案应可按 proposal 筛出"
