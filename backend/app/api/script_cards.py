"""话术卡 API（SPEC-SALE S1）：SALES_PLAYBOOK 独有内容 → 系统功能。

- 种子：5 张昊志话术卡（首联/竞对替换/老户复购/拟建窗口/通用收尾），幂等
  用 DB 标记（SyncWatermark 行，非内存缓存——SPEC-AS 教训：模块级缓存会被
  测试初始化污染），照 product_seed cron 模式注册（main.py script_card_seed）。
- 通话驾驶舱接线：pick_script_cards 场景映射（prep 端点调）——
  competitor_replace > proposal_window > repeat_purchase > first_contact，
  另附 1 张 generic；role_hint 与企业 customer_role 相等或为空的卡优先。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import Enterprise, Lead, ScriptCard, Signal, SyncWatermark, User, utcnow

router = APIRouter(tags=["script_cards"])

#: scene 枚举（SPEC-SALE S1）
SCENES = (
    "first_contact",
    "competitor_replace",
    "repeat_purchase",
    "proposal_window",
    "generic",
)
#: role_hint 枚举（对齐 Enterprise.customer_role）
ROLES = ("oem", "end_user", "reseller", "integrator")

#: 种子防重标记（sync_watermark 行，DB 标记不用内存缓存）
SEED_MARKER = "script_card_seed"

# 种子内容（SPEC-SALE S1，销售语言提炼自 SALES_PLAYBOOK.md）
_SEED_CARDS: list[dict] = [
    {
        "title": "首联·官方授权开场",
        "scene": "first_contact",
        "role_hint": None,
        "sort": 10,
        "body_md": (
            "你是XX设备的负责人吧？我们是昊青气体装备官方授权销售——油封/螺杆真空泵"
            "+压缩机的整机与系统方案。您这边产线用真空工艺的话，设备选型、能耗账、"
            "售后响应我们可以先做个免费评估，不买也没关系，先给您留一份选型计算书，"
            "选型错了后面停机的账更大。"
        ),
    },
    {
        "title": "竞对替换·工况痛点",
        "scene": "competitor_replace",
        "role_hint": None,
        "sort": 20,
        "body_md": (
            "现在用的泵经常停机或者选型偏大偏小吧？螺杆泵采购最常见的问题就是选型"
            "跟工况不匹配。我们把工况摸清楚再算账——您把现在的真空度、抽速、每天开机"
            "时长给我，我给您出一份对比账：能耗差多少、维护差多少、五年停机风险差"
            "多少。算完不划算您就不用换。"
        ),
    },
    {
        "title": "老户复购·耗材时钟",
        "scene": "repeat_purchase",
        "role_hint": None,
        "sort": 30,
        "body_md": (
            "您那边 X 年装的设备用了快到密封件更换周期了（一般 12-24 个月，"
            "滤芯 2000-4000 小时）。趁还没停机提前换，排队工期能省一周以上。"
            "我把备件清单和价格发您，您对比现在停机一天的损失就知道值不值。"
        ),
    },
    {
        "title": "拟建窗口·项目介入",
        "scene": "proposal_window",
        "role_hint": None,
        "sort": 40,
        "body_md": (
            "看到贵司 XX 项目刚完成备案——按经验这类项目设备采购在 1-3 个月内启动。"
            "真空系统这块建议在设计阶段就把选型定掉，土建和管路预留一次到位。"
            "我们先免费上门勘测，出方案不收费。"
        ),
    },
    {
        "title": "确定性收尾",
        "scene": "generic",
        "role_hint": None,
        "sort": 90,
        "body_md": (
            "我们的打法就一条——让您五年不停机。品牌授权、系统搭建、区域响应，"
            "三件事都有人兜底。"
        ),
    },
]


def seed_script_cards(session: Session) -> int:
    """话术卡种子（幂等，SPEC-SALE S1）：DB 标记防重 + 标题查重双保险。

    首种写入 5 张卡并把 SyncWatermark[script_card_seed] 落 ok；之后 cron/启动
    重跑零写入。admin 删掉的卡不被复活（标记命中即整段跳过）；标记丢失时
    按标题查重不重复插。
    """
    row = session.get(SyncWatermark, SEED_MARKER)
    if row is not None and row.last_status == "ok":
        return 0
    existing = {t for (t,) in session.query(ScriptCard.title).all()}
    n = 0
    for card in _SEED_CARDS:
        if card["title"] in existing:
            continue
        session.add(ScriptCard(**card))
        n += 1
    if row is None:
        row = SyncWatermark(job_name=SEED_MARKER)
        session.add(row)
    row.last_run_at = utcnow()
    row.last_status = "ok"
    row.stats_json = json.dumps({"seeded": n}, ensure_ascii=False)
    session.commit()
    return n


def _card_dict(c: ScriptCard) -> dict:
    return {
        "id": c.id, "title": c.title, "scene": c.scene,
        "role_hint": c.role_hint, "body_md": c.body_md, "sort": c.sort,
        "enabled": bool(c.enabled),
    }


def _rank_cards(cards: list[ScriptCard], customer_role: str | None) -> list[ScriptCard]:
    """role_hint 排序：与企业 customer_role 相等 > 空（通用）> 其他；再按 sort/id。"""

    def key(c: ScriptCard) -> tuple[int, int, int]:
        hint = c.role_hint or ""
        if customer_role and hint == customer_role:
            rank = 0
        elif not hint:
            rank = 1
        else:
            rank = 2
        return (rank, c.sort, c.id)

    return sorted(cards, key=key)


_SCENE_PRIORITY = ("competitor_replace", "proposal_window", "repeat_purchase", "first_contact")


def pick_script_cards(
    session: Session,
    e: Enterprise,
    lead: Lead | None,
    has_followup: bool,
) -> list[dict]:
    """通话驾驶舱场景映射（SPEC-SALE S1）：一个 lead 命中多场景取最紧急。

    - competitor_replace：lead.lost_reason=competitor 或 30 天内竞对中标信号
      （竞对 tender_win 与本企业 tender_buy/tender 同项目名——ggzy-harvest
      双侧落库的项目名对齐）
    - proposal_window：90 天内有 proposal 信号
    - repeat_purchase：lead.status=won
    - first_contact：无线索 / status=new / 无跟进记录
    """
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    cutoff30 = (today - timedelta(days=30)).isoformat()
    cutoff90 = (today - timedelta(days=90)).isoformat()
    recency = func.coalesce(Signal.pub_date, func.substr(Signal.created_at, 1, 10))

    scenes: set[str] = set()
    if lead is not None and (lead.lost_reason or "") == "competitor":
        scenes.add("competitor_replace")
    comp_titles = (
        select(Signal.title)
        .join(Enterprise, Enterprise.id == Signal.enterprise_id)
        .where(
            Enterprise.is_competitor == 1,
            Signal.type == "tender_win",
            recency >= cutoff30,
        )
        .scalar_subquery()
    )
    comp_hit = (
        session.query(Signal)
        .filter(
            Signal.enterprise_id == e.id,
            Signal.type.in_(["tender_buy", "tender"]),
            Signal.title.in_(comp_titles),
            recency >= cutoff30,
        )
        .first()
    )
    if comp_hit is not None:
        scenes.add("competitor_replace")
    proposal_hit = (
        session.query(Signal)
        .filter(
            Signal.enterprise_id == e.id,
            Signal.type == "proposal",
            recency >= cutoff90,
        )
        .first()
    )
    if proposal_hit is not None:
        scenes.add("proposal_window")
    if lead is not None and lead.status == "won":
        scenes.add("repeat_purchase")
    if lead is None or lead.status == "new" or not has_followup:
        scenes.add("first_contact")

    primary = next((sc for sc in _SCENE_PRIORITY if sc in scenes), None)
    out: list[ScriptCard] = []
    if primary is not None:
        rows = (
            session.query(ScriptCard)
            .filter(ScriptCard.enabled == 1, ScriptCard.scene == primary)
            .all()
        )
        out.extend(_rank_cards(rows, e.customer_role))
    if primary != "generic":
        generics = (
            session.query(ScriptCard)
            .filter(ScriptCard.enabled == 1, ScriptCard.scene == "generic")
            .all()
        )
        ranked = _rank_cards(generics, e.customer_role)
        if ranked:
            out.append(ranked[0])  # 另附 1 张 generic（SPEC：一个场景一张兜底）
    return [_card_dict(c) for c in out]


# ---- API ----


class ScriptCardIn(BaseModel):
    title: str = Field(min_length=1)
    scene: str = "generic"
    role_hint: str | None = None
    body_md: str = ""
    sort: int = 100
    enabled: int = 1

    @field_validator("scene")
    @classmethod
    def _scene_ok(cls, v: str) -> str:
        if v not in SCENES:
            raise ValueError(f"scene 须为 {'/'.join(SCENES)} 之一")
        return v

    @field_validator("role_hint")
    @classmethod
    def _role_ok(cls, v: str | None) -> str | None:
        if v and v not in ROLES:
            raise ValueError(f"role_hint 须为 {'/'.join(ROLES)} 之一或空")
        return v or None


class ScriptCardPatch(BaseModel):
    title: str | None = None
    scene: str | None = None
    role_hint: str | None = None
    body_md: str | None = None
    sort: int | None = None
    enabled: int | None = None

    @field_validator("scene")
    @classmethod
    def _scene_ok(cls, v: str | None) -> str | None:
        if v is not None and v not in SCENES:
            raise ValueError(f"scene 须为 {'/'.join(SCENES)} 之一")
        return v

    @field_validator("role_hint")
    @classmethod
    def _role_ok(cls, v: str | None) -> str | None:
        if v and v not in ROLES:
            raise ValueError(f"role_hint 须为 {'/'.join(ROLES)} 之一或空")
        return v or None


@router.get("/script-cards")
def list_script_cards(
    scene: str | None = None,
    role: str | None = None,
    include_disabled: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),  # 全员可读（驾驶舱+设置页）
):
    """话术卡列表（全员，enabled 按 sort 排序）。

    role 过滤语义：命中该角色的卡 + 通用卡（role_hint 空）——销售取卡口径。
    include_disabled=1 仅供 admin 管理面板看全量（含停用卡，可重新启用）。
    """
    q = session.query(ScriptCard)
    if not include_disabled:
        q = q.filter(ScriptCard.enabled == 1)
    elif (user.role or "") != "admin":
        raise HTTPException(403, "仅管理员可查看停用话术卡")
    if scene:
        q = q.filter(ScriptCard.scene == scene)
    if role:
        q = q.filter(
            or_(
                ScriptCard.role_hint == role,
                ScriptCard.role_hint.is_(None),
                ScriptCard.role_hint == "",
            )
        )
    rows = q.order_by(ScriptCard.sort.asc(), ScriptCard.id.asc()).all()
    return {"items": [_card_dict(c) for c in rows]}


@router.post("/script-cards", status_code=201)
def create_script_card(
    body: ScriptCardIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    c = ScriptCard(
        title=body.title, scene=body.scene, role_hint=body.role_hint,
        body_md=body.body_md, sort=body.sort, enabled=body.enabled,
    )
    session.add(c)
    session.commit()
    return {"id": c.id}


@router.put("/script-cards/{cid}")
def update_script_card(
    cid: int,
    body: ScriptCardPatch,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    c = session.get(ScriptCard, cid)
    if c is None:
        raise HTTPException(404, "话术卡不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(c, field, value)
    c.updated_at = utcnow()
    session.commit()
    return {"id": c.id}


@router.delete("/script-cards/{cid}")
def delete_script_card(
    cid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    c = session.get(ScriptCard, cid)
    if c is None:
        raise HTTPException(404, "话术卡不存在")
    session.delete(c)
    session.commit()
    return {"ok": True}
