"""品牌装机抽取器（SPEC-BIM B3）：公告语料 → brand_install 幂等抽取。

三层改道的"装机预备"层（SPEC-BIM 头注）：现有语料品牌词命中稀薄（5,677 条
信号命中个位数），故抽取器定位为"词表入配置+幂等任务+增量钩子"——语料升级
后自动增产，当前以"竞对→买家"抢单链为主链数据。

- 品牌词命中：brand_terms 精确子串（词长降序，长词优先，命中区间不重叠）；
  自提及守卫——品牌词是企业名子串时跳过（竞对自家公告提自家名不记装机）。
- 型号抽取：品牌锚定 ±20 字符窗口 + [A-Z]{1,4}[- ]?\\d{2,4}[A-Z]? token。
  摸底教训（docs/SPEC-BIM 头注）：全库型号正则 1,006 次命中实测全是项目
  编号——三重防御拒收：① token 后紧贴数字（"HJ-20231106" 截断形态）；
  ② 连字链段（"2023-SN-001" 的 SN 段 / "SN-001-2" 的首段是编号链中段）；
  ③ 纯数字 token 正则本身不收（必须含字母）。
- 竞对→买家链（主链数据）：is_competitor 企业的 tender_win evidence_claim
  抽采购人（_BUYER_RE 与 competitors.py 同源字面量，复制防路由模块依赖链）
  → resolve_enterprise 归一（source 不变只关联；新建落 ggzy_buyer 帧同
  ggzy-harvest S2 语义）→ 买家非竞对 → brand_install 记 brand=竞对公司名
  （model=""），"北方华创→中色东方"式抢单关系直接进装机地图。
- upsert 幂等：unique(enterprise_id, brand, model) 命中即 first_seen=min /
  last_seen=max / signal_id、evidence 取最新信号——全量重跑零新增。
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .models import BrandInstall, Enterprise, Signal
from .normalize import resolve_enterprise
from .vertical import brand_terms

# 与 competitors.py / intel.py 同源：公告文本回抽（可溯源，不发明数据）。
# 正则字面量复制而非 import——competitors 模块级拉入路由+jobs 依赖链，B3
# 抽取器要保持零路由耦合（tasks.py/jobs.py 都会在无 FastAPI 上下文调用）。
_BUYER_RE = re.compile(r"采购人[：:]\s*([^\s，,。；;]+)")
# 型号 token：字母段 1-4 位 + 可选连字/空格 + 2-4 位数字 + 可选尾字母
_MODEL_RE = re.compile(r"[A-Z]{1,4}[- ]?\d{2,4}[A-Z]?")
_MODEL_WINDOW = 20  # 型号必须紧邻品牌词 ±20 字符（SPEC-BIM B3）
_EVIDENCE_CAP = 200


def _model_near(text: str, bstart: int, bend: int) -> str:
    """品牌锚定 ±20 字符窗口内抽型号 token；项目编号形态拒收（无命中回空）。"""
    lo = max(0, bstart - _MODEL_WINDOW)
    hi = min(len(text), bend + _MODEL_WINDOW)
    window = text[lo:hi]
    for m in _MODEL_RE.finditer(window):
        post = window[m.end(): m.end() + 1]
        if post.isdigit() or post == "-":
            continue  # 数字截断（HJ-20231106）/ 连字链后段（SN-001-2）
        pre = window[m.start() - 1: m.start()] if m.start() >= 1 else ""
        pre2 = window[m.start() - 2: m.start() - 1] if m.start() >= 2 else ""
        if pre == "-" and pre2.isdigit():
            continue  # 连字链中段（2023-SN-001 的 SN 段）
        return m.group()
    return ""


def _scan_signal(sig: Signal, terms: list[str]) -> list[tuple[str, str]]:
    """单信号三字段拼接文本的品牌命中 → [(brand, model)]（同对去重保序）。"""
    text = " ".join(
        t for t in (sig.title or "", sig.raw_excerpt or "", sig.evidence_claim or "") if t
    )
    hits: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    spans: list[tuple[int, int]] = []
    for term in terms:  # 词长降序——长词优先且命中区间不重叠
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            span = (idx, idx + len(term))
            start = span[1]
            if any(idx < e and s < span[1] for s, e in spans):
                continue  # 与已命中长词区间重叠（"汉钟精机"吃掉"汉钟"）
            spans.append(span)
            model = _model_near(text, idx, span[1])
            pair = (term, model)
            if pair not in seen:
                seen.add(pair)
                hits.append(pair)
    return hits


def _sig_seen(sig: Signal) -> str:
    """装机证据归属日（pub_date 优先 created_at 兜底，截到日——字典序可比）。"""
    return (sig.pub_date or sig.created_at or "")[:10]


def _upsert_install(
    session: Session,
    enterprise_id: int,
    brand: str,
    model: str,
    signal_id: int | None,
    evidence: str,
    seen: str,
) -> int:
    """幂等 upsert（唯一键 enterprise_id+brand+model）：命中即 first_seen=min /
    last_seen=max / signal_id、evidence 取最新信号。返回新增行数（0/1）。"""
    row = (
        session.query(BrandInstall)
        .filter(
            BrandInstall.enterprise_id == enterprise_id,
            BrandInstall.brand == brand,
            BrandInstall.model == model,
        )
        .first()
    )
    if row is None:
        session.add(BrandInstall(
            enterprise_id=enterprise_id,
            brand=brand,
            model=model,
            signal_id=signal_id,
            evidence=(evidence or "")[:_EVIDENCE_CAP],
            first_seen=seen,
            last_seen=seen,
        ))
        # autoflush=False（SessionLocal 全局）：新行立即可见——同一企业多条
        # 信号命中同 (brand, model) 对时，下一次 upsert 查询必须能看到 pending
        # 行，否则重复 add 撞 uq_brand_install（真库实弹 2026-11-15 实锤）
        session.flush()
        return 1
    if seen:
        row.first_seen = min(row.first_seen or seen, seen)
        row.last_seen = max(row.last_seen or seen, seen)
    if signal_id is not None and (row.signal_id is None or signal_id > row.signal_id):
        row.signal_id = signal_id
        row.evidence = (evidence or "")[:_EVIDENCE_CAP]
    return 0


def extract_brand_for(session: Session, enterprise_id: int) -> int:
    """单企业品牌装机抽取（幂等）→ 本次新增 brand_install 行数。

    词表命中行落该企业自身；若该企业是竞对，其 tender_win 的采购人侧同步
    落抢单链行（brand=竞对公司名，model=""）——增量钩子（ggzy-harvest affected
    循环尾 / daily_incremental 尾）与全量任务共用本入口。"""
    ent = session.get(Enterprise, enterprise_id)
    if ent is None or ent.deleted_at is not None:
        return 0
    terms = sorted(brand_terms(), key=len, reverse=True)
    # 自提及守卫：品牌词是企业名子串（竞对自家名/品牌商本身）不记装机
    terms = [t for t in terms if t not in (ent.name or "")]
    signals = (
        session.query(Signal)
        .filter(Signal.enterprise_id == enterprise_id)
        .order_by(Signal.id.asc())  # 升序处理：新信号 id 大后覆盖（取最新语义）
        .all()
    )
    created = 0
    for sig in signals:
        seen = _sig_seen(sig)
        evidence = (sig.evidence_claim or sig.raw_excerpt or sig.title or "")
        for brand, model in _scan_signal(sig, terms):
            created += _upsert_install(
                session, enterprise_id, brand, model, sig.id, evidence, seen
            )
    # 竞对→买家链（主链数据）：中标方是竞对且买家非竞对 → brand=竞对名落买家
    if ent.is_competitor == 1:
        for sig in signals:
            if sig.type != "tender_win":
                continue
            m = _BUYER_RE.search(sig.evidence_claim or "")
            if not m:
                continue
            buyer_name = m.group(1).strip()
            if len(buyer_name) < 4 or buyer_name in (ent.name or ""):
                continue
            buyer_ent, _is_new = resolve_enterprise(
                session, buyer_name, source="ggzy_buyer"
            )
            if buyer_ent.id == ent.id or buyer_ent.is_competitor == 1:
                continue  # 买家非竞对（SPEC-BIM B3）
            created += _upsert_install(
                session,
                buyer_ent.id,
                ent.name or "",  # brand=竞对公司名
                "",
                sig.id,
                sig.evidence_claim or "",
                _sig_seen(sig),
            )
    session.commit()
    return created


def extract_all(session: Session, limit: int | None = None) -> dict:
    """全量抽取（5,677 行信号秒级）：按企业去重调 extract_brand_for。

    totals：signals=全库信号数；enterprises=本次处理企业数；brands_found=
    brand_install 全表去重品牌数（幂等稳定）；installs=**本轮新增行数**
    （SPEC-MULTI new 语义对齐——全量重跑零新增=0，首跑=落表行数）。"""
    ent_ids = [
        rid for (rid,) in session.query(Enterprise.id).order_by(Enterprise.id).all()
    ]
    if limit is not None and limit > 0:
        ent_ids = ent_ids[:limit]
    signal_total = session.query(Signal.id).count()
    new_rows = 0
    for eid in ent_ids:
        new_rows += extract_brand_for(session, int(eid))
    brands_found = (
        session.query(BrandInstall.brand).distinct().count()
    )
    return {
        "signals": signal_total,
        "enterprises": len(ent_ids),
        "brands_found": brands_found,
        "installs": new_rows,
    }
