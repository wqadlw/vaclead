"""README 截图专用演示服务器（SPEC-BB 配套）：全假数据，跑在 :8011。

与真库完全隔离（temp DB + 禁 cron）。数据为虚构企业/假号码段
（138000xxxxx / 各市 0000 局号），专供 README 截图与对外演示。
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ["VACLEAD_DB"] = os.path.join(tempfile.mkdtemp(prefix="vaclead-demo-"), "demo.db")
os.environ["VACLEAD_DISABLE_JOBS"] = "1"

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.api.units import seed_haoqing_products  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    CaseStudy,
    Contact,
    Contract,
    Enterprise,
    InstalledUnit,
    Lead,
    PaymentMilestone,
    Signal,
    Todo,
    utcnow,
)


def _d(days_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%dT%H:%M:%S")


def _date(days_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%d")


def _date_ahead(days: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).strftime("%Y-%m-%d")


# （名称, 城市, 行业 key, 行业名, 角色, 电话, 手机, lat, lng, icp, intent, 竞品）
FAKE = [
    ("苏州澜庭精密机械有限公司", "苏州", "machining", "机械加工", "oem", "0512-60001001", "13800001001", 31.32, 120.58, 78, 62, 0),
    ("苏州云涌镀膜科技有限公司", "苏州", "coating", "真空镀膜加工", "end_user", "0512-60001002", "13800001002", 31.30, 120.62, 82, 74, 0),
    ("昆山汇润真空设备有限公司", "苏州", "machining", "机械加工", "oem", "0512-60001003", "13800001003", 31.38, 120.98, 74, 55, 0),
    ("苏州蓝谷镀膜设备有限公司", "苏州", "coating", "真空镀膜加工", "oem", "0512-60001004", "13800001004", 31.26, 120.55, 68, 48, 0),
    ("苏州竹屿食品机械有限公司", "苏州", "machining", "机械加工", "oem", "0512-60001005", "13800001005", 31.28, 120.72, 71, 40, 0),
    ("苏州汀兰真空科技有限公司", "苏州", "coating", "真空镀膜加工", "end_user", "0512-60001006", "13800001006", 31.34, 120.66, 76, 58, 0),
    ("苏州望苏半导体装备有限公司", "苏州", "semiconductor", "半导体/集成电路", "end_user", "0512-60001007", "13800001007", 31.36, 120.72, 85, 70, 0),
    ("苏州姑苏采暖设备有限公司", "苏州", "hvac_building", "暖通机电", "end_user", "0512-60001008", "13800001008", 31.31, 120.60, 62, 35, 0),
    ("苏州平江精密仪器有限公司", "苏州", "lab_instrument", "检验检测", "end_user", "0512-60001009", "13800001009", 31.29, 120.63, 66, 44, 0),
    ("苏州枫桥冻干食品有限公司", "苏州", "freeze_dried", "冻干食品/医药", "end_user", "0512-60001010", "13800001010", 31.35, 120.52, 70, 52, 0),
    ("苏州先进真空技术研究院", "苏州", "research_edu", "科研院所/高校医疗", "end_user", "0512-60001011", "13800001011", 31.27, 120.68, 88, 66, 0),
    ("苏州吴中职业技术学校", "苏州", "research_edu", "科研院所/高校医疗", "end_user", "0512-60001012", "13800001012", 31.25, 120.63, 55, 30, 0),
    ("无锡梁溪真空装备有限公司", "无锡", "machining", "机械加工", "oem", "0510-60002001", "13800002001", 31.57, 120.30, 76, 58, 0),
    ("无锡雪浪镀膜材料有限公司", "无锡", "coating", "真空镀膜加工", "end_user", "0510-60002002", "13800002002", 31.50, 120.36, 72, 50, 0),
    ("无锡蠡湖光电科技有限公司", "无锡", "semiconductor", "半导体/集成电路", "end_user", "0510-60002003", "13800002003", 31.52, 120.28, 80, 62, 0),
    ("无锡梅村机械制造有限公司", "无锡", "machining", "机械加工", "oem", "0510-60002004", "13800002004", 31.55, 120.42, 64, 38, 0),
    ("无锡鸿山食品装备有限公司", "无锡", "machining", "机械加工", "oem", "0510-60002005", "13800002005", 31.53, 120.47, 69, 45, 0),
    ("无锡市第二人民医院", "无锡", "medical_health", "医疗卫生", "end_user", "0510-60002006", "13800002006", 31.57, 120.31, 60, 36, 0),
    ("江苏恒芯半导体装备研究院", "无锡", "semiconductor", "半导体/集成电路", "end_user", "0510-60002007", "13800002007", 31.49, 120.35, 86, 72, 0),
    ("江阴蓝湾真空科技有限公司", "无锡", "coating", "真空镀膜加工", "end_user", "0510-60002008", "13800002008", 31.90, 120.28, 70, 46, 0),
    ("东莞虎门精密五金有限公司", "东莞", "machining", "机械加工", "oem", "0769-60003001", "13800003001", 22.82, 113.72, 66, 42, 0),
    ("东莞长安光电科技有限公司", "东莞", "coating", "真空镀膜加工", "end_user", "0769-60003002", "13800003002", 22.80, 113.75, 74, 55, 0),
    ("东莞松山湖真空装备有限公司", "东莞", "machining", "机械加工", "oem", "0769-60003003", "13800003003", 22.92, 113.88, 80, 60, 0),
    ("东莞厚街包装机械有限公司", "东莞", "packaging_printing", "包装印刷", "oem", "0769-60003004", "13800003004", 22.94, 113.67, 62, 40, 0),
    ("东莞大朗食品机械有限公司", "东莞", "machining", "机械加工", "oem", "0769-60003005", "13800003005", 23.02, 113.82, 68, 44, 0),
    ("东莞市第九人民医院", "东莞", "medical_health", "医疗卫生", "end_user", "0769-60003006", "13800003006", 23.02, 113.70, 58, 34, 0),
    ("东莞松山湖材料实验室", "东莞", "research_edu", "科研院所/高校医疗", "end_user", "0769-60003007", "13800003007", 22.91, 113.89, 84, 68, 0),
    ("佛山季华精密装备有限公司", "佛山", "machining", "机械加工", "oem", "0757-60004001", "13800004001", 23.02, 113.10, 72, 50, 0),
    ("佛山绿源环保设备有限公司", "佛山", "environmental", "环保水务", "end_user", "0757-60004002", "13800004002", 23.01, 113.15, 64, 40, 0),
    ("深圳湾芯半导体材料有限公司", "深圳", "semiconductor", "半导体/集成电路", "end_user", "0755-60005001", "13800005001", 22.53, 113.94, 88, 75, 0),
    ("深圳前海精密制造有限公司", "深圳", "machining", "机械加工", "oem", "0755-60005002", "13800005002", 22.52, 113.90, 70, 48, 0),
    ("宁波市检验检测集团", "宁波", "lab_instrument", "检验检测", "end_user", "0574-60006001", "13800006001", 29.88, 121.55, 62, 38, 0),
    ("宁波梅山真空装备有限公司", "宁波", "machining", "机械加工", "oem", "0574-60006002", "13800006002", 29.76, 121.90, 70, 46, 0),
    ("常州西太湖镀膜有限公司", "常州", "coating", "真空镀膜加工", "end_user", "0519-60007001", "13800007001", 31.70, 119.85, 72, 52, 0),
    ("常州龙城食品装备有限公司", "常州", "machining", "机械加工", "oem", "0519-60007002", "13800007002", 31.78, 119.96, 66, 42, 0),
    ("南通如皋真空科技有限公司", "南通", "coating", "真空镀膜加工", "end_user", "0513-60008001", "13800008001", 32.38, 120.46, 68, 44, 0),
    ("西安高新真空装备研究院", "西安", "research_edu", "科研院所/高校医疗", "end_user", "029-60009001", "13800009001", 34.23, 108.84, 80, 60, 0),
    ("合肥新站半导体材料有限公司", "合肥", "semiconductor", "半导体/集成电路", "end_user", "0551-60010001", "13800010001", 31.88, 117.28, 78, 58, 0),
    ("合肥庐阳暖通工程有限公司", "合肥", "hvac_building", "暖通机电", "end_user", "0551-60010002", "13800010002", 31.86, 117.25, 60, 36, 0),
    ("佛山南海化工装备有限公司", "佛山", "chemical_new_material", "化工新材料", "end_user", "0757-60004003", "13800004003", 23.05, 113.14, 66, 42, 0),
    # 真同行（泵/真空设备制造商——打标排除出推荐）
    ("苏州 competitor 真空泵制造有限公司", "苏州", "machining", "机械加工", "end_user", "0512-60001111", "13800001111", 31.33, 120.61, 70, 50, 1),
    ("无锡竞品真空泵厂", "无锡", "machining", "机械加工", "end_user", "0510-60002111", "13800002111", 31.58, 120.31, 68, 48, 1),
]


def seed() -> None:
    init_db()
    with SessionLocal() as s:
        for (name, city, ik, inm, role, tel, mob, lat, lng, icp, intent, comp) in FAKE:
            e = Enterprise(
                name=name, province="", city=city, lat=lat, lng=lng,
                business_scope=f"{inm}相关工艺与设备运行", industry_key=ik,
                industry_name=inm, source="poi", icp_score=icp,
                intent_score=intent, is_competitor=comp,
                customer_role="oem" if role == "oem" else "end_user",
            )
            s.add(e)
            s.flush()
            s.add(Contact(
                enterprise_id=e.id, name="设备科 王工" if role == "oem" else "联系人",
                role="poi", title=f"{tel}",
                phones_json=json.dumps([tel, mob]),
                call_result="pending", source="amap",
                evidence_claim="演示数据（虚构）",
            ))
        s.commit()

        # 信号：近 30 天分布（趋势图有形），覆盖三类
        sig_titles = [
            ("tender_win", "真空镀膜机组采购中标公告", 70, 1),
            ("tender", "真空获得设备采购项目招标公告", 55, 3),
            ("tender_buy", "食品冻干生产线设备采购公告", 60, 5),
            ("tender_win", "半导体真空泵组中标公示", 75, 7),
            ("proposal", "年产 500 台真空获得设备扩建项目备案", 45, 9),
            ("tender", "洁净车间真空系统改造招标", 50, 12),
            ("tender_win", "镀膜生产线设备租赁服务中标", 65, 15),
            ("tender_buy", "实验室真空制备系统采购公告", 55, 18),
            ("proposal", "精密机械智能制造基地项目备案", 45, 21),
            ("tender", "真空干燥系统设备采购招标", 50, 24),
        ]
        ents = s.query(Enterprise).all()
        for i, (t, title, bs, days) in enumerate(sig_titles):
            e = ents[(i * 7) % 20]
            s.add(Signal(
                enterprise_id=e.id, type=t, title=f"{e.name[:8]}·{title}",
                url=f"https://demo.example/{i}", pub_date=_date(days),
                base_score=bs,
                evidence_claim=f"该主体于 {_date(days)} 公开「{title}」（演示数据）",
                created_at=_d(days),
            ))
        s.commit()

        # 赢单线索 + 合同 + 分期（一条逾期/一条 15 天到期/一条已付）
        rich = s.query(Enterprise).filter(
            Enterprise.name == "苏州云涌镀膜科技有限公司").first()
        lead = Lead(enterprise_id=rich.id, status="won", owner_id=1)
        s.add(lead)
        s.flush()
        c = Contract(lead_id=lead.id, name="云涌镀膜真空系统改造合同",
                     amount_wan=120, status="active", owner_id=1,
                     start_date=_date(120), end_date=_date_ahead(400))
        s.add(c)
        s.flush()
        s.add(PaymentMilestone(contract_id=c.id, seq=1, label="预付款 30%",
                               amount_wan=36, status="paid",
                               paid_date=_date(100)))
        s.add(PaymentMilestone(contract_id=c.id, seq=2, label="发货款 40%",
                               amount_wan=48, status="overdue",
                               due_date=_date(10)))
        s.add(PaymentMilestone(contract_id=c.id, seq=3, label="验收款 20%",
                               amount_wan=24, status="pending",
                               due_date=_date_ahead(15)))
        # 装机
        s.add(InstalledUnit(
            enterprise_id=rich.id, model="EVS-100 永磁变频螺杆泵",
            serial="EVS2025-0001", install_date=_date(120),
            consumable_json=json.dumps({
                "seal_due": _date_ahead(18), "filter_due": _date_ahead(120)}),
            notes="演示装机",
        ))
        s.add(InstalledUnit(
            enterprise_id=rich.id, model="LG-200 干式螺杆泵",
            serial="LG2024-0007", install_date=_date(380), status="active",
            consumable_json=json.dumps({"seal_due": _date_ahead(45)}),
            notes="演示装机",
        ))
        # 触达结果（一台已接通）
        c1 = s.query(Contact).filter(
            Contact.enterprise_id == rich.id).first()
        c1.call_result = "connected"
        c1.called_at = utcnow()
        # 私有成交案例
        s.add(CaseStudy(
            enterprise_id=rich.id, industry_key="coating",
            title="为苏州某镀膜企业交付 3 套永磁变频螺杆真空系统",
            content="交付后连续运行 18 个月无故障停机，综合能耗较原系统下降 28%，"
                    "噪音下降 12 分贝，年节省电费约 8 万元（演示数据）。",
            year="2025",
        ))
        s.commit()

        seed_haoqing_products(s)


if __name__ == "__main__":
    seed()
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8011, log_level="warning")
