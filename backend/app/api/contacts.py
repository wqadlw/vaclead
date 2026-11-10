"""联系人中心 API（SPEC-BQ）：跨企业拨号工作台。

"打 10 通电话"的日常作业面：可拨优先、未触达优先、企业意图分排序、
边打边标（快标复用 SPEC-AN 的 /contacts/{cid}/call-result，零新写端点）。
untouched 口径与 SPEC-AZ 一致：有号码且 call_result 为空或 pending。
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, User
from ..phones import load_phones

router = APIRouter(tags=["contacts"])

_UNTOUCHED = (None, "pending")

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
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """跨企业联系人工作列表（SPEC-BQ BQ1）。

    排序=作业面语义：可拨优先 → 企业意图分降序 → 未拨优先 → id 降序。
    SPEC-CUX：order 白名单覆写——newest 最近入库先打 / intent 企业意图分降序 /
    recent_called 已拨在前按拨打时间倒序（未拨垫尾，回访视角）。
    """
    limit = max(1, min(limit, 200))
    # SPEC-DQ：号码共享标记（拨打现场前置——疑似代理/中介总机 ≥3 家挂靠）
    from .quality import phone_sharing_map

    sharing = phone_sharing_map(session, min_ents=3)
    rows = (
        session.query(Contact, Enterprise)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(Enterprise.deleted_at.is_(None))
        .order_by(Contact.id.desc())
        .limit(2000)
        .all()
    )
    # KPI 统计走全局口径（独立轻查询），不吃工作列表的 2000 条采样——
    # 否则"可拨/未触达"卡显示的是最近 2000 条的样本数而非全局真值
    stat_pairs = (
        session.query(Contact.phones_json, Contact.call_result)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(Enterprise.deleted_at.is_(None))
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
    qq = q.strip()
    items: list[dict] = []
    for c, e in rows:
        phones = load_phones(c.phones_json)
        dialable = len(phones) > 0
        untouched = dialable and c.call_result in _UNTOUCHED
        if has_phone and not dialable:
            continue
        if call_status == "untouched" and not untouched:
            continue
        if call_status == "touched" and (not dialable or untouched):
            continue
        if call_status and call_status not in ("untouched", "touched") and c.call_result != call_status:
            continue
        if decision_role and (c.decision_role or "") != decision_role:
            continue
        if province and (e.province or "") != province:
            continue
        if qq and qq not in (c.name or "") and qq not in (e.name or ""):
            continue
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
    # 作业面排序：可拨 → 意图分 → 未拨 → id（默认智能推荐序，现状逐字节保留）
    items.sort(
        key=lambda x: (
            0 if x["_dialable"] else 1,
            -(x["enterprise"]["intent_score"] or 0),
            0 if x["_untouched"] else 1,
            -x["id"],
        )
    )
    # SPEC-CUX CUX2 排序切换：白名单键覆写；非法值不进分支（自然回退默认序）。
    # called_at 是 ISO 文本串无法取负 → recent_called 用两趟稳定排序：
    # 先按 id desc 打底（尾键），再按 called_at 文本倒序排（reverse 下
    # None 归空串最小、自然垫尾，等价 SQL 的 is_not(None) desc 双键口径）。
    _order = order.strip().lower()
    if _order == "newest":
        items.sort(key=lambda x: -x["id"])  # 最近入库先打
    elif _order == "intent":
        items.sort(key=lambda x: (-(x["enterprise"]["intent_score"] or 0), -x["id"]))
    elif _order == "recent_called":
        items.sort(key=lambda x: -x["id"])
        items.sort(key=lambda x: x["called_at"] or "", reverse=True)
    total = len(items)
    provinces = Counter(
        i["enterprise"]["province"] for i in items if i["enterprise"]["province"]
    )
    return {
        "stats": stats,
        "provinces": dict(provinces.most_common(12)),
        "items": items[offset:offset + limit],
        "total": total,
    }


@router.get("/contacts/export")
def contacts_export(
    call_status: str = "",
    decision_role: str = "",
    province: str = "",
    q: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """拨打名单 CSV（SPEC-CE）：当前筛选全量（上限 5000）——外勤场景导出。

    号码明文仅限内部自用（W52 定调）；同步流式响应无超时。
    """
    import csv as _csv
    import io as _io

    from fastapi.responses import StreamingResponse

    rows = (
        session.query(Contact, Enterprise)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(Enterprise.deleted_at.is_(None))
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
    }
