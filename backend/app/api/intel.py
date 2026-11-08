"""企业情报 API（SPEC-D）：采购关系图谱 / 关联企业 / 关键联系人。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, Signal, User, utcnow

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
        if not label or not value:
            continue
        field = _GS_LABELS.get(label)
        if field is None:
            continue
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
        filled[label] = value
    session.commit()
    return {"filled": filled, "skipped": skipped, "phone_added": phone_added}
