"""E2E 后端启动脚本（SPEC-H）：临时库 + 静态托管 :8010。

Playwright webServer 拉起；独立 VACLEAD_DB（e2e 目录内），绝不碰真库。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent / "backend"
DB = HERE / "tmp" / "e2e.db"
DB.parent.mkdir(parents=True, exist_ok=True)
if DB.exists():
    DB.unlink()
for suffix in ("-wal", "-shm"):
    extra = DB.with_name(DB.name + suffix)
    if extra.exists():
        extra.unlink()

os.environ["VACLEAD_DB"] = str(DB)
os.environ["VACLEAD_JWT_SECRET"] = "e2e-secret"
os.environ["MEILI_URL"] = "http://127.0.0.1:17999"  # 死端口：禁用投影，强制 SQLite 真源路径
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AutomationRule, Contact, Enterprise, Signal, Todo,
)

init_db()
# ---- 确定性种子数据 ----
with SessionLocal() as s:
    ent1 = Enterprise(
        name="苏州长晶半导体有限公司", province="江苏省", city="苏州市",
        business_scope="半导体晶圆制造与真空镀膜技术研发", registered_capital_wan=2000,
        industry_key="semiconductor", industry_name="半导体/集成电路",
        source="csv", icp_score=65, intent_score=52,
    )
    ent2 = Enterprise(
        name="北京大学", province="北京市", city="北京市",
        business_scope="教学科研", industry_key="research_edu",
        industry_name="科研院所/高校医疗", source="ccgp_buyer", icp_score=40,
    )
    ent3 = Enterprise(  # 无电话对照企业（快捷筛选计数用）
        name="东莞恒晖真空设备有限公司", province="广东省", city="东莞市",
        source="ggzy", is_competitor=1,
    )
    s.add_all([ent1, ent2, ent3])
    s.flush()
    s.add(Signal(
        enterprise_id=ent1.id, type="tender_win", title="12英寸真空镀膜机采购中标公告",
        url="https://e2e.example/1", pub_date="2026-10-20", base_score=70,
        evidence_claim="该主体中标「真空镀膜机」，成交价300万元，采购人：苏州长晶",
    ))
    s.add(Signal(
        enterprise_id=ent2.id, type="tender_buy", title="北京大学分子泵机组采购公告",
        url="https://e2e.example/2", pub_date="2026-10-22", base_score=70,
        evidence_claim="该单位公开采购「分子泵机组」",
    ))
    s.add(Contact(
        enterprise_id=ent1.id, name="王工", role="采购联系人",
        title="13800000001", source="ccgp",
        evidence_claim="E2E 种子联系人",
    ))
    s.add(Contact(
        enterprise_id=ent2.id, name="李老师", role="项目联系人",
        title="010-62750000", source="ccgp",
        evidence_claim="E2E 种子联系人",
    ))
    s.add(AutomationRule(
        name="E2E演示规则", cond_signal_type="tender_win", action_type="notify",
    ))
    s.add(Todo(title="E2E 待办：联系苏州长晶", due_date="2026-01-01", source="manual"))
    s.commit()

uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
