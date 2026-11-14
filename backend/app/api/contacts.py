"""联系人中心 API（SPEC-BQ）：跨企业拨号工作台。

"打 10 通电话"的日常作业面：可拨优先、未触达优先、企业意图分排序、
边打边标（快标复用 SPEC-AN 的 /contacts/{cid}/call-result，零新写端点）。
untouched 口径与 SPEC-AZ 一致：有号码且 call_result 为空或 pending。

SPEC-CUX2：列表链路整体下沉 SQL——过滤/排序/分页全部参数化进查询，
删除旧版 `.limit(2000)` 采样（全库 1.4 万+ 可拨联系人里 1.2 万永远翻不到
的数据完整性 bug，offset 形参此前根本没进查询）；新增
POST /contacts/batch-call-result 批量标记（企业级效率）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, User, utcnow
from ..phones import load_phones

router = APIRouter(tags=["contacts"])

_UNTOUCHED = (None, "pending")

# SPEC-CUX2 2：批量标记七态白名单（与 intel._CALL_RESULTS / 前端 CallResultTag
# 的 CALL_RESULTS 三方对齐；单条快标沿用 intel 的 _CALL_RESULTS 校验）
_BATCH_CALL_RESULTS = ("pending", "no_answer", "voicemail", "invalid",
                       "connected", "interested", "not_interested")

# SPEC-CUX CUX3：拨打结果中文标签（与前端 CallResultTag 的 CALL_RESULTS 对齐）
_CALL_RESULT_CN = {
    "pending": "未拨",
    "no_answer": "无人接",
    "voicemail": "语音信箱",
    "invalid": "空号",
    "connected": "已接通",
    "interested": "有意向",
    "not_interested": "无需求",
}


@router.get("/contacts")
def contacts_worklist(
    call_status: str = "",
    has_phone: int = 1,
    decision_role: str = "",
    province: str = "",
    q: str = "",
    # SPEC-CUX CUX2 排序切换：白名单 ""/newest/intent/recent_called，非法值回退默认
    order: str = "",
    # SPEC-CUX3 筛选升级（打电话工作台语义：按"今天想打哪类客户群"组织）：
    industry_key: str = "",  # 昊志产品线客户群（半导体/医药/食品…）
    intent_min: int | None = None,  # 意图分下限（高意图先打）
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """跨企业联系人工作列表（SPEC-BQ BQ1；SPEC-CUX2 分页正确化）。

    排序=作业面语义（默认智能推荐四键）：可拨优先 → 企业意图分降序 →
    未拨优先 → id 降序。SPEC-CUX：order 白名单覆写——newest 最近入库先打 /
    intent 企业意图分降序 / recent_called 已拨在前按拨打时间倒序（未拨垫尾，
    回访视角）。SPEC-CUX2：过滤/排序/分页全部下沉 SQL，COUNT(*) 真分页 +
    offset/limit 进查询，2000 采样上限删除——全量数据任意页可达。
    SPEC-CUX3：页大小 100（一上午一页打完）+ 行业/意图筛选。
    """
    limit = max(1, min(limit, 300))
    offset = max(0, offset)
    qq = q.strip()

    # ---- SPEC-CUX2 1：过滤条件全部下沉 SQL（与旧 Python 版语义逐条一致）----
    # 可拨 = phones_json 非空（load_phones 口径的 SQL 对位：NULL/''/'[]' 之外；
    # NOT IN 对 NULL 行天然取假，is_not(None) 显式表达）
    dialable_expr = Contact.phones_json.is_not(None) & Contact.phones_json.notin_(("", "[]"))
    # 未拨 = call_result 为空或 pending（_UNTOUCHED 口径）
    untouched_expr = Contact.call_result.is_(None) | (Contact.call_result == "pending")
    conds = [Enterprise.deleted_at.is_(None)]
    # 用户定稿（2026-11-11）：不做科研院所/高校生意——获客动线全局排除
    # research_edu（数据保留可查，仅从拨打工作列表隐去）
    conds.append(
        or_(Enterprise.industry_key.is_(None), Enterprise.industry_key != "research_edu")
    )
    if has_phone:
        conds.append(dialable_expr)
    if call_status == "untouched":
        # untouched：可拨 且 未拨
        conds.append(dialable_expr & untouched_expr)
    elif call_status == "touched":
        # touched：可拨 且 已拨（pending/空不算）
        conds.append(dialable_expr & ~untouched_expr)
    elif call_status:
        # 其他值 → call_result 精确等值（interested/connected/...）
        conds.append(Contact.call_result == call_status)
    if decision_role:
        conds.append(Contact.decision_role == decision_role)
    if province:
        conds.append(Enterprise.province == province)
    # SPEC-CUX3：行业（产品线客户群）+ 意图分下限筛选
    if industry_key:
        conds.append(Enterprise.industry_key == industry_key)
    if intent_min is not None:
        conds.append(Enterprise.intent_score >= intent_min)
    if qq:
        # q 双列 LIKE 参数化：联系人名 / 企业名
        like = f"%{qq}%"
        conds.append(Contact.name.like(like) | Enterprise.name.like(like))

    base = (
        session.query(Contact, Enterprise)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(*conds)
    )
    # ---- SPEC-CUX2 1：COUNT(*) 真分页——total 为全量过滤总数（旧版是
    # 2000 采样后 Python 过滤的 len(items)，头部列表数据完整性 bug 根源）----
    total = base.count()

    # 省份分布（筛选下拉口径）：全量过滤集 GROUP BY 取前 12（旧版
    # Counter.most_common(12) 的 SQL 对位，count 降序 + 省份名稳定次序）
    prov_rows = (
        session.query(Enterprise.province, func.count())
        .join(Contact, Contact.enterprise_id == Enterprise.id)
        .filter(*conds)
        .filter(Enterprise.province.is_not(None), Enterprise.province != "")
        .group_by(Enterprise.province)
        .order_by(func.count().desc(), Enterprise.province)
        .limit(12)
        .all()
    )
    provinces = dict(prov_rows)

    # ---- SPEC-CUX2 1：排序 SQL 化（语义与旧 Python 版一致）----
    # SPEC-CUX CUX2 排序切换：白名单键覆写；非法值不进分支（自然回退默认序）。
    _order = order.strip().lower()
    if _order == "newest":
        base = base.order_by(Contact.id.desc())  # 最近入库先打
    elif _order == "intent":
        # 旧版 key=(-(intent or 0), -id)：NULL 意图分按 0 计
        base = base.order_by(
            func.coalesce(Enterprise.intent_score, 0).desc(), Contact.id.desc()
        )
    elif _order == "recent_called":
        # called_at IS NOT NULL desc 前置 + ISO 文本倒序 + id desc：
        # 已拨在前按拨打时间倒序，未拨（NULL）垫尾且按 id 降序——与旧版
        # 两趟稳定排序（reverse 下 None 归空串最小自然垫尾）口径一致
        base = base.order_by(
            Contact.called_at.is_not(None).desc(),
            Contact.called_at.desc(),
            Contact.id.desc(),
        )
    else:
        # 默认智能推荐四键：可拨 → 企业意图分 → 未拨 → id（旧 Python
        # key=(0 if dialable else 1, -(intent or 0), 0 if untouched else 1, -id)）；
        # 注意未拨 CASE 必须带可拨前件（_untouched = dialable and pending），
        # 否则 has_phone=0 时不可拨行的组内次序会偏离旧版
        base = base.order_by(
            case((dialable_expr, 0), else_=1).asc(),
            func.coalesce(Enterprise.intent_score, 0).desc(),
            case((dialable_expr & untouched_expr, 0), else_=1).asc(),
            Contact.id.desc(),
        )
    # offset/limit 真分页进查询（SPEC-CUX2：2000 采样上限删除）
    rows = base.offset(offset).limit(limit).all()

    # KPI 统计走全局口径（独立轻查询），不吃工作列表的 2000 条采样——
    # 否则"可拨/未触达"卡显示的是最近 2000 条的样本数而非全局真值
    stat_pairs = (
        session.query(Contact.phones_json, Contact.call_result)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(
            Enterprise.deleted_at.is_(None),
            # 科研院所/高校排除（与列表口径一致，2026-11-11 用户定稿）
            or_(
                Enterprise.industry_key.is_(None),
                Enterprise.industry_key != "research_edu",
            ),
        )
        .all()
    )
    stats = {"with_phone": 0, "untouched": 0, "touched": 0, "interested": 0}
    for phones_json, call_result in stat_pairs:
        dialable = bool(load_phones(phones_json))
        if not dialable:
            continue
        stats["with_phone"] += 1
        if call_result in _UNTOUCHED:
            stats["untouched"] += 1
        else:
            stats["touched"] += 1
            if call_result == "interested":
                stats["interested"] += 1

    # SPEC-DQ：号码共享标记（拨打现场前置——疑似代理/中介总机 ≥3 家挂靠）。
    # SPEC-CUX2：保留 Python 后处理一层，仅对当前页 50 行标注（不进 SQL）
    from .quality import phone_sharing_map

    sharing = phone_sharing_map(session, min_ents=3)
    items: list[dict] = []
    for c, e in rows:
        phones = load_phones(c.phones_json)
        dialable = len(phones) > 0
        untouched = dialable and c.call_result in _UNTOUCHED
        items.append({
            "id": c.id,
            "name": c.name,
            "role": c.role,
            "title": c.title,
            "decision_role": c.decision_role,
            "verify_state": c.verify_state,
            "email": c.email,
            "phones": phones,
            "call_result": c.call_result,
            "called_at": c.called_at,
            "source": c.source,
            # SPEC-PROV：号码时效（电话会变——采集超 180 天前端标"号码偏旧"）
            "created_at": c.created_at,
            "evidence_claim": c.evidence_claim,
            "enterprise": {
                "id": e.id,
                "name": e.name,
                "province": e.province,
                "city": e.city,
                "industry_name": e.industry_name,
                "intent_score": e.intent_score,
                "is_competitor": e.is_competitor,
                "customer_role": e.customer_role,
            },
            "_dialable": dialable,
            "_untouched": untouched,
            # SPEC-DQ：该联系人号码在共享地图中的最大挂靠数（≥3 = 疑似代理）
            "_shared_ent_count": max((len(sharing.get(p, ())) for p in phones), default=0),
        })
    return {
        "stats": stats,
        "provinces": provinces,
        "items": items,
        "total": total,
    }


@router.post("/contacts/batch-call-result")
def batch_call_result(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """批量触达结果标记（SPEC-CUX2 2，企业级效率）。

    body {ids: number[], result: string}；result 七态白名单（422 拒收）；
    UPDATE ... WHERE id IN ids 仅更新传入 ids（changed=实际命中行数）；
    called_at 统一写 utcnow——与单条快标 /contacts/{cid}/call-result 同口径。
    """
    result = str(body.get("result") or "")
    if result not in _BATCH_CALL_RESULTS:
        raise HTTPException(422, f"非法结果，允许值：{'/'.join(_BATCH_CALL_RESULTS)}")
    ids = body.get("ids")
    # ids 必须为非空整数数组（bool 是 int 子类，显式排除）
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
    ):
        raise HTTPException(422, "ids 必须为非空整数数组")
    # 批量 UPDATE IN：仅传入 ids；不存在的 id 不计入 changed
    changed = (
        session.query(Contact)
        .filter(Contact.id.in_(ids))
        .update(
            {Contact.call_result: result, Contact.called_at: utcnow()},
            synchronize_session=False,
        )
    )
    session.commit()
    return {"changed": changed}


@router.get("/contacts/export")
def contacts_export(
    call_status: str = "",
    decision_role: str = "",
    province: str = "",
    industry_key: str = "",
    intent_min: int | None = None,
    q: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """拨打名单 CSV（SPEC-CE）：当前筛选全量（上限 5000）——外勤场景导出。

    SPEC-CUX3：industry_key/intent_min 与列表筛选口径一致（导出=所见即所得）；
    科研院所/高校排除同步（2026-11-11 用户定稿）。
    号码明文仅限内部自用（W52 定调）；同步流式响应无超时。
    """
    import csv as _csv
    import io as _io

    from fastapi.responses import StreamingResponse

    conds = [
        Enterprise.deleted_at.is_(None),
        or_(
            Enterprise.industry_key.is_(None),
            Enterprise.industry_key != "research_edu",
        ),
    ]
    if industry_key:
        conds.append(Enterprise.industry_key == industry_key)
    if intent_min is not None:
        conds.append(Enterprise.intent_score >= intent_min)
    rows = (
        session.query(Contact, Enterprise)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(*conds)
        .order_by(Contact.id.desc())
        .limit(5000)
        .all()
    )
    qq = q.strip()
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["联系人", "角色", "职务", "企业", "行业", "省份", "城市",
                "号码", "触达结果", "证据"])
    role_cn = {
        "legal_rep": "法人", "buyer_contact": "采购联系人",
        "project_contact": "项目联系人", "map_phone": "地图公开电话",
        "proxy": "代理机构",
    }
    for c, e in rows:
        phones = load_phones(c.phones_json)
        if not phones:
            continue
        untouched = c.call_result in (None, "pending")
        if call_status == "untouched" and not untouched:
            continue
        if call_status == "touched" and untouched:
            continue
        if (call_status not in ("", "untouched", "touched")
                and c.call_result != call_status):
            continue
        if decision_role and (c.decision_role or "") != decision_role:
            continue
        if province and (e.province or "") != province:
            continue
        if qq and qq not in (c.name or "") and qq not in (e.name or ""):
            continue
        w.writerow([
            c.name, role_cn.get(c.role, c.role), c.title or "",
            e.name, e.industry_name or "", e.province or "", e.city or "",
            ";".join(phones), c.call_result or "pending",
            (c.evidence_claim or "")[:80],
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dial_list.csv"},
    )


@router.get("/contacts/{cid}/prep")
def contact_prep(
    cid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """通话准备聚合（SPEC-CH CH1）：为什么现在打 + 怎么聊，一屏原料。"""
    from ..models import Followup, Lead, Signal
    from ..recommend import _product_match

    c = session.get(Contact, cid)
    if c is None:
        from fastapi import HTTPException

        raise HTTPException(404, "联系人不存在")
    e = session.get(Enterprise, c.enterprise_id)
    if e is None or e.deleted_at is not None:
        from fastapi import HTTPException

        raise HTTPException(404, "企业不存在")

    signals = (
        session.query(Signal)
        .filter(Signal.enterprise_id == e.id)
        .order_by(Signal.id.desc())
        .limit(3)
        .all()
    )
    lead = (
        session.query(Lead)
        .filter(Lead.enterprise_id == e.id)
        .first()
    )
    last_followup = None
    if lead is not None:
        f = (
            session.query(Followup)
            .filter(Followup.lead_id == lead.id)
            .order_by(Followup.id.desc())
            .first()
        )
        if f is not None:
            last_followup = {
                "content": f.content, "created_at": (f.created_at or "")[:16]
            }

    # SPEC-CUX CUX3 通话时间线：拨打事件 + 跟进记录合流。
    # lead-contact 关联口径：Lead 无 contact_id，靠共享 enterprise_id 关联
    # （Lead.enterprise_id == Contact.enterprise_id，模型层 UQ enterprise_id+tenant_id）；
    # 该企业名下全部线索的跟进都要进时间线，不止 .first() 那一条。
    timeline: list[dict] = []
    if c.called_at:
        timeline.append({
            "kind": "call",
            "at": c.called_at[:16],
            "label": _CALL_RESULT_CN.get(c.call_result or "", "已拨"),
        })
    followups = (
        session.query(Followup)
        .join(Lead, Lead.id == Followup.lead_id)
        .filter(Lead.enterprise_id == e.id)
        .order_by(Followup.id.desc())
        .all()
    )
    for f in followups:
        timeline.append({
            "kind": "followup",
            "at": (f.created_at or "")[:16],
            "content": f.content,
        })
    # 合流按时间字符串倒序（ISO 截断到分钟，字典序即时间序）
    timeline.sort(key=lambda ev: ev["at"], reverse=True)

    match = _product_match(session, e.industry_key)
    phones = load_phones(c.phones_json)
    # SPEC-DQ：拨打现场共享号码提示（疑似代理/中介总机 ≥3 家挂靠）
    from .quality import phone_sharing_map

    sharing = phone_sharing_map(session, min_ents=3)
    shared_ent_count = max((len(sharing.get(p, set())) for p in phones), default=0)
    # SPEC-SALE S1：话术卡接线——场景映射（优先级 competitor_replace >
    # proposal_window > repeat_purchase > first_contact）+ role_hint 优先，
    # 另附 1 张 generic 兜底卡
    from .script_cards import pick_script_cards

    script_cards = pick_script_cards(session, e, lead, has_followup=bool(followups))
    return {
        "contact": {
            "id": c.id, "name": c.name, "role": c.role, "title": c.title,
            "phones": phones,
            "decision_role": c.decision_role,
            "verify_state": c.verify_state,
            "call_result": c.call_result,
            "evidence_claim": c.evidence_claim,
            "shared_ent_count": shared_ent_count,
        },
        "enterprise": {
            "id": e.id, "name": e.name,
            "industry_name": e.industry_name,
            "province": e.province, "city": e.city,
            "intent_score": e.intent_score,
            "customer_role": e.customer_role,
            "is_competitor": e.is_competitor,
        },
        "signals": [
            {
                "type": s.type, "title": s.title,
                "evidence": s.evidence_claim or s.raw_excerpt or "",
                "pub_date": (s.pub_date or "")[:10],
                "url": s.url,
            }
            for s in signals
        ],
        "product_match": match,
        "lead": (
            {
                "id": lead.id, "status": lead.status,
                "next_action": lead.next_action,
                "next_time": (lead.next_time or "")[:16],
            }
            if lead is not None
            else None
        ),
        "last_followup": last_followup,
        # SPEC-CUX CUX3：通话时间线（拨打 + 跟进合流，时间倒序）
        "timeline": timeline,
        # SPEC-SALE S1：话术卡（场景映射取卡，主场景卡在前 + generic 兜底）
        "script_cards": script_cards,
    }
