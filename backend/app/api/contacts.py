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


@router.get("/contacts")
def contacts_worklist(
    call_status: str = "",
    has_phone: int = 1,
    decision_role: str = "",
    province: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """跨企业联系人工作列表（SPEC-BQ BQ1）。

    排序=作业面语义：可拨优先 → 企业意图分降序 → 未拨优先 → id 降序。
    """
    limit = max(1, min(limit, 200))
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
        })
    # 作业面排序：可拨 → 意图分 → 未拨 → id
    items.sort(
        key=lambda x: (
            0 if x["_dialable"] else 1,
            -(x["enterprise"]["intent_score"] or 0),
            0 if x["_untouched"] else 1,
            -x["id"],
        )
    )
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
