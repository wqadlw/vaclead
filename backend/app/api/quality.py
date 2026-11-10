"""数据质量与覆盖中心 API（SPEC-BW）：免费扩池阶梯的可视化账本。

覆盖漏斗（总数→坐标→行业→联系人→可拨）+ 来源分层 + 缺口清单——
"下一桶金在哪"的运营驾驶舱。全局口径只算未删企业；只读零写路径，
回填动作全部复用既有任务（backfill-icp/查重扫描）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import get_session
from ..models import Contact
from ..models import Contact, Enterprise, ReviewQueue, Signal, User
from ..phones import load_phones

router = APIRouter(tags=["quality"])


def _alive(session: Session):
    return session.query(Enterprise).filter(Enterprise.deleted_at.is_(None))


@router.get("/quality/overview")
def quality_overview(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """覆盖漏斗 + 来源分层 + 缺口清单（admin 运营口径）。"""
    rows = _alive(session).all()

    total = len(rows)
    has_geo = 0
    has_industry = 0
    has_province = 0
    by_source: dict[str, dict] = {}
    for e in rows:
        st = by_source.setdefault(e.source or "unknown", {
            "count": 0, "geo": 0, "industry": 0, "province": 0,
            "contact": 0, "dialable": 0,
        })
        st["count"] += 1
        geo = e.lat is not None and e.lng is not None
        ind = bool(e.industry_key)
        prov = bool(e.province)
        has_geo += geo
        has_industry += ind
        has_province += prov
        st["geo"] += geo
        st["industry"] += ind
        st["province"] += prov

    # 联系人/号码：联系人级聚合后回填企业级
    contact_rows = (
        session.query(Contact.enterprise_id, Contact.phones_json)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(Enterprise.deleted_at.is_(None))
        .all()
    )
    ent_contact: dict[int, bool] = {}
    ent_dial: dict[int, bool] = {}
    for eid, pj in contact_rows:
        ent_contact[eid] = True
        if load_phones(pj):
            ent_dial[eid] = True
    has_contact = len(ent_contact)
    dialable = len(ent_dial)
    # 来源回填：按 id 建索引一次，两遍回填均摊 O(n)
    src_by_id = {e.id: (e.source or "unknown") for e in rows}
    for eid in ent_contact:
        by_source[src_by_id.get(eid, "unknown")]["contact"] += 1
    for eid in ent_dial:
        by_source[src_by_id.get(eid, "unknown")]["dialable"] += 1

    for st in by_source.values():
        st["contact_pct"] = round(st["contact"] * 100 / st["count"], 1) if st["count"] else 0.0
        st["dialable_pct"] = round(st["dialable"] * 100 / st["count"], 1) if st["count"] else 0.0
        st["industry_pct"] = round(st["industry"] * 100 / st["count"], 1) if st["count"] else 0.0
        st["geo_pct"] = round(st["geo"] * 100 / st["count"], 1) if st["count"] else 0.0

    # 缺口清单（信号覆盖分母=活跃企业）
    with_signal_alive = (
        session.query(func.count(func.distinct(Signal.enterprise_id)))
        .join(Enterprise, Enterprise.id == Signal.enterprise_id)
        .filter(Enterprise.deleted_at.is_(None))
        .scalar() or 0
    )
    dup_open = (
        session.query(ReviewQueue).filter(ReviewQueue.status == "pending").count()
    )
    trash_count = (
        session.query(func.count())
        .select_from(Enterprise)
        .filter(Enterprise.deleted_at.is_not(None))
        .scalar() or 0
    )
    unclassified_by_source: dict[str, int] = {}
    for e in rows:
        if not e.industry_key:
            k = e.source or "unknown"
            unclassified_by_source[k] = unclassified_by_source.get(k, 0) + 1

    # SPEC-DQ：号码共享分析——同一号码挂 ≥N 家企业 = 疑似招标代理/中介
    # （销售打了发现是中介的根因；不删除，标注供人工判断）
    import json as _json

    phone_ents: dict[str, set[int]] = {}
    contact_rows = (
        session.query(Contact)
        .filter(Contact.phones_json.is_not(None), Contact.phones_json != "[]")
        .all()
    )
    for ct in contact_rows:
        try:
            phones = _json.loads(ct.phones_json or "[]")
        except ValueError:
            continue
        for p in phones if isinstance(phones, list) else []:
            if isinstance(p, str) and len(p) >= 7:
                phone_ents.setdefault(p, set()).add(ct.enterprise_id)
    shared = {
        p: sorted(ents)
        for p, ents in phone_ents.items()
        if len(ents) >= 3
    }
    shared_top = sorted(shared.items(), key=lambda x: -len(x[1]))[:10]

    return {
        "funnel": {
            "total": total,
            "has_geo": has_geo,
            "has_industry": has_industry,
            "has_contact": has_contact,
            "dialable": dialable,
        },
        "gaps": {
            "province_missing": total - has_province,
            "industry_missing": total - has_industry,
            "industry_missing_by_source": dict(
                sorted(unclassified_by_source.items(), key=lambda x: -x[1])[:6]
            ),
            "signal_coverage": with_signal_alive,
            "signal_coverage_pct": round(with_signal_alive * 100 / total, 1) if total else 0.0,
            "dup_open": dup_open,
            "trash_count": trash_count,
        },
        "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1]["count"])),
        # SPEC-DQ：号码共享（疑似代理）——总数 + top10（号码→企业数）
        "phone_sharing": {
            "shared_numbers": len(shared),
            "shared_ent_links": sum(len(v) for v in shared.values()),
            "top": [
                {"phone": p, "ent_count": len(ents), "ent_ids": ents[:6]}
                for p, ents in shared_top
            ],
        },
    }
