"""客户群 API（SPEC-SEG2）：客户画像数据化——群成为系统一级实体。

- 种子：10 群（全国版，SPEC §2 表格逐行落库），幂等照 script_card 模式
  （SyncWatermark DB 标记 + 名称查重双保险），main.py segment_seed cron
  （06:50）+ lifespan 启动即种。
- GET /api/segments（全员）：每群实时统计——enterprise_count=industry_key
  ∈ 群 industry_keys 的存活企业叠加全部非客户排除（customer.non_buyer_conds
  与 contacts 同口径）；dialable_count=该群企业去重可拨联系人（contacts
  工作列表同款 phones_json SQL 口径）。附录 A1 作业化改造追加六字段
  （dialable_untouched/touched/intent_high/following/new_7d/last_signal），
  口径同源（同 id 集+非客户排除），实现走"企业 id 集先算一次+分批聚合"。
- POST/PUT/DELETE（admin）：CRUD，code 唯一；enabled=0 停用退出推荐/权重。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..customer import non_buyer_conds, parse_json_list, segment_keys_of
from ..db import get_session
from ..models import (
    BrandInstall,
    Contact,
    CustomerSegment,
    Enterprise,
    Lead,
    Signal,
    SyncWatermark,
    User,
    utcnow,
)

router = APIRouter(tags=["segments"])

#: 种子防重标记（sync_watermark 行，DB 标记不用内存缓存——SPEC-AS 教训）
SEED_MARKER = "segment_seed"

PRIORITIES = ("P0", "P1", "P2")

# 种子内容（SPEC-SEG2 §2 十群表格，全国版；notes=销售能懂的典型客户描述）
_SEED_SEGMENTS: list[dict] = [
    {
        "name": "镀膜加工厂",
        "code": "coating_factory",
        "keywords": ["真空镀膜", "水镀", "光学镀膜"],
        "industry_keys": ["coating"],
        "business_lines": ["整机", "维修", "配件", "泵油"],
        "priority": "P0",
        "enabled": 1,
        "notes": "光学/装饰/工模具镀膜代工厂，真空室加泵组日常满负荷，泵油与密封件消耗稳定，认整机置换和维修快修。",
        "sort": 10,
    },
    {
        "name": "半导体/电子厂",
        "code": "semiconductor_factory",
        "keywords": ["半导体", "晶圆", "集成电路", "封装测试"],
        "industry_keys": ["semiconductor"],
        "business_lines": ["整机", "系统"],
        "priority": "P0",
        "enabled": 1,
        "notes": "晶圆/封测产线洁净车间持续排风，干泵螺杆泵为主力，对颗粒、噪音和维保响应敏感，客单高。",
        "sort": 20,
    },
    {
        "name": "热处理厂",
        "code": "heat_treatment_factory",
        "keywords": ["热处理", "真空淬火", "退火炉"],
        "industry_keys": ["heat_treatment"],
        "business_lines": ["整机", "泵油", "配件"],
        "priority": "P0",
        "enabled": 1,
        "notes": "真空淬火/退火炉配套泵组，高温工况泵油老化快，泵油加配件复购稳定。",
        "sort": 30,
    },
    {
        "name": "食品冻干厂",
        "code": "food_freeze_dry",
        "keywords": ["冻干食品", "脱水蔬菜", "食品加工"],
        "industry_keys": ["freeze_dried"],
        "business_lines": ["整线", "配件"],
        "priority": "P0",
        "enabled": 1,
        "notes": "冻干生产线冷阱环节靠真空泵，整线新建和扩产是主切入点，配件跟着产量走。",
        "sort": 40,
    },
    {
        "name": "制药厂",
        "code": "pharma_factory",
        "keywords": ["制药", "药业", "生物科技", "冻干"],
        "industry_keys": [],
        "business_lines": ["整线", "维保"],
        "priority": "P0",
        "enabled": 0,
        "notes": "冻干粉针/原料药车间；词表待用户确认——确认前不启用（不参与统计名单与推荐）。",
        "sort": 50,
    },
    {
        "name": "光伏/锂电厂",
        "code": "pv_battery_factory",
        "keywords": ["光伏", "太阳能", "锂电", "动力电池", "电芯"],
        "industry_keys": [],
        "business_lines": ["整机"],
        "priority": "P0",
        "enabled": 1,
        "notes": "电池片/电芯车间真空工序；行业类目暂混半导体/未分类，名单按关键词采集推进，词表待细化。",
        "sort": 60,
    },
    {
        "name": "化工/新材料厂",
        "code": "chemical_factory",
        "keywords": ["精细化工", "锂电材料", "石墨碳素"],
        "industry_keys": ["chemical_new_material"],
        "business_lines": ["系统", "整机"],
        "priority": "P1",
        "enabled": 1,
        "notes": "精细化工/锂电材料/石墨碳素产线，真空系统成套需求，从系统和整机切入。",
        "sort": 70,
    },
    {
        "name": "吸塑/塑料成型厂",
        "code": "plastics_factory",
        "keywords": ["吸塑包装", "医疗包装", "吸塑厂", "厚片吸塑"],
        "industry_keys": ["plastic_rubber"],
        "business_lines": ["泵", "配件"],
        "priority": "P1",
        "enabled": 1,
        "notes": "厚片吸塑/医疗包装产线负压成型用泵，单泵和配件复购高频，决策链短。",
        "sort": 80,
    },
    {
        "name": "包装印刷厂",
        "code": "packaging_factory",
        "keywords": ["真空包装", "镀铝膜", "包装印刷"],
        "industry_keys": ["packaging_printing"],
        "business_lines": ["泵", "配件"],
        "priority": "P1",
        "enabled": 1,
        "notes": "真空包装/镀铝膜产线，泵与配件为主，单机复购，设备台数多。",
        "sort": 90,
    },
    {
        "name": "中央系统改造厂",
        "code": "central_retrofit",
        "keywords": ["注塑厂", "压合", "化工厂"],
        "industry_keys": [],
        "business_lines": ["系统集成"],
        "priority": "P1",
        "enabled": 1,
        "notes": "存量注塑厂/压合/化工厂中央真空系统改造，资格清单见 SEGMENTATION §四：应用点≥8；按系统集成切入，行业类目暂不圈名单。",
        "sort": 100,
    },
    {
        # 2026-11-15 用户拍板补建（记忆业务线④："国内外大品牌的真空泵维修、
        # 配件、泵油都会搞"）——跨行业：不管什么行业，厂里装着进口泵就是客户。
        # 名单来源=装机台账（待录 Excel）+竞对中标雷达（73 家在册），行业不圈
        # 名单故 industry_keys=[]；统计显示 0 是诚实状态，名单随台账录入增长。
        "name": "跨品牌维修客户",
        "code": "cross_brand_service",
        "keywords": ["莱宝", "普旭", "阿特拉斯", "爱发科", "真空泵维修"],
        "industry_keys": [],
        "business_lines": ["维修", "配件", "泵油"],
        "priority": "P0",
        "enabled": 1,
        "notes": "厂里装着莱宝/普旭/阿特拉斯等进口泵的工厂——不管什么行业，泵到期/故障就是入口；原厂维修贵且慢，我们跨品牌接活。名单=装机台账（等录入）+竞对中标雷达。",
        "sort": 20,
    },
]


def _json_list(arr: list[str]) -> str:
    """列表字段落库形态：紧凑分隔 JSON（ensure_ascii=False）。"""
    return json.dumps(arr, ensure_ascii=False, separators=(",", ":"))


def seed_customer_segments(session: Session) -> int:
    """客户群种子（SPEC-SEG2 §2 + 2026-11-15 补建跨品牌维修群，幂等）：
    DB 标记防重 + 名称查重双保险。

    首种写入种子全量并把 SyncWatermark[segment_seed] 落 ok；之后 cron/启动
    重跑零写入。admin 删掉的群不被复活（标记命中即整段跳过）；标记丢失时
    按名称查重不重复插（新增种子行靠清标记补种——2026-11-15 跨品牌群即此路）。
    """
    row = session.get(SyncWatermark, SEED_MARKER)
    if row is not None and row.last_status == "ok":
        return 0
    existing = {t for (t,) in session.query(CustomerSegment.name).all()}
    n = 0
    for seg in _SEED_SEGMENTS:
        if seg["name"] in existing:
            continue
        session.add(
            CustomerSegment(
                name=seg["name"],
                code=seg["code"],
                keywords=_json_list(seg["keywords"]),
                industry_keys=_json_list(seg["industry_keys"]),
                business_lines=_json_list(seg["business_lines"]),
                priority=seg["priority"],
                enabled=seg["enabled"],
                notes=seg["notes"],
                sort=seg["sort"],
            )
        )
        n += 1
    if row is None:
        row = SyncWatermark(job_name=SEED_MARKER)
        session.add(row)
    row.last_run_at = utcnow()
    row.last_status = "ok"
    row.stats_json = json.dumps({"seeded": n}, ensure_ascii=False)
    session.commit()
    return n


def _seg_dict(seg: CustomerSegment) -> dict:
    return {
        "id": seg.id,
        "name": seg.name,
        "code": seg.code,
        "keywords": parse_json_list(seg.keywords),
        "industry_keys": parse_json_list(seg.industry_keys),
        "business_lines": parse_json_list(seg.business_lines),
        "priority": seg.priority,
        "enabled": bool(seg.enabled),
        "notes": seg.notes,
        "sort": seg.sort,
    }


def _segment_counts(session: Session) -> tuple[dict[str, int], dict[str, int]]:
    """industry_key → (存活企业数, 去重可拨联系人数)，叠加全部非客户排除。

    一遍 GROUP BY 出两样（SPEC-SEG2 §3 口径）：企业=industry_key 计数
    （customer.non_buyer_conds 与 contacts 同口径 + 存活）；可拨=contacts
    工作列表同款 phones_json SQL 口径的去重联系人。
    """
    nb = non_buyer_conds()
    ent_counts: dict[str, int] = {}
    for key, cnt in (
        session.query(Enterprise.industry_key, func.count())
        .filter(
            Enterprise.deleted_at.is_(None),
            Enterprise.industry_key.is_not(None),
            Enterprise.industry_key != "",
            nb,
        )
        .group_by(Enterprise.industry_key)
        .all()
    ):
        ent_counts[key] = cnt
    # 可拨口径与 contacts 工作列表逐字节一致（NULL/''/'[]' 之外）
    dialable_expr = Contact.phones_json.is_not(None) & Contact.phones_json.notin_(("", "[]"))
    dial_counts: dict[str, int] = {}
    for key, cnt in (
        session.query(Enterprise.industry_key, func.count(func.distinct(Contact.id)))
        .join(Contact, Contact.enterprise_id == Enterprise.id)
        .filter(
            Enterprise.deleted_at.is_(None),
            Enterprise.industry_key.is_not(None),
            Enterprise.industry_key != "",
            nb,
            dialable_expr,
        )
        .group_by(Enterprise.industry_key)
        .all()
    ):
        dial_counts[key] = cnt
    return ent_counts, dial_counts


# ---- 附录 A1 作业化统计口径常量 ----
_INTENT_HIGH_MIN = 60  # intent_high：意图分 ≥60
_NEW_DAYS = 7  # new_7d：近 7 天入库（main.py 每日窗口同款 isoformat 截法）
_LEAD_NOT_MANAGED = ("pool", "lost")  # following：公海/流失之外即在管
_CHUNK = 900  # id 集分批聚合批大小（SQLite in_ 变量上限防御，规避超长参数清单）


def _chunked(ids: list[int], size: int = _CHUNK):
    """企业 id 集分批：逐批聚合后 Python 累加——不逐企业 N+1，也规避 SQLite
    in_ 参数量上限（附录 A1 实现纪律：id 集先算一次，六项统计复用分批聚合）。"""
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


# SPEC-BIM B5-2：跨品牌维修群（code=cross_brand_service）名单来源特判——
# 行业不圈名单（industry_keys=[]），改用 brand_install 去重企业（装机证据：
# 厂里装着进口/竞对泵的工厂）join non_buyer_conds 同口径（政采买家/学校/
# 检测实验室/OEM 不进拨打面，数据保留可查）。
CROSS_BRAND_CODE = "cross_brand_service"


def _cross_brand_ids(session: Session) -> list[int]:
    """跨品牌群企业 id 集：brand_install 去重企业（存活 + 非客户排除同口径）。"""
    nb = non_buyer_conds()
    return [
        rid
        for (rid,) in (
            session.query(BrandInstall.enterprise_id)
            .join(Enterprise, Enterprise.id == BrandInstall.enterprise_id)
            .filter(Enterprise.deleted_at.is_(None), nb)
            .distinct()
            .all()
        )
    ]


def _dialable_count_of(session: Session, ent_ids: list[int]) -> int:
    """企业 id 集的去重可拨联系人数（contacts 工作列表同款 phones_json 口径）。"""
    dialable_expr = Contact.phones_json.is_not(None) & Contact.phones_json.notin_(("", "[]"))
    n = 0
    for ids in _chunked(ent_ids):
        n += (
            session.query(func.count(func.distinct(Contact.id)))
            .filter(Contact.enterprise_id.in_(ids), dialable_expr)
            .scalar()
            or 0
        )
    return n


def _segment_ops_stats(
    session: Session, seg: CustomerSegment, ids_override: list[int] | None = None
) -> dict:
    """附录 A1 作业六字段（"今天能打什么"）：口径与 enterprise_count/dialable_count
    同源——群内企业 id 集**先算一次**（industry_keys OR 括号分组 + 存活 +
    customer.non_buyer_conds 非客户排除），六项统计全部复用该 id 集分批聚合；
    群数仅 10、每群数条聚合 SQL，无逐群逐企业 N+1。

    SPEC-BIM B5-2：ids_override 非 None 时跳过 industry_keys 查询，直接以该
    id 集计算六字段（跨品牌群 brand_install 去重企业来源专用分支）。

    - dialable_untouched：可拨（有号码联系人）且无任一已触达联系人的企业数
      ——"今天能打的"（全 pending 才算没碰过）
    - touched：任一联系人 call_result 非 pending/空 的企业数（与上一项互斥：
      群内企业有触达即不在 untouched 计数里）
    - intent_high：intent_score ≥60 的企业数
    - following：群内在管线索数（lead.status 非公海/流失）
    - new_7d：近 7 天 created_at 入库企业数（新面孔）
    - last_signal：群内最新信号 {type, title(截 40), date, ent_name}；date=
      pub_date 或 created_at 前 10 位，按 pub_date（空回退 created_at）倒序
      取最新；无信号 null
    """
    zero = {
        "dialable_untouched": 0,
        "touched": 0,
        "intent_high": 0,
        "following": 0,
        "new_7d": 0,
        "last_signal": None,
    }
    nb = non_buyer_conds()
    if ids_override is not None:
        ent_ids = list(dict.fromkeys(ids_override))
        if not ent_ids:
            return zero
        ent_names: dict[int, str] = {}
        for ids in _chunked(ent_ids):
            ent_names.update(
                dict(
                    session.query(Enterprise.id, Enterprise.name)
                    .filter(Enterprise.deleted_at.is_(None), Enterprise.id.in_(ids))
                    .all()
                )
            )
    else:
        keys = segment_keys_of(seg)
        if not keys:
            return zero
        rows = (
            session.query(Enterprise.id, Enterprise.name)
            .filter(
                Enterprise.deleted_at.is_(None),
                Enterprise.industry_key.is_not(None),
                Enterprise.industry_key != "",
                # OR 括号分组（与 enterprises.py segment 筛选同款，规避扫描器锚定）
                or_(*[Enterprise.industry_key == k for k in keys]),
                nb,
            )
            .all()
        )
        if not rows:
            return zero
        ent_ids = [rid for rid, _name in rows]
        ent_names = dict(rows)

    # 触达企业级聚合：每企业一行（可拨联系人数, 已触达联系数）一趟 GROUP BY——
    # touched=有已触达联系人；untouched=零触达且有可拨联系人（互斥口径）
    dialable_expr = Contact.phones_json.is_not(None) & Contact.phones_json.notin_(("", "[]"))
    untouched_expr = Contact.call_result.is_(None) | (Contact.call_result == "pending")
    n_untouched = 0
    n_touched = 0
    for ids in _chunked(ent_ids):
        for _eid, dial_n, touch_n in (
            session.query(
                Contact.enterprise_id,
                func.sum(case((dialable_expr, 1), else_=0)),
                func.sum(case((~untouched_expr, 1), else_=0)),
            )
            .filter(Contact.enterprise_id.in_(ids))
            .group_by(Contact.enterprise_id)
            .all()
        ):
            if (touch_n or 0) > 0:
                n_touched += 1
            elif (dial_n or 0) > 0:
                n_untouched += 1

    n_intent = 0
    for ids in _chunked(ent_ids):
        n_intent += (
            session.query(func.count())
            .select_from(Enterprise)
            .filter(
                Enterprise.id.in_(ids),
                Enterprise.intent_score >= _INTENT_HIGH_MIN,
            )
            .scalar()
            or 0
        )

    n_following = 0
    for ids in _chunked(ent_ids):
        n_following += (
            session.query(func.count())
            .select_from(Lead)
            .filter(
                Lead.enterprise_id.in_(ids),
                Lead.status.notin_(_LEAD_NOT_MANAGED),
            )
            .scalar()
            or 0
        )

    # 近 7 天入库（created_at 与 utcnow 同为 ISO 文本，字典序=时间序）
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_NEW_DAYS)).isoformat(
        timespec="seconds"
    )
    n_new = 0
    for ids in _chunked(ent_ids):
        n_new += (
            session.query(func.count())
            .select_from(Enterprise)
            .filter(Enterprise.id.in_(ids), Enterprise.created_at >= cutoff)
            .scalar()
            or 0
        )

    # 群内最新信号：pub_date 空回退 created_at（ISO 文本字典序=时间序）；
    # 分批各取批内最新（date+id 键跨批可比），Python 合并出全局最新一条
    date_expr = case(
        (or_(Signal.pub_date.is_(None), Signal.pub_date == ""), Signal.created_at),
        else_=Signal.pub_date,
    )
    last_signal = None
    best_key: tuple[str, int] | None = None
    for ids in _chunked(ent_ids):
        sig = (
            session.query(Signal)
            .filter(Signal.enterprise_id.in_(ids))
            .order_by(date_expr.desc(), Signal.id.desc())
            .limit(1)
            .first()
        )
        if sig is None:
            continue
        key = ((sig.pub_date or sig.created_at or ""), sig.id)
        if best_key is None or key > best_key:
            best_key = key
            last_signal = {
                "type": sig.type,
                "title": (sig.title or "")[:40],
                "date": (sig.pub_date or sig.created_at or "")[:10],
                "ent_name": ent_names.get(sig.enterprise_id, ""),
            }

    return {
        "dialable_untouched": n_untouched,
        "touched": n_touched,
        "intent_high": n_intent,
        "following": n_following,
        "new_7d": n_new,
        "last_signal": last_signal,
    }


class SegmentIn(BaseModel):
    name: str = Field(min_length=1)
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    keywords: list[str] = []
    industry_keys: list[str] = []
    business_lines: list[str] = []
    priority: str = "P2"
    enabled: int = 1
    notes: str = ""
    sort: int = 100

    @field_validator("priority")
    @classmethod
    def _priority_ok(cls, v: str) -> str:
        if v not in PRIORITIES:
            raise ValueError(f"priority 须为 {'/'.join(PRIORITIES)} 之一")
        return v

    @field_validator("keywords", "industry_keys", "business_lines")
    @classmethod
    def _list_ok(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(x.strip() for x in v if x.strip()))


class SegmentPatch(BaseModel):
    name: str | None = None
    code: str | None = None
    keywords: list[str] | None = None
    industry_keys: list[str] | None = None
    business_lines: list[str] | None = None
    priority: str | None = None
    enabled: int | None = None
    notes: str | None = None
    sort: int | None = None

    @field_validator("priority")
    @classmethod
    def _priority_ok(cls, v: str | None) -> str | None:
        if v is not None and v not in PRIORITIES:
            raise ValueError(f"priority 须为 {'/'.join(PRIORITIES)} 之一")
        return v

    @field_validator("code")
    @classmethod
    def _code_ok(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("code 不能为空")
        return v

    @field_validator("keywords", "industry_keys", "business_lines")
    @classmethod
    def _list_ok(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return list(dict.fromkeys(x.strip() for x in v if x.strip()))


def _dump_lists(body: dict) -> dict:
    """列表字段 → TEXT JSON（keywords/industry_keys/business_lines）。"""
    for field in ("keywords", "industry_keys", "business_lines"):
        if field in body and body[field] is not None:
            body[field] = _json_list(body[field])
    return body


def _ensure_code_free(session: Session, code: str, exclude_id: int | None = None) -> None:
    q = session.query(CustomerSegment.id).filter(CustomerSegment.code == code)
    if exclude_id is not None:
        q = q.filter(CustomerSegment.id != exclude_id)
    if q.first() is not None:
        raise HTTPException(422, f"code 已存在：{code}")


@router.get("/segments")
def list_segments(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),  # 全员可读（画像页 + 企业列表下拉）
):
    """客户群列表（全员）：十群定义 + 实时统计（SPEC-SEG2 §3 + 附录 A1）。

    enterprise_count=群 industry_keys 命中的存活企业（叠加 contacts 同款
    非客户排除）；dialable_count=该群企业去重可拨联系人；附录 A1 追加作业
    六字段（dialable_untouched/touched/intent_high/following/new_7d/
    last_signal）——"今天能打什么"，口径同源（同 id 集+非客户排除）。
    特判：code=cross_brand_service（SPEC-BIM B5-2）名单来源改 brand_install
    去重企业（装机证据），count/dialable/六字段全套同源换算。
    停用群（enabled=0）照常返回定义与统计，由前端置灰——停用只退出推荐/权重。
    """
    rows = (
        session.query(CustomerSegment)
        .order_by(CustomerSegment.sort.asc(), CustomerSegment.id.asc())
        .all()
    )
    ent_counts, dial_counts = _segment_counts(session)
    items: list[dict] = []
    for seg in rows:
        d = _seg_dict(seg)
        if seg.code == CROSS_BRAND_CODE:
            # SPEC-BIM B5-2：跨品牌群名单来源=brand_install 去重企业（装机
            # 证据），count/dialable/六字段全套换源（id 集一次算好复用）
            cross_ids = _cross_brand_ids(session)
            d["enterprise_count"] = len(cross_ids)
            d["dialable_count"] = _dialable_count_of(session, cross_ids)
            d.update(_segment_ops_stats(session, seg, ids_override=cross_ids))
        else:
            keys = segment_keys_of(seg)
            d["enterprise_count"] = sum(ent_counts.get(k, 0) for k in keys)
            d["dialable_count"] = sum(dial_counts.get(k, 0) for k in keys)
            d.update(_segment_ops_stats(session, seg))  # 附录 A1：作业六字段
        items.append(d)
    return {"items": items}


@router.post("/segments", status_code=201)
def create_segment(
    body: SegmentIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    _ensure_code_free(session, body.code)
    payload = _dump_lists(body.model_dump())
    seg = CustomerSegment(**payload)
    session.add(seg)
    session.commit()
    return {"id": seg.id}


@router.put("/segments/{sid}")
def update_segment(
    sid: int,
    body: SegmentPatch,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    seg = session.get(CustomerSegment, sid)
    if seg is None:
        raise HTTPException(404, "客户群不存在")
    payload = _dump_lists(body.model_dump(exclude_unset=True))
    code = payload.get("code")
    if code is not None:
        _ensure_code_free(session, code, exclude_id=sid)
    for field, value in payload.items():
        if value is None:
            continue  # 显式 null 不落（清空传空串/空数组）
        setattr(seg, field, value)
    seg.updated_at = utcnow()
    session.commit()
    return {"id": seg.id}


@router.delete("/segments/{sid}")
def delete_segment(
    sid: int,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    seg = session.get(CustomerSegment, sid)
    if seg is None:
        raise HTTPException(404, "客户群不存在")
    session.delete(seg)
    session.commit()
    return {"ok": True}
