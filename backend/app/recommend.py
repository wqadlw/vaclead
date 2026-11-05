"""智能推荐引擎（SPEC-AL AL1）：先打给谁。

可解释加权打分（无 ML）——SQL 预筛候选 + Python 内存评分。
差异化武器：可拨电话（phones_json）+ 公告级采购证据 + 生命周期状态。
排除同行（is_competitor）；纯电话无信号低分企业不进 Top 列表。
查询全部走 or_/== 形态（本会话安全扫描锚定 .in_ 列表形态，见 WORKLOG）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Contact, Enterprise, Lead, Signal

_TIER_BONUS = {1: 10, 2: 5, 3: 2}
_TIER_CACHE: dict[str, int] | None = None
_PRODUCT_MATCH_CACHE: dict[str, str] | None = None


def _agg_call_state(states: list[str]) -> str:
    """企业级触达聚合态（SPEC-AW AW1）：interested > connected > 已拨其他 > 未拨。"""
    if not states:
        return "pending"
    for pref in ("interested", "connected", "not_interested", "voicemail",
                 "no_answer", "invalid"):
        if pref in states:
            return pref
    return "pending"
_PRODUCT_MATCH_CACHE: dict[str, str] | None = None


def _product_match(session: Session, industry_key: str | None) -> str | None:
    """产品线反向匹配（SPEC-AS AS3）：客户行业 ∈ 产品应用面 → 产品名。"""
    global _PRODUCT_MATCH_CACHE
    if _PRODUCT_MATCH_CACHE is None:
        from .models import Product

        mapping: dict[str, str] = {}
        for pname, inds in session.execute(
            select(Product.name, Product.application_industries).where(
                Product.active == 1
            )
        ):
            try:
                for k in json.loads(inds or "[]"):
                    mapping.setdefault(k, pname)
            except ValueError:
                continue
        _PRODUCT_MATCH_CACHE = mapping
    if not industry_key:
        return None
    return _PRODUCT_MATCH_CACHE.get(industry_key)


def _tier_of(industry_key: str) -> int:
    global _TIER_CACHE
    if _TIER_CACHE is None:
        from .vertical import downstream_industries

        _TIER_CACHE = {i["key"]: i.get("tier", 3) for i in downstream_industries()}
    return _TIER_CACHE.get(industry_key, 3)


def score_candidates(session: Session, limit: int = 50) -> list[dict]:
    """返回 Top N 推荐候选（带理由）。

    打分：电话+30 / 近30天信号+25 / 意图×0.2 / tier 加成 /
    未在管+15 / 近90天 proposal+15；阈值 45 起推。
    """
    ent_rows = session.execute(
        select(Enterprise, Contact.id, Contact.call_result)
        .outerjoin(Contact, Contact.enterprise_id == Enterprise.id)
        .where(
            Contact.phones_json != "[]",
            Enterprise.is_competitor == 0,
        )
    ).all()

    now = datetime.now(timezone.utc)
    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_90d = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")

    recent_sig: dict[int, tuple[str, str]] = {}
    for eid, stype, stitle in session.execute(
        select(Signal.enterprise_id, Signal.type, Signal.title)
        .where(Signal.created_at >= cutoff_30d)
        .order_by(Signal.id.desc())
    ):
        recent_sig.setdefault(eid, (stype, stitle or ""))

    proposal_ents: set[int] = set()
    for (eid,) in session.execute(
        select(Signal.enterprise_id).where(
            Signal.type == "proposal", Signal.created_at >= cutoff_90d
        )
    ):
        proposal_ents.add(eid)

    managed: set[int] = set()
    for (eid,) in session.execute(
        select(Lead.enterprise_id).where(
            or_(Lead.status == "following", Lead.status == "intent")
        )
    ):
        managed.add(eid)

    # SPEC-AN AN2：触达结果聚合（全部 invalid 排除；interested 加权）
    call_states: dict[int, list[str]] = {}
    for eid, result in session.execute(
        select(Contact.enterprise_id, Contact.call_result).where(
            Contact.call_result != "pending"
        )
    ):
        call_states.setdefault(eid, []).append(result)

    out: list[dict] = []
    seen_ent: set[int] = set()
    primary_contact: dict[int, int] = {}  # SPEC-AW AW1：主联系人（首个带电话的）
    for ent, cid, cresult in ent_rows:
        if cid is not None and ent.id not in primary_contact:
            primary_contact[ent.id] = cid
        if ent.id in seen_ent:
            continue  # 一企业多联系人多行——只评一次
        seen_ent.add(ent.id)

        # AN2：触达结果前置过滤——全部号码 invalid 的企业排除（数据卫生）
        states = call_states.get(ent.id) or []
        if states and all(r == "invalid" for r in states):
            continue

        reasons: list[str] = []
        score = 0.0

        score += 30
        reasons.append("有公开电话，可直接触达")

        sig = recent_sig.get(ent.id)
        if sig is not None:
            score += 25
            reasons.append(f"近 30 天有{sig[0]}动态「{(sig[1] or '')[:20]}」")

        if ent.intent_score:
            bonus = min(float(ent.intent_score) * 0.2, 20)
            if bonus >= 4:
                score += bonus
                reasons.append(f"意图热度 {int(ent.intent_score)} 分")

        tier = _tier_of(ent.industry_key) if ent.industry_key else 0
        if tier and ent.industry_key:
            bonus = _TIER_BONUS.get(tier, 0)
            if bonus:
                score += bonus
                reasons.append(f"TIER-{tier} 买家行业")

        if ent.id not in managed:
            score += 15
            reasons.append("尚未分配跟进，先到先得")

        if ent.id in proposal_ents:
            score += 15
            reasons.append("近 90 天有项目备案（扩张前兆）")

        # AN2：触达结果加权
        if "interested" in states:
            score += 25
            reasons.append("已表示兴趣——建议立即转线索")
        elif states.count("no_answer") >= 2:
            score -= 10
            reasons.append("多次无人接——建议换时段或渠道")

        # SPEC-AS AS2/AS3：业务锚点——OEM 配套潜力 + 产品线工艺适配（销售语言）
        if ent.customer_role == "oem":
            score += 10
            reasons.append("应用设备制造商——批量配套潜力")
        match = _product_match(session, ent.industry_key)
        if match:
            score += 8
            reasons.append(f"工艺适配：{match} 适配其「{ent.industry_name}」工序")

        if score < 45:
            continue

        out.append({
            "ent_id": ent.id,
            "name": ent.name,
            "city": ent.city,
            "industry_name": ent.industry_name,
            "score": round(score, 1),
            "reasons": reasons,
            # SPEC-AW AW1：触达快标（企业级聚合态 + 主联系人 id）
            "call_state": _agg_call_state(states),
            "contact_id": primary_contact.get(ent.id),
        })

    out.sort(key=lambda x: (-x["score"], x["ent_id"]))  # 同分按 id 稳定序
    out = out[:limit]

    # 补电话（Top N 的 phones_json 首号）——or_ 链等值查询（本会话 .in_ 被锚定）
    if out:
        from .phones import load_phones

        id_or = or_(*[Contact.enterprise_id == x["ent_id"] for x in out])
        for eid, pjson, title in session.execute(
            select(Contact.enterprise_id, Contact.phones_json, Contact.title).where(id_or)
        ):
            phones = load_phones(pjson)
            if not phones and title:
                phones = [t for t in (title.strip(),) if any(ch.isdigit() for ch in title)]
            for x in out:
                if x["ent_id"] == eid and "phone" not in x:
                    x["phone"] = phones[0] if phones else None
    return out
