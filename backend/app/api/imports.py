"""数据导入路由：企业 CSV / 招标公告 CSV（词表匹配生成意图信号）。"""
from __future__ import annotations

import csv
import io
import math
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..auth import admin_only
from ..collectors.tender import _decode_csv_bytes
from ..db import get_session
from ..models import Enterprise, Signal, User, utcnow
from ..normalize import resolve_enterprise
from ..vertical import (
    detect_industry,
    downstream_industries,
    signal_weights,
)

router = APIRouter(tags=["imports"])

# 从标题/正文中提取企业名的保守模式（XX公司/厂/集团 专名）
# 企业名抽取（两级）：
# 1) _COMPANY_RE 严格模式：以"公司/集团/厂"等后缀结尾的完整企业名（含括号分段）
# 2) _ORG_RE 宽松模式：采购单位常见形态（XX中心/XX局/XX学院/XX研究所/XX医院/XX学校/XX委员会）
# 顺序：先标题后正文；严格模式优先于宽松模式。
_COMPANY_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,28}?(?:有限公司|股份有限公司|有限责任公司|公司|集团|工厂|厂))"
)
_ORG_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,18}?(?:研究中心|研究所|研究院|检测中心|疾控中心|交易中心|管理中心|服务中心|"
    r"采购中心|管理局|财政局|发改委|人民政府|委员会|大学|学院|学校|医院))"
)
# 宽松模式的黑名单：行政区名/泛称不是采购单位
_ORG_BLACKLIST_SUFFIX = ("县", "市", "省", "区", "镇", "乡", "园区", "新区")


def _org_name_ok(name: str) -> bool:
    """过滤'某某县/某某市'这类纯行政区名（宽松模式误抓）。"""
    return not name.endswith(_ORG_BLACKLIST_SUFFIX)


def extract_enterprise_name(title: str, content: str) -> str:
    """从公告标题/正文抽取企业（或采购单位）名：严格企业名 > 宽松机构名，先标题后正文。

    抽不到返回空串——调用方不应把整条公告丢弃（落"待归属"占位，见 imports）。
    """
    for text in (title, content):
        if not text:
            continue
        m = _COMPANY_RE.search(text)
        if m:
            name = m.group(1).strip("（(〕 ")
            # 常见误抽：后缀前是"建设/采购/项目/内容"等动词性词组的直接否定
            if not re.search(r"(建设|采购|项目|内容|施工|监理|咨询|设计|服务|货物)$", name[:4]):
                return name
    for text in (title, content):
        if not text:
            continue
        candidates = [
            m.group(1).strip("（(〕 ")
            for m in _ORG_RE.finditer(text)
        ]
        # 取最长合法匹配（"中国科学院某光学精密机械研究所"优于截断的"中国科学院"）
        candidates = [c for c in candidates if _org_name_ok(c)]
        if candidates:
            return max(candidates, key=len)
    return ""


def _decay(base_score: float, pub_date: str | None) -> float:
    half_life = float(signal_weights().get("intent_half_life_days", 30))
    if pub_date:
        try:
            d = datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days = max((datetime.now(timezone.utc) - d).days, 0)
            return base_score * (0.5 ** (days / half_life))
        except ValueError:
            pass
    return base_score


def recompute_intent(session: Session, enterprise_id: int) -> float:
    signals = session.query(Signal).filter(Signal.enterprise_id == enterprise_id).all()
    score = sum(_decay(s.base_score, s.pub_date) for s in signals)
    ent = session.get(Enterprise, enterprise_id)
    if ent is not None:
        ent.intent_score = round(score, 2)
        ent.updated_at = utcnow()
    return round(score, 2)


def apply_icp(ent: Enterprise) -> None:
    """ICP 打分（与 agents.impl.icp_judge_rule 同一套规则）。

    优先调用 Agent 框架实现（可观测）；框架未就绪时退回本地同规则计算。
    """
    try:
        from ..agents.impl.icp_judge_rule import judge_enterprise

        result = judge_enterprise(
            name=ent.name,
            business_scope=ent.business_scope or "",
            registered_capital_wan=ent.registered_capital_wan,
            industry_key=ent.industry_key,
        )
        ent.icp_score = result["icp_score"]
        ent.set_reasons(result["icp_reasons"])
        ent.is_competitor = result["is_competitor"]
        return
    except Exception:
        pass
    from ..vertical import (
        device_strong_terms,
        icp_weights,
        manufacture_or_sale_terms,
        manufacturing_terms,
        qualification_terms,
    )

    w = icp_weights()
    scope = ent.business_scope or ""
    text_all = f"{ent.name} {scope} {ent.industry_name or ''}"
    reasons: list[dict] = []
    score = 0.0
    ind = detect_industry(text_all)
    if ind is not None:
        pts = w.get(f"industry_tier{ind['tier']}", 0)
        score += pts
        reasons.append({"rule": f"行业命中:{ind['name']}(tier{ind['tier']})", "points": pts})
        ent.industry_key = ind["key"]
        ent.industry_name = ind["name"]
    strong_terms = device_strong_terms()
    mfg_terms = manufacturing_terms()
    qual_terms = qualification_terms()
    from ..vertical import manufacture_or_sale_terms

    # 同行判定：名称含"真空"且具备设备制造特征（名称含"设备"或经营范围含设备制造/真空泵生产等）
    # 例：XX真空设备有限公司✓、YY真空科技(产真空泵)✓；而真空镀膜材料厂/镀膜加工厂是客户✗
    equipment_markers = ("设备制造", "设备生产", "设备销售", "真空泵", "真空机组")
    is_competitor = ("真空" in ent.name) and (
        "设备" in ent.name or any(m in scope for m in equipment_markers)
    )
    if not is_competitor and any(t in scope for t in strong_terms):
        score += w.get("device_usage_terms", 0)
        reasons.append({"rule": "经营范围含设备使用词", "points": w.get("device_usage_terms", 0)})
    cap = ent.registered_capital_wan
    if cap is not None:
        if cap >= 1000:
            score += w.get("capital_gte_1000w", 0)
            reasons.append({"rule": "注册资本≥1000万", "points": w.get("capital_gte_1000w", 0)})
        elif cap >= 100:
            score += w.get("capital_100w_1000w", 0)
            reasons.append({"rule": "注册资本100-1000万", "points": w.get("capital_100w_1000w", 0)})
    if any(t in scope for t in mfg_terms):
        score += w.get("manufacturing_terms", 0)
        reasons.append({"rule": "经营范围含生产/制造/加工", "points": w.get("manufacturing_terms", 0)})
    if any(t in text_all for t in qual_terms):
        score += w.get("qualification", 0)
        reasons.append({"rule": "专精特新/高企标识", "points": w.get("qualification", 0)})
    ent.icp_score = min(round(score, 1), float(w.get("max_score", 100)))
    ent.set_reasons(reasons)
    ent.is_competitor = 1 if is_competitor else 0


@router.post("/import/enterprises")
async def import_enterprises(
    file: UploadFile,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    raw = await file.read()
    text_content = _decode_csv_bytes(raw)

    reader = csv.DictReader(io.StringIO(text_content))
    inserted, merged, skipped = 0, 0, 0
    batch_id = uuid.uuid4().hex[:12]
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        ent, is_new = resolve_enterprise(session, name, source="csv")
        if is_new:
            inserted += 1
        else:
            merged += 1
        ent.source_batch_id = batch_id
        ent.province = row.get("province") or ent.province
        ent.city = row.get("city") or ent.city
        ent.district = row.get("district") or ent.district
        ent.address = row.get("address") or ent.address
        ent.business_scope = row.get("business_scope") or ent.business_scope
        if row.get("registered_capital_wan"):
            try:
                ent.registered_capital_wan = float(row["registered_capital_wan"])
            except ValueError:
                pass
        provided_key = (row.get("industry_key") or "").strip()
        if provided_key:
            ent.industry_key = provided_key
            for ind in downstream_industries():
                if ind["key"] == provided_key:
                    ent.industry_name = ind["name"]
                    break
        apply_icp(ent)
        session.flush()
    session.commit()
    # 检索投影：导入后全量同步（导入是批量操作，逐条增量反而慢）
    try:
        from .. import search as meili
        from ..db import SessionLocal

        with SessionLocal() as s2:
            meili.sync_all(s2)
    except Exception:  # noqa: BLE001 — 检索层失败不影响导入结果
        pass
    return {"inserted": inserted, "merged": merged, "skipped": skipped, "batch_id": batch_id}


@router.post("/import/tenders")
async def import_tenders(
    file: UploadFile,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    raw = await file.read()
    text_content = _decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text_content))
    strong = None
    from ..vertical import device_strong_terms, device_weak_terms

    strong = device_strong_terms()
    weak = device_weak_terms()
    weights = signal_weights()
    title_pts = float(weights.get("tender_title_hit", 70))
    body_pts = float(weights.get("tender_body_hit", 50))
    weak_discount = float(weights.get("weak_discount", 0.5))

    def match_signal(title: str, content: str) -> dict | None:
        t_hits = [t for t in strong if t in title]
        b_hits = [t for t in strong if t in content and t not in t_hits]
        w_hits = [t for t in weak if t in title and t not in t_hits and t not in b_hits]
        if t_hits:
            return {"terms": t_hits, "hit_part": "title", "score": title_pts}
        if w_hits:
            return {"terms": w_hits, "hit_part": "title", "score": title_pts * weak_discount}
        if b_hits:
            return {"terms": b_hits, "hit_part": "body", "score": body_pts}
        return None

    signals_created = 0
    enterprises_created = 0
    affected: set[int] = set()
    for row in reader:
        title = (row.get("title") or "").strip()
        content = (row.get("content") or "").strip()
        pub_date = (row.get("pub_date") or "").strip() or None
        url = (row.get("url") or "").strip()
        if not title:
            continue
        m = match_signal(title, content)
        if m is None:
            continue
        ent_name = (row.get("enterprise_name") or "").strip()
        if not ent_name:
            ent_name = extract_enterprise_name(title, content)
        if not ent_name:
            # 抽不到归属方：不再整条丢弃，落"待归属"企业占位（信号保留，
            # 后续可人工归属或凭企业名补录——修复最大数据漏损口）
            ent_name = f"待归属·{title[:18].strip()}"
        ent, is_new = resolve_enterprise(session, ent_name, source="tender")
        if is_new:
            enterprises_created += 1
        excerpt = content[:200] if content else title
        # 证据句（学 AI_Find_Customer evidence.claim）：标题本身即最可信的"要求买什么"
        evidence_claim = (content or title).strip()[:80]
        sig = Signal(
            enterprise_id=ent.id,
            type="tender",
            title=title,
            url=url,
            pub_date=pub_date,
            matched_terms="[]",
            hit_part=m["hit_part"],
            raw_excerpt=excerpt,
            evidence_claim=evidence_claim,
            base_score=m["score"],
        )
        import json as _json

        sig.matched_terms = _json.dumps(m["terms"], ensure_ascii=False)
        existing = (
            session.query(Signal)
            .filter(
                Signal.enterprise_id == ent.id,
                Signal.url == url,
                Signal.title == title,
            )
            .first()
        )
        if existing is None:
            session.add(sig)
            signals_created += 1
            affected.add(ent.id)
        session.flush()
    for eid in affected:
        recompute_intent(session, eid)
        ent = session.get(Enterprise, eid)
        if ent is not None:
            apply_icp(ent)
    session.commit()
    return {
        "signals_created": signals_created,
        "enterprises_created": enterprises_created,
        "matched_terms_summary": f"{signals_created} 条信号命中设备词",
    }
