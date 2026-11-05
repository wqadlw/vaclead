"""装机台账端点（SPEC-AS AS1）+ 昊青产品线种子（AS3）。

CMMS asset 模型移植：装机=资产负债表，耗材时钟=定时订单。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import Enterprise, InstalledUnit, Product, Todo, User, utcnow

router = APIRouter(tags=["units"])

_HAOQING_LINE = [
    {"name": "EVS 永磁变频油封螺杆真空泵", "model": "EVS 系列",
     "spec": "永磁同步变频直联，节能 30%+，PTC 温度保护",
     "inds": ["food_processing", "packaging_printing", "textile_apparel",
              "medical_health", "pharma_biotech", "freeze_dried", "woodworking"]},
    {"name": "螺杆真空泵（干式）", "model": "LG 系列",
     "spec": "干式运行无油污染，适用洁净工况",
     "inds": ["semiconductor", "coating", "pharma_biotech", "chemical_new_material"]},
    {"name": "螺杆压缩机", "model": "LGC 系列",
     "spec": "压缩空气系统主机", "inds": ["machining", "metal_fabrication"]},
    {"name": "磁悬浮/螺杆鼓风机", "model": "LGB 系列",
     "spec": "污水处理/气力输送", "inds": ["environmental"]},
]


def seed_haoqing_products(session: Session) -> int:
    """昊青产品线种子（product 表为空时写入；幂等）。"""
    if session.query(Product).count() > 0:
        return 0
    n = 0
    for p in _HAOQING_LINE:
        session.add(Product(
            name=p["name"], model=p["model"], spec=p["spec"],
            application_industries=json.dumps(p["inds"], ensure_ascii=False),
        ))
        n += 1
    session.commit()
    return n


class UnitIn(BaseModel):
    product_id: int | None = None
    model: str = ""
    serial: str | None = None
    install_date: str | None = None
    consumable_json: str = "{}"
    notes: str | None = None


class UnitPatch(BaseModel):
    status: str | None = None
    consumable_json: str | None = None
    notes: str | None = None


@router.get("/enterprises/{eid}/units")
def list_units(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    rows = session.execute(
        select(InstalledUnit, Product.name)
        .outerjoin(Product, InstalledUnit.product_id == Product.id)
        .where(InstalledUnit.enterprise_id == eid)
        .order_by(InstalledUnit.id.desc())
    ).all()
    return {
        "items": [
            {"id": u.id, "product_name": pname, "model": u.model,
             "serial": u.serial, "install_date": u.install_date,
             "status": u.status, "consumable_json": u.consumable_json,
             "notes": u.notes}
            for u, pname in rows
        ]
    }


@router.post("/enterprises/{eid}/units", status_code=201)
def create_unit(
    eid: int,
    body: UnitIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if session.get(Enterprise, eid) is None:
        raise HTTPException(404, "企业不存在")
    u = InstalledUnit(
        enterprise_id=eid, product_id=body.product_id,
        model=body.model or "", serial=body.serial,
        install_date=body.install_date, consumable_json=body.consumable_json,
        notes=body.notes,
    )
    session.add(u)
    session.commit()
    return {"id": u.id}


@router.patch("/units/{uid}")
def patch_unit(
    uid: int,
    body: UnitPatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    u = session.get(InstalledUnit, uid)
    if u is None:
        raise HTTPException(404, "装机不存在")
    if body.status is not None:
        if body.status not in ("active", "replaced", "retired"):
            raise HTTPException(422, "status 取值 active|replaced|retired")
        u.status = body.status
    if body.consumable_json is not None:
        json.loads(body.consumable_json)  # 容错：非法 JSON 拒绝
        u.consumable_json = body.consumable_json
    if body.notes is not None:
        u.notes = body.notes
    session.commit()
    return {"ok": True, "id": uid}


def consumable_clock_sweep() -> dict:
    """耗材时钟扫描（SPEC-AS AS1，每日 06:20）：30 天内到期 → 回访待办。"""
    from datetime import datetime, timedelta, timezone

    from ..db import SessionLocal

    cutoff = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    created = 0
    with SessionLocal() as s:
        rows = s.execute(
            select(InstalledUnit, Enterprise.name)
            .join(Enterprise, InstalledUnit.enterprise_id == Enterprise.id)
            .where(InstalledUnit.status == "active")
        ).all()
        for u, ename in rows:
            try:
                clock = json.loads(u.consumable_json or "{}")
            except ValueError:
                continue
            for item, due in (clock or {}).items():
                if not due or str(due) > cutoff:
                    continue
                label = {"seal_due": "密封件", "filter_due": "滤芯",
                         "oil_due": "润滑油"}.get(item, item)
                title = f"{ename} {label}更换回访（装机 {u.model or u.id}）"
                exists = s.query(Todo).filter(
                    Todo.enterprise_id == u.enterprise_id,
                    Todo.title == title, Todo.done == 0,
                ).first()
                if exists is None:
                    s.add(Todo(
                        title=title, enterprise_id=u.enterprise_id,
                        due_date=str(due), owner_id=None, source="automation",
                    ))
                    created += 1
        s.commit()
    return {"todos_created": created}


class UnitsPasteIn(BaseModel):
    """装机批量粘贴导入（SPEC-AU AU2）：历史成交 Excel 直接 Ctrl+V。

    每行：企业名称[Tab]型号[Tab]序列号[Tab]安装日期；表头行自动跳过。
    企业走 resolve_enterprise 归一（撞库复用）；已删企业跳过；
    同企业同型号 active 装机去重（重复导入幂等）。
    """

    text: str = Field(..., description="Tab 分隔，每行一条")


@router.post("/import/units-paste", status_code=201)
def import_units_paste(
    body: UnitsPasteIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    from ..normalize import resolve_enterprise

    created = 0
    skipped = 0
    seen_models: set[tuple[int, str]] = set()  # autoflush=False 盲区兜底
    for raw in (body.text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2 or "企业" in parts[0] or "型号" in parts[0]:
            continue  # 表头/垃圾行
        name, model = parts[0], parts[1]
        serial = parts[2] if len(parts) > 2 and parts[2] else None
        install = parts[3] if len(parts) > 3 and parts[3] else None
        if len(name) < 4 or not model:
            skipped += 1
            continue
        ent, _is_new = resolve_enterprise(session, name, source="manual")
        if ent.deleted_at:
            skipped += 1
            continue
        key = (ent.id, model)
        if key in seen_models:
            skipped += 1
            continue
        exists = (
            session.query(InstalledUnit)
            .filter(
                InstalledUnit.enterprise_id == ent.id,
                InstalledUnit.model == model,
                InstalledUnit.status == "active",
            )
            .first()
        )
        if exists is not None:
            skipped += 1
            continue
        session.add(InstalledUnit(
            enterprise_id=ent.id, model=model, serial=serial,
            install_date=install, notes="paste-import",
        ))
        seen_models.add(key)
        created += 1
    session.commit()
    return {"created": created, "skipped": skipped}
