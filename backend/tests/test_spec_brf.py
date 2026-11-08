"""SPEC-BRF 回归：客户作战简报（六块大聚合 JSON + A4 打印页）。"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-brf-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.api.brief import _intent_band  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Contact,
    Enterprise,
    Followup,
    InstalledUnit,
    Lead,
    Signal,
)

init_db(seed=False)

# 六分区销售语言标题（打印页断言用，与 docs/SPEC-BRF.md §1 口径一致）
SECTION_TITLES = (
    "这家公司是干嘛的",
    "他们在买什么",
    "为什么现在打",
    "打给谁",
    "谈资",
    "下一步",
)


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    """模块级行隔离：只清理本模块种子新增的行（先子后父，防 FK 残留）。"""
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in (Signal, Contact, InstalledUnit, Followup, Lead, Enterprise):
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in (Signal, Contact, InstalledUnit, Followup, Lead, Enterprise):
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
        s.commit()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def seeded():
    """种子：目标企业（high 档，五源数据齐挂）+ 竞对 + 同行成交 + mid/low 档企业。"""
    d = lambda days: (date.today() - timedelta(days=days)).isoformat()
    with SessionLocal() as s:
        e = Enterprise(
            name="BRF作战目标真空装备有限公司", industry_key="machining",
            industry_name="真空装备制造", province="江苏省", city="苏州市",
            registered_capital_wan=5000.0, business_scope="真空镀膜设备研发制造",
            uscc="91320500BRFTEST01X", icp_score=80.0, intent_score=68.0, source="csv",
        )
        s.add(e)
        s.flush()
        # 中标史 ×3（pub_date 乱序插入，验证 wins 按 pub_date desc）+ 招标 + 拟建/备案
        s.add_all([
            Signal(enterprise_id=e.id, type="tender_win",
                   title="BRF目标中标半导体镀膜线项目", url="", pub_date="2026-11-01",
                   base_score=75.0, raw_excerpt="中标｜成交价300万元",
                   evidence_claim=("该主体在公共资源交易平台中标「半导体镀膜线」，"
                                   "采购人：苏州微电子研究所，成交价300万元")),
            Signal(enterprise_id=e.id, type="tender",
                   title="BRF目标发布真空腔体招标公告", url="", pub_date="2026-10-20",
                   base_score=55.0),
            Signal(enterprise_id=e.id, type="proposal",
                   title="广东项目备案：BRF扩产项目（2026年度）", url="", pub_date="2026-10-05",
                   base_score=40.0, evidence_claim="该单位在广东省完成项目备案"),
            Signal(enterprise_id=e.id, type="tender_win",
                   title="BRF目标中标检测设备采购项目", url="", pub_date="2026-10-01",
                   base_score=70.0, raw_excerpt="中标｜成交价150万元",
                   evidence_claim="该主体中标「检测设备采购」，采购人：苏州工业园区管委会，成交价150万元"),
            Signal(enterprise_id=e.id, type="tender_win",
                   title="BRF目标中标老项目", url="", pub_date="2026-09-15",
                   base_score=65.0, raw_excerpt="中标｜成交价120万元",
                   evidence_claim="该主体中标「老项目」，采购人：常熟某材料厂，成交价120万元"),
        ])
        # 联系人 ×4：可拨决策人 / 可拨已拨通 / 决策执行者无号 / 无角色无号（排序验证）
        s.add_all([
            Contact(enterprise_id=e.id, name="张董", role="buyer_contact", title="董事长",
                    decision_role="decision_maker", phones_json='["0512-66668888"]',
                    call_result="pending", source="ggzy", evidence_claim="公告公开联系人"),
            Contact(enterprise_id=e.id, name="李工", role="project_contact", title="设备科",
                    phones_json='["13912345678"]', call_result="connected", source="manual"),
            Contact(enterprise_id=e.id, name="王主任", role="buyer_unit", title="采购中心主任",
                    decision_role="handler", phones_json="[]", source="manual"),
            Contact(enterprise_id=e.id, name="赵秘书", role="tender_agency",
                    phones_json="[]", source="manual"),
        ])
        # 装机 ×2（一条无装机日期，验证排序兜底）
        s.add_all([
            InstalledUnit(enterprise_id=e.id, model="CX-100D", install_date="2025-06-01"),
            InstalledUnit(enterprise_id=e.id, model="CX-63", install_date=None),
        ])
        lead = Lead(enterprise_id=e.id, status="following", owner_id=1)
        s.add(lead)
        s.flush()
        s.add_all([
            Followup(lead_id=lead.id, content="电话初访：对接设备科",
                     created_at="2026-10-10T09:00:00"),
            Followup(lead_id=lead.id, content="寄送选型资料",
                     created_at="2026-10-20T14:30:00"),
        ])
        # 竞对：30 天内抢走目标企业的单 + 40 天前的旧单（窗口过滤验证）
        comp = Enterprise(name="BRF竞对泵业有限公司", industry_key="machining",
                          is_competitor=1, intent_score=50.0, source="csv")
        s.add(comp)
        s.flush()
        s.add_all([
            Signal(enterprise_id=comp.id, type="tender_win",
                   title="竞对中标目标企业真空泵采购", url="", pub_date=d(5),
                   base_score=70.0, raw_excerpt="中标｜成交价88万元",
                   evidence_claim=f"该主体中标「真空泵采购」，采购人：{e.name}，成交价88万元"),
            Signal(enterprise_id=comp.id, type="tender_win",
                   title="竞对早期中标单", url="", pub_date=d(40),
                   base_score=60.0,
                   evidence_claim=f"该主体中标「旧单」，采购人：{e.name}，成交价10万元"),
        ])
        # 同行业已成交同行（similar_won）+ 跨行业 won（应被排除）
        peer = Enterprise(name="BRF同行已成交机械有限公司", industry_key="machining",
                          province="江苏省", source="csv")
        noise = Enterprise(name="BRF跨行业公司", industry_key="semiconductor", source="csv")
        s.add_all([peer, noise])
        s.flush()
        s.add(Lead(enterprise_id=peer.id, status="won", amount_wan=320.0))
        s.add(Lead(enterprise_id=noise.id, status="won"))
        # 冷启动企业（mid 档：有可拨联系人、无跟进）与低意图企业（low 档：全空）
        e2 = Enterprise(name="BRF冷启动机械有限公司", industry_key="machining",
                        intent_score=40.0, source="csv")
        e3 = Enterprise(name="BRF低意图贸易有限公司", industry_key="trade",
                        intent_score=10.0, source="csv")
        s.add_all([e2, e3])
        s.flush()
        s.add(Contact(enterprise_id=e2.id, name="冷启动联系人", role="buyer_contact",
                      phones_json='["13800001111"]', source="manual"))
        s.commit()
        ids = {"eid": e.id, "e2": e2.id, "e3": e3.id}
    yield ids


# ---------------- 2.1 大聚合 JSON ----------------

def test_brief_intent_band_thresholds():
    """intent_band 三档边界：≥60 high / ≥30 mid / else low（SPEC-BRF §3）。"""
    assert _intent_band(60) == "high"
    assert _intent_band(68) == "high"
    assert _intent_band(59.9) == "mid"
    assert _intent_band(30) == "mid"
    assert _intent_band(29.9) == "low"
    assert _intent_band(0) == "low"


def test_brief_requires_login(client, seeded):
    r = client.get(f"/api/enterprises/{seeded['eid']}/brief")
    assert r.status_code == 401


def test_brief_404(client, admin_headers):
    r = client.get("/api/enterprises/999999/brief", headers=admin_headers)
    assert r.status_code == 404


def test_brief_six_blocks_and_buying(client, admin_headers, seeded):
    r = client.get(f"/api/enterprises/{seeded['eid']}/brief", headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"enterprise", "buying", "why_now", "who", "leverage", "next"}
    ent = d["enterprise"]
    assert set(ent) == {
        "name", "industry_name", "province", "city", "registered_capital_wan",
        "business_scope", "uscc", "icp_score", "intent_score", "is_competitor",
    }
    assert ent["name"] == "BRF作战目标真空装备有限公司"
    assert ent["intent_score"] == 68.0

    buying = d["buying"]
    assert set(buying) == {"wins", "top_buyers", "amount_total_wan"}
    # wins 按 pub_date desc（SPEC-BRF §3）
    assert [w["title"] for w in buying["wins"]] == [
        "BRF目标中标半导体镀膜线项目",
        "BRF目标中标检测设备采购项目",
        "BRF目标中标老项目",
    ]
    assert [w["amount_wan"] for w in buying["wins"]] == [300.0, 150.0, 120.0]
    assert buying["wins"][0]["buyer"] == "苏州微电子研究所"
    assert buying["amount_total_wan"] == 570.0
    # 采购体系：金额降序（enrich_stats 采购人回抽同口径）
    assert buying["top_buyers"][0]["buyer"] == "苏州微电子研究所"
    assert buying["top_buyers"][0]["amount_total_wan"] == 300.0
    assert buying["top_buyers"][0]["count"] == 1
    assert len(buying["top_buyers"]) == 3


def test_brief_why_now_and_who(client, admin_headers, seeded):
    r = client.get(f"/api/enterprises/{seeded['eid']}/brief", headers=admin_headers)
    d = r.json()
    why = d["why_now"]
    assert set(why) == {"signals", "intent_score", "intent_band", "proposals"}
    assert why["intent_band"] == "high"  # 68 ≥ 60
    # 近期信号 pub_date desc ×8，type_cn 中文映射
    assert [s["pub_date"] for s in why["signals"]] == [
        "2026-11-01", "2026-10-20", "2026-10-05", "2026-10-01", "2026-09-15",
    ]
    assert why["signals"][2]["type_cn"] == "拟建/备案"
    assert why["proposals"] == [
        {"project": "广东项目备案：BRF扩产项目（2026年度）", "pub_date": "2026-10-05"}
    ]

    who = d["who"]
    assert set(who) == {"contacts", "has_phone_count"}
    assert who["has_phone_count"] == 2
    # 排序：可拨优先 + 决策链优先（决策人 > 执行者 > 无角色），未拨优先、id 兜底
    names = [c["name"] for c in who["contacts"]]
    assert names == ["张董", "李工", "王主任", "赵秘书"]
    assert who["contacts"][0]["phones"] == ["0512-66668888"]
    assert who["contacts"][0]["channel"] == "公共资源交易平台"
    assert who["contacts"][0]["evidence_claim"] == "公告公开联系人"


def test_brief_leverage_and_next(client, admin_headers, seeded):
    r = client.get(f"/api/enterprises/{seeded['eid']}/brief", headers=admin_headers)
    d = r.json()
    lev = d["leverage"]
    assert set(lev) == {"installations", "competitor_wins_30d", "similar_won"}
    # 装机：有日期在前
    assert [i["model"] for i in lev["installations"]] == ["CX-100D", "CX-63"]
    assert lev["installations"][0]["installed_at"] == "2025-06-01"
    # 竞对 30 天窗口：只含 5 天前那笔，40 天前的排除
    assert len(lev["competitor_wins_30d"]) == 1
    cw = lev["competitor_wins_30d"][0]
    assert cw["competitor_name"] == "BRF竞对泵业有限公司"
    assert cw["amount_wan"] == 88.0
    # 同行业 won ×3：同行入选、跨行业与自身排除
    assert [w["name"] for w in lev["similar_won"]] == ["BRF同行已成交机械有限公司"]

    nxt = d["next"]
    assert set(nxt) == {"followups", "lead_status", "todo_hint"}
    assert nxt["lead_status"] == "following"
    assert [f["content"] for f in nxt["followups"]] == ["寄送选型资料", "电话初访：对接设备科"]
    # 有跟进无下一步 → 确定性规则"补记下一步动作"
    assert nxt["todo_hint"] == "补记下一步动作"


def test_brief_todo_hint_rules(client, admin_headers, seeded):
    # 无跟进+可拨 → "先拨第一个可拨联系人"；全空+low 档 → 先补录联系人
    r2 = client.get(f"/api/enterprises/{seeded['e2']}/brief", headers=admin_headers)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["why_now"]["intent_band"] == "mid"
    assert d2["next"]["lead_status"] is None
    assert d2["next"]["todo_hint"] == "先拨第一个可拨联系人"
    assert d2["buying"]["wins"] == []
    # e2 同为 machining：同行成交案例对其同样成立（同行业 won 口径）
    assert [w["name"] for w in d2["leverage"]["similar_won"]] == [
        "BRF同行已成交机械有限公司"
    ]

    r3 = client.get(f"/api/enterprises/{seeded['e3']}/brief", headers=admin_headers)
    d3 = r3.json()
    assert d3["why_now"]["intent_band"] == "low"
    assert d3["who"]["has_phone_count"] == 0
    assert d3["next"]["todo_hint"] == "先补录联系人电话，再开启首次触达"
    assert d3["leverage"]["similar_won"] == []  # trade 行业无 won 案例


# ---------------- 2.2 打印页 ----------------

def test_brief_print_html(client, admin_headers, seeded):
    r = client.get(f"/api/enterprises/{seeded['eid']}/brief/print", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    # 六分区销售语言标题
    for t in SECTION_TITLES:
        assert t in html, f"缺分区标题：{t}"
    # 页眉：企业名 + 生成时间 + 意图徽章
    assert "BRF作战目标真空装备有限公司" in html
    assert "生成于" in html and "意图高" in html
    # 打印模式：内联 CSS @media print + .noprint 提示行
    assert "@media print" in html and "noprint" in html
    # AI 摘要占位注释（SPEC-BRF 2.2 末段预留）
    assert "<!-- SPEC-BRF-AI: GLM Key 到位后在此注入 AI 摘要段 -->" in html


def test_brief_print_token_query_fallback(client, admin_token, seeded):
    """详情页 window.open 新窗口无 Authorization 头 → ?token= 回退（proposal_print 先例）。"""
    r = client.get(
        f"/api/enterprises/{seeded['eid']}/brief/print?token={admin_token}"
    )
    assert r.status_code == 200, r.text
    assert "他们在买什么" in r.text


def test_brief_print_404_and_empty(client, admin_headers, seeded):
    r = client.get("/api/enterprises/999999/brief/print", headers=admin_headers)
    assert r.status_code == 404
    # 全空企业也须出完整六分区（空态文案兜底）
    r = client.get(f"/api/enterprises/{seeded['e3']}/brief/print", headers=admin_headers)
    assert r.status_code == 200
    for t in SECTION_TITLES:
        assert t in r.text
