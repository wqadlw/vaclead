"""企业情报 API（SPEC-D）：采购关系图谱 / 关联企业 / 关键联系人。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, SettingsKV, Signal, User, utcnow

router = APIRouter(tags=["intel"])

_AMOUNT_RE = re.compile(r"成交价([\d.]+)万元")
_BUYER_RE = re.compile(r"采购人[：:]\s*([^\s，,。；;]+)")
_WINNER_RE = re.compile(r"由(.+?)中标")


def _amount(text: str) -> float:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _counterparties(signals: list[Signal]) -> tuple[set[str], set[str]]:
    """从信号证据句反抽（买家集合, 供应商集合）。"""
    buyers: set[str] = set()
    suppliers: set[str] = set()
    for sig in signals:
        text = sig.evidence_claim or ""
        mb = _BUYER_RE.search(text)
        mw = _WINNER_RE.search(text)
        if sig.type == "tender_win" and mb:
            buyers.add(mb.group(1))
        if sig.type == "tender_buy" and mw:
            suppliers.add(mw.group(1))
    return buyers, suppliers


def _my_signals(session: Session, eid: int) -> list[Signal]:
    return (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid, Signal.type.in_(("tender_win", "tender_buy")))
        .all()
    )


@router.get("/enterprises/{eid}/graph")
def enterprise_graph(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """采购关系图谱（SPEC-D 1）：本企业 ↔ 对手方带金额聚合的边。

    tender_win 信号 → 对手方是采购人（我方卖给他）；tender_buy → 对手方是中标方
    （他卖给我方）。金额从证据句回抽，全部可溯源。
    """
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    edges: dict[str, dict] = {}
    for sig in _my_signals(session, eid):
        text = sig.evidence_claim or ""
        mb = _BUYER_RE.search(text)
        mw = _WINNER_RE.search(text)
        if sig.type == "tender_win" and mb:
            counterparty, role = mb.group(1), "buyer"
        elif sig.type == "tender_buy" and mw:
            counterparty, role = mw.group(1), "supplier"
        else:
            continue
        edge = edges.setdefault(
            counterparty,
            {"name": counterparty, "role": role, "amount": 0.0, "count": 0, "last_date": None},
        )
        edge["amount"] += _amount(text)
        edge["count"] += 1
        if sig.pub_date and (edge["last_date"] is None or sig.pub_date > edge["last_date"]):
            edge["last_date"] = sig.pub_date
    edge_list = sorted(edges.values(), key=lambda x: -x["amount"])
    return {"center": {"id": e.id, "name": e.name, "source": e.source}, "edges": edge_list[:12]}


@router.get("/enterprises/{eid}/related")
def related_enterprises(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """关联企业（SPEC-D 3）：共享同一采购人的其他供应商（竞争情报）、
    共享同一中标方的其他采购人（同行动态）。"""
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    my_buyers, my_suppliers = _counterparties(_my_signals(session, eid))
    result: dict[str, list[dict]] = {"competitors": [], "peers": []}
    if my_buyers or my_suppliers:
        candidates = session.query(Enterprise).filter(Enterprise.id != eid).all()
        for c in candidates:
            c_buyers, c_suppliers = _counterparties(_my_signals(session, c.id))
            shared_buyer = my_buyers & c_buyers
            shared_supplier = my_suppliers & c_suppliers
            if shared_buyer:
                result["competitors"].append({
                    "id": c.id, "name": c.name, "via": sorted(shared_buyer)[0],
                    "is_competitor": c.is_competitor,
                })
            elif shared_supplier:
                result["peers"].append({
                    "id": c.id, "name": c.name, "via": sorted(shared_supplier)[0],
                })
    result["competitors"] = result["competitors"][:6]
    result["peers"] = result["peers"][:6]
    return result


@router.get("/enterprises/{eid}/contacts")
def list_contacts(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """关键联系人（SPEC-D 2）：全部带证据溯源，不发明数据。"""
    e = session.get(Enterprise, eid)
    if e is None:
        raise HTTPException(404, "企业不存在")
    rows = session.query(Contact).filter(Contact.enterprise_id == eid).all()
    from ..phones import load_phones

    return {
        "items": [
            {"id": c.id, "name": c.name, "role": c.role, "title": c.title,
             "email": c.email, "decision_role": c.decision_role,
             "verify_state": c.verify_state,
             "phones": load_phones(c.phones_json),  # SPEC-AH AH3：规范化可拨号码
             "call_result": c.call_result,  # SPEC-AN AN1 触达结果
             "called_at": c.called_at,
             "source": c.source, "evidence_claim": c.evidence_claim}
            for c in rows
        ]
    }


class ContactManualIn(BaseModel):
    """手工录入联系人（SPEC-CL）：风鸟/企查查查到后直接记进来。"""
    name: str
    phone: str = ""
    title: str = ""
    role: str = "buyer_contact"

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("姓名不能为空")
        return v.strip()


_ROLES = ("legal_rep", "buyer_contact", "project_contact", "map_phone", "proxy")


@router.post("/enterprises/{eid}/contacts", status_code=201)
def create_contact_manual(
    eid: int,
    body: ContactManualIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """手工录入一条联系人（SPEC-CL）：查到电话直接记，进可拨库与推荐。

    电话走规范化抽取（手机/座机含分机）；同企业已有同号码 → 422 拒重。
    source=manual，evidence 如实标注"手工录入"。
    """
    if body.role not in _ROLES:
        raise HTTPException(422, f"非法角色，允许值：{'/'.join(_ROLES)}")
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "企业不存在")
    from ..phones import dump_phones, extract_phones, load_phones

    phones = extract_phones(body.phone)
    if body.phone.strip() and not phones:
        raise HTTPException(422, "电话格式无法识别（手机号或座机）")

    def _base(num: str) -> str:
        """座机去分机段（0512-66668888-801 → 0512-66668888）；手机号原样。"""
        parts = num.split("-")
        return "-".join(parts[:2]) if len(parts) > 2 else num

    new_bases = {_base(p) for p in phones}
    for c in session.query(Contact).filter(Contact.enterprise_id == eid).all():
        if {_base(p) for p in load_phones(c.phones_json)} & new_bases:
            raise HTTPException(422, "该号码在此企业下已存在（含分机差异）")
    c = Contact(
        enterprise_id=eid,
        name=body.name,
        role=body.role,
        title=body.title.strip() or None,
        phones_json=dump_phones(phones),
        source="manual",
        evidence_claim="手工录入（如风鸟/企查查查询后登记）",
    )
    session.add(c)
    session.commit()
    return {"id": c.id, "phones": phones}


class ContactUpdateIn(BaseModel):
    """编辑联系人（SPEC-CP CP1）：全 Optional，仅更新提供字段。"""
    name: str | None = None
    phone: str | None = None
    title: str | None = None
    role: str | None = None


@router.patch("/contacts/{cid}")
def update_contact(
    cid: int,
    body: ContactUpdateIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """编辑联系人（SPEC-CP CP1）：错号/错名修正——信任基础的运维能力。

    电话走规范化重写（手机/座机含分机）；同企业其他联系人基础号查重；
    role 白名单；仅更新提供字段。
    """
    from ..phones import dump_phones, extract_phones, load_phones

    c = session.get(Contact, cid)
    if c is None:
        raise HTTPException(404, "联系人不存在")
    e = session.get(Enterprise, c.enterprise_id)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "所属企业不存在")

    if body.role is not None:
        if body.role not in _ROLES:
            raise HTTPException(422, f"非法角色，允许值：{'/'.join(_ROLES)}")
        c.role = body.role
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(422, "姓名不能为空")
        c.name = body.name.strip()
    if body.title is not None:
        c.title = body.title.strip() or None

    if body.phone is not None:
        phones = extract_phones(body.phone)
        if body.phone.strip() and not phones:
            raise HTTPException(422, "电话格式无法识别（手机号或座机）")

        def _base(num: str) -> str:
            parts = num.split("-")
            return "-".join(parts[:2]) if len(parts) > 2 else num

        new_bases = {_base(p) for p in phones}
        for other in session.query(Contact).filter(Contact.enterprise_id == c.enterprise_id).all():
            if other.id == c.id:
                continue
            if {_base(p) for p in load_phones(other.phones_json)} & new_bases:
                raise HTTPException(422, "该号码在此企业下已存在（含分机差异）")
        c.phones_json = dump_phones(phones)

    session.commit()
    return {
        "id": c.id, "name": c.name, "role": c.role, "title": c.title,
        "phones": load_phones(c.phones_json),
    }


@router.delete("/contacts/{cid}")
def delete_contact(
    cid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """删除联系人（SPEC-CP CP1）：错号修正——公开渠道派生数据，可重新
    回填恢复，风险低全员可删。
    """
    c = session.get(Contact, cid)
    if c is None:
        raise HTTPException(404, "联系人不存在")
    e = session.get(Enterprise, c.enterprise_id)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "所属企业不存在")
    session.delete(c)
    session.commit()
    return {"ok": True}


_CALL_RESULTS = ("pending", "no_answer", "voicemail", "invalid",
                 "connected", "interested", "not_interested")


@router.post("/contacts/{cid}/call-result")
def set_call_result(
    cid: int,
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """触达结果标记（SPEC-AN AN1，受控枚举）：连接类与对话类分层。"""
    result = str(body.get("result") or "")
    if result not in _CALL_RESULTS:
        raise HTTPException(422, f"非法结果，允许值：{'/'.join(_CALL_RESULTS)}")
    c = session.get(Contact, cid)
    if c is None:
        raise HTTPException(404, "联系人不存在")
    c.call_result = result
    c.called_at = utcnow()
    session.commit()
    return {"ok": True, "id": cid, "call_result": result, "called_at": c.called_at}


_GS_LABELS = {
    "统一社会信用代码": "uscc", "信用代码": "uscc", "纳税人识别号": "uscc",
    "法定代表人": "legal_rep", "法人": "legal_rep", "负责人": "legal_rep",
    "注册资本": "_capital",
    "成立日期": "established_at", "注册日期": "established_at",
    "企业电话": "_phone", "联系电话": "_phone", "电话": "_phone",
    "企业地址": "address", "注册地址": "address", "地址": "address",
    "经营范围": "business_scope",
}


def _parse_capital_raw(raw: str) -> float | None:
    import re as _re

    m = _re.search(r"([\d.,]+)\s*万", raw.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_date_raw(raw: str) -> str | None:
    import re as _re

    m = _re.search(r"(\d{4})[年.-]\s*(\d{1,2})[月.-]\s*(\d{1,2})", raw)
    if not m:
        m2 = _re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        return m2.group(1) if m2 else None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


@router.post("/enterprises/{eid}/gongshang-paste")
def gongshang_paste(
    eid: int,
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """从风鸟企业页整块复制的"字段名 值"文本 → 解析入库（SPEC-CM）。

    吃原始粘贴（每行"标签：值"/"标签 值"/Tab 分隔均可）；**仅补空不
    覆盖**；电话进联系人（source=riskbird，基础号查重）。返回逐字段
    结果供 UI 显示"补了什么/跳过什么"。
    """
    from ..models import Contact as _CT
    from ..normalize import resolve_enterprise  # noqa: F401 语义对齐提示
    from ..phones import dump_phones as _dp
    from ..phones import extract_phones as _ep
    from ..phones import load_phones as _lp

    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "企业不存在")
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(422, "粘贴内容为空")

    import re as _re

    filled: dict[str, str] = {}
    skipped: list[str] = []
    phone_added = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _re.split(r"[：:]|" + chr(9) + r"|\s{2,}|(?<=[一-龥])\s(?=\S)", line, maxsplit=1)
        if len(parts) != 2:
            continue
        label = parts[0].strip()
        value = parts[1].strip()
        # 风鸟 TSV 尾巴净化：复制表格时值格常连带"复制"按钮文本与后续字段
        # （实锤 2026-11-09：uscc="914403006189353349复制\t企业名称\t深圳森丰…复制"）
        value = value.split(chr(9))[0].strip()
        if value.endswith("复制"):
            value = value[: -len("复制")].strip()
        if not label or not value:
            continue
        field = _GS_LABELS.get(label)
        if field is None:
            continue
        if field == "uscc":
            # 统一社会信用代码强格式（18 位，不含 I/O/S/V/Z）：从粘连值里提取
            m = _re.search(r"[0-9A-HJ-NPQRTUWXY]{18}", value)
            if not m:
                skipped.append(label + "（非 18 位信用代码）")
                continue
            value = m.group(0)
        if field == "_capital":
            cap = _parse_capital_raw(value)
            if cap is not None and not e.registered_capital_wan:
                e.registered_capital_wan = cap
                filled["注册资本"] = value
            elif cap is None:
                skipped.append(label + "（无法解析金额）")
            else:
                skipped.append(label + "（已有值）")
            continue
        if field == "_phone":
            phones = _ep(value)
            added = False
            for ph in phones:
                base_new = ph.split("-")
                base_new = "-".join(base_new[:2]) if len(base_new) > 2 else ph
                dup = False
                for c in session.query(_CT).filter(_CT.enterprise_id == eid).all():
                    bases = {
                        "-".join(p.split("-")[:2]) if len(p.split("-")) > 2 else p
                        for p in _lp(c.phones_json)
                    }
                    if base_new in bases:
                        dup = True
                        break
                if dup:
                    continue
                session.add(_CT(
                    enterprise_id=eid, name="工商档案电话", role="map_phone",
                    phones_json=_dp([ph]), source="riskbird",
                    evidence_claim="风鸟工商信息粘贴（" + label + "）",
                ))
                phone_added = True
                added = True
            if added:
                filled[label] = value
            elif phones:
                skipped.append(label + "（号码已存在）")
            continue
        # 文本字段：仅补空
        current = getattr(e, field, None)
        if current:
            skipped.append(label + "（已有值）")
            continue
        if field == "established_at":
            norm = _parse_date_raw(value)
            if not norm:
                skipped.append(label + "（无法解析日期）")
                continue
            setattr(e, field, norm)
        else:
            setattr(e, field, value)
        # SPEC-PROV：字段级溯源打标（手工粘贴=风鸟搬运，操作人=登录者）
        e.mark_prov(field, "风鸟搬运 · 手工粘贴", getattr(user, "name", "") or user.username)
        filled[label] = value
    session.commit()
    return {"filled": filled, "skipped": skipped, "phone_added": phone_added}


# ---- SPEC-INFO v2 情报中心一期：情报晨报 + 机会雷达 ----
# 核心洞察（INFO_CENTER_PLAN v2）：信息价值=时机。分散信号聚合为
# "今天该打谁"的时机判断。research_edu 全局排除（用户定稿）。

_INTEL_TYPES = {
    "tender_win": {"cn": "新中标", "icon": "🏆",
                   "why": "他们刚拿下项目——预算刚花，正是配套设备谈判窗口"},
    "proposal": {"cn": "新备案", "icon": "🌱",
                 "why": "备案=扩产前兆——1-3 个月后有真空泵需求"},
    "web_change": {"cn": "官网变更", "icon": "🖥",
                   "why": "官网提到新产线/招聘——扩张进行时"},
    "tender": {"cn": "采购公告", "icon": "📋",
               "why": "正在采购——招标窗口期内直接响应"},
    "tender_buy": {"cn": "公开采购", "icon": "📋",
                   "why": "公开采购挂网——采购中直接联系"},
}

# 机会雷达类型权重（INFO_CENTER_PLAN v2 3.2）
_OPP_WEIGHTS = {"tender_win": 30, "proposal": 25, "web_change": 20, "tender": 10, "tender_buy": 10}


def _intel_exclusions(session: Session):
    """科研院所/高校排除（research_edu 企业 id 集——用户定稿不做的生意）。"""
    ids = {
        row[0]
        for row in session.query(Enterprise.id).filter(
            Enterprise.industry_key == "research_edu"
        )
    }
    return ids


@router.get("/intel/brief")
def intel_brief(
    days: int = 7,
    kind: str = "",
    page: int = 1,
    page_size: int = 30,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """情报晨报（SPEC-INFO v2）：按日分组的获客情报流——每条带获客判断语。"""
    from datetime import datetime, timedelta

    from sqlalchemy import select as _sel

    cut = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    excl = _intel_exclusions(session)
    # SPEC-INFO v2：已处理集（按用户 settings_kv 存 signal id，尾部 500 截断）
    import json as _json

    seen_row = session.get(SettingsKV, f"intel_seen:{user.id}")
    try:
        seen_list = _json.loads(seen_row.value if seen_row else "[]")
    except ValueError:
        seen_list = []
    seen_set = set(seen_list)
    rows = (
        session.execute(
            _sel(Signal, Enterprise)
            .select_from(Signal)
            .join(Enterprise, Signal.enterprise_id == Enterprise.id)
            .where(
                Signal.created_at >= cut,
                Enterprise.deleted_at.is_(None),
                Enterprise.id.notin_(excl) if excl else Enterprise.id > 0,
                # 商业模式推论（2026-11-13）：昊志卖真空泵——泵制造商不买泵
                Enterprise.name.notlike("%真空泵%"),
            )
            .order_by(Signal.created_at.desc())
            .limit(300)
        )
        .all()
    )
    items = []
    for sig, ent in rows:
        meta = _INTEL_TYPES.get(sig.type)
        if meta is None:
            continue
        if kind and sig.type != kind:
            continue
        items.append({
            "signal_id": sig.id,
            "kind": sig.type,
            "kind_cn": meta["cn"],
            "icon": meta["icon"],
            "why": meta["why"],
            "title": sig.title,
            "ent_id": ent.id,
            "ent_name": ent.name,
            "industry_name": ent.industry_name,
            "province": ent.province,
            "at": (sig.created_at or "")[:16],
            "evidence": (sig.evidence_claim or "")[:120],
            "url": sig.url,
            # SPEC-INFO v2：已处理标记（前端灰显/隐藏，晨报闭环）
            "seen": sig.id in seen_set,
        })
    total = len(items)
    lo = max(0, (page - 1) * page_size)
    return {"items": items[lo: lo + page_size], "total": total, "page": page, "days": days,
            "seen": seen_list[:500]}


@router.get("/intel/seen")
def intel_seen_get(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """晨报已处理标记（SPEC-INFO v2：按用户存，最近 500 条防膨胀）。"""
    import json as _json

    row = session.get(SettingsKV, f"intel_seen:{user.id}")
    try:
        ids = _json.loads(row.value if row else "[]")
    except ValueError:
        ids = []
    return {"seen": ids[:500]}


@router.post("/intel/seen")
def intel_seen_mark(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """标记晨报条目已处理：合并进用户已处理集（幂等，尾部 500 截断）。"""
    import json as _json

    new_ids = [int(x) for x in (body.get("signal_ids") or []) if str(x).isdigit()]
    row = session.get(SettingsKV, f"intel_seen:{user.id}")
    try:
        ids = _json.loads(row.value if row else "[]")
    except ValueError:
        ids = []
    merged = sorted(set(ids) | set(new_ids))[-500:]
    if row is None:
        session.add(SettingsKV(key=f"intel_seen:{user.id}", value=_json.dumps(merged)))
    else:
        row.value = _json.dumps(merged)
    session.commit()
    return {"ok": True, "seen": merged}


@router.get("/intel/opp")
def intel_opportunity(
    days: int = 30,
    top: int = 20,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """机会雷达（SPEC-INFO v2）：采购时机分——多信号加权 × 时间衰减。

    时机分 = Σ(类型权重 × (1 - 距今天数/30))，tender_win 30/proposal 25/
    web_change 20/新号码 15/tender 10。research_edu 排除。"""
    from datetime import datetime, timedelta

    from sqlalchemy import select as _sel

    cut = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    excl = _intel_exclusions(session)
    rows = (
        session.execute(
            _sel(Signal, Enterprise)
            .select_from(Signal)
            .join(Enterprise, Signal.enterprise_id == Enterprise.id)
            .where(
                Signal.created_at >= cut,
                Enterprise.deleted_at.is_(None),
                # 机会雷达找的是客户——同行（竞对）不是机会
                Enterprise.is_competitor == 0,
                # 商业模式推论：泵制造商不买泵（真空设备 OEM 除外，不误伤）
                Enterprise.name.notlike("%真空泵%"),
                Enterprise.id.notin_(excl) if excl else Enterprise.id > 0,
            )
        )
        .all()
    )
    now = datetime.utcnow()
    scored: dict[int, dict] = {}
    for sig, ent in rows:
        w = _OPP_WEIGHTS.get(sig.type, 0)
        if w == 0:
            continue
        d = sig.created_at or ""
        try:
            ran = datetime.fromisoformat(d)
            if ran.tzinfo is not None:
                ran = ran.replace(tzinfo=None)  # ISO 时区坑（SPEC-DASH 同款）
            age = (now - ran).days
        except ValueError:
            age = days
        decay = max(0.0, 1 - age / max(days, 1))
        rec = scored.setdefault(ent.id, {
            "ent_id": ent.id, "name": ent.name,
            "industry_name": ent.industry_name, "province": ent.province,
            "score": 0.0, "kinds": [], "last_at": "",
        })
        rec["score"] += w * decay
        if sig.type not in rec["kinds"]:
            rec["kinds"].append(sig.type)
        if d > rec["last_at"]:
            rec["last_at"] = d
    ranked = sorted(scored.values(), key=lambda x: -x["score"])[: max(1, min(top, 50))]
    # SPEC-INFO v2：已跟进标注——企业已有活跃线索（非公海/流失）= 已在跟进
    # （雷达只推"还没人跟"的新机会，已跟进的沉到列表尾不消失）
    from ..models import Lead as _Lead

    active_lead_ents = {
        row[0]
        for row in session.query(_Lead.enterprise_id).filter(
            _Lead.status.notin_(("pool", "lost"))
        )
    }
    for r in ranked:
        r["score"] = round(r["score"], 1)
        r["followed"] = r["ent_id"] in active_lead_ents
    # 已跟进的沉到列表尾（同分内），保持"未跟进优先"的作业面语义
    ranked.sort(key=lambda x: x["followed"])
    return {"items": ranked, "days": days}


# ---- SPEC-EXT：企业情报直达系统（外部权威源深链矩阵） ----
# 设计立场（用户定稿）：不爬（公示站有验证码）、不买 API（企查查付费）——
# 平台做"导航层"：每家企业一键直达各权威源的搜索/详情页，查到后经
# 工商补数粘贴框 / 批量回填通道入库——查→搬→存→用四步闭环。
# 模板 {name}=企业名 urlencode；加新源只需追加一项（v2 再做 UI 自定义）。
_EXT_SOURCE_TEMPLATES: list[dict] = [
    {"key": "riskbird", "label": "风鸟", "category": "工商",
     "what": "工商全要素 + 电话（已登录直连企业页）",
     "url": "https://www.riskbird.com/ent/{name}"},
    {"key": "aiqicha", "label": "爱企查", "category": "工商",
     "what": "工商全要素（百度系，免费额度大，登录门槛低）",
     "url": "https://aiqicha.baidu.com/s?q={name}"},
    {"key": "qcc", "label": "企查查", "category": "工商",
     "what": "工商 + 股东 + 电话（需登录）",
     "url": "https://www.qcc.com/web/search?key={name}"},
    {"key": "tianyancha", "label": "天眼查", "category": "工商",
     "what": "工商 + 关联企业 + 涉诉（需登录）",
     "url": "https://www.tianyancha.com/search?key={name}"},
    {"key": "creditchina", "label": "信用中国", "category": "涉诉失信",
     "what": "行政处罚 / 失信记录（官方免费）",
     "url": "https://public.creditchina.gov.cn/credit-check/index.html?searchtype=0&keyword={name}"},
    {"key": "wenshu", "label": "裁判文书", "category": "涉诉失信",
     "what": "涉诉判决（合同纠纷/货款——合作前必查）",
     "url": "https://wenshu.court.gov.cn/website/wenshu/181217CFMTKH42837/index.html?pageId=&s21={name}"},
    {"key": "bing_phone", "label": "必应搜电话", "category": "电话",
     "what": "企业名+电话 组合搜索（官网/黄页/B2B 站常挂公开电话）",
     "url": "https://www.bing.com/search?q={name}+电话"},
    {"key": "bing_bid", "label": "必应搜中标", "category": "招投标",
     "what": "企业名+中标 组合搜索（各交易平台的公开中标公告）",
     "url": "https://www.bing.com/search?q={name}+中标"},
    {"key": "boss_zp", "label": "BOSS 直聘", "category": "招聘信号",
     "what": "招真空泵/售后工程师 = 扩张与装机增长前兆（人工浏览，不爬）",
     "url": "https://www.zhipin.com/web/job/search?query={name}"},
    {"key": "liepin_zp", "label": "猎聘", "category": "招聘信号",
     "what": "企业招聘页——新增岗位=产能/服务扩张的获客前兆",
     "url": "https://www.liepin.com/zhaopin/?key={name}"},
]


@router.get("/enterprises/{eid}/external-links")
def enterprise_external_links(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """SPEC-EXT：按企业生成外部权威源深链矩阵（导航层，不爬不存）。"""
    from urllib.parse import quote

    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at is not None:
        raise HTTPException(404, "企业不存在")
    name = e.name or ""
    qname = quote(name)
    # SPEC-RADAR：招聘类目——企业招"真空泵工程师/售后工程师"= 扩张/装机
    # 增长的获客前兆（跳转形态，不爬招聘站）
    sources = [
        {**t, "url": t["url"].replace("{name}", qname)}
        for t in _EXT_SOURCE_TEMPLATES
    ]
    # 按类目分组（前端卡片矩阵渲染）
    categories: dict[str, list] = {}
    for s in sources:
        categories.setdefault(s["category"], []).append(s)
    return {
        "enterprise": {"id": e.id, "name": name},
        "categories": [
            {"category": cat, "sources": items}
            for cat, items in categories.items()
        ],
        # 闭环提示：查到 → 这两个入库通道
        "backfill_hint": {
            "paste": f"/enterprises/view?id={e.id}#gongshang",
            "batch": "/tasks",
        },
    }
