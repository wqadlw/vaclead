"""企业名归一与实体解析引擎（契约：docs/SPEC.md §1.3 / §3.2 entity_resolver）。

解析流程（resolve_enterprise）：
  1. 精确名匹配（Enterprise.name == 原全名）
  2. 别名表匹配（EnterpriseAlias，先原全名后标准化主体名）
  3. rapidfuzz token_sort_ratio 对现有企业主体名（canonical_name）比对 ≥90 视为同企业，
     合并时把变体写入 EnterpriseAlias
  4. 找不到则创建新 Enterprise（name=原全名, source=source）

本模块只依赖 models，不 import db（不触发全局 ENGINE / 建库），可用内存库独立测试。
"""
from __future__ import annotations

import inspect
import json
import re
import time
from importlib import import_module
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from .models import Enterprise, EnterpriseAlias

#: rapidfuzz token_sort_ratio 视为同企业的阈值（docs/SPEC.md §3.2）
FUZZY_THRESHOLD = 90.0

# 全角/半角括号段（如 "(苏州)" / "（苏州）"）
_BRACKET_RE = re.compile(r"[（(][^()（）]*[）)]")
# 常见企业后缀，按长度降序尝试循环剥离
_SUFFIXES: tuple[str, ...] = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团",
    "公司",
)
_WS_RE = re.compile(r"\s+")


def canonical_name(name: str) -> str:
    """清洗企业名，返回标准化主体名。

    - 去掉全角/半角括号及其中的后缀内容（"沃尔真空(苏州)有限公司" → "沃尔真空"）
    - 去掉常见后缀词（有限公司/有限责任公司/股份有限公司/集团/公司，可叠加剥离）
    - 去掉所有空白字符
    """
    s = _WS_RE.sub("", (name or "").strip())
    s = _BRACKET_RE.sub("", s)
    for ch in "()（）":
        s = s.replace(ch, "")
    out = s
    while True:
        for suf in _SUFFIXES:
            if out.endswith(suf) and len(out) > len(suf):
                out = out[: -len(suf)]
                break
        else:
            break
    return out or s


def _add_alias(session: Session, ent: Enterprise, alias: str, source: str) -> None:
    """写入别名（幂等：跳过与主名相同、已入库或已 pending 的别名）。"""
    alias = _WS_RE.sub("", (alias or "").strip())
    if not alias or alias == ent.name:
        return
    exists = (
        session.query(EnterpriseAlias)
        .filter(EnterpriseAlias.enterprise_id == ent.id, EnterpriseAlias.alias == alias)
        .first()
    )
    if exists is not None:
        return
    for obj in session.new:
        if (
            isinstance(obj, EnterpriseAlias)
            and obj.enterprise_id == ent.id
            and obj.alias == alias
        ):
            return
    session.add(EnterpriseAlias(enterprise_id=ent.id, alias=alias, source=source))
    session.flush()


def _resolve(
    session: Session, name: str, source: str
) -> tuple[Enterprise, bool, float]:
    """返回 (enterprise, is_new, confidence)。confidence 为匹配置信度，新建时 0.0。"""
    raw = _WS_RE.sub("", (name or "").strip())
    if not raw:
        raise ValueError("企业名不能为空")
    canon = canonical_name(raw)

    # 1) 精确名匹配
    ent = session.query(Enterprise).filter(Enterprise.name == raw).first()
    if ent is not None:
        return ent, False, 1.0

    # 2) 别名表匹配（先原全名，后标准化主体名）
    for alias_val in dict.fromkeys((raw, canon)):  # 去重且保序
        if not alias_val:
            continue
        row = (
            session.query(EnterpriseAlias)
            .filter(EnterpriseAlias.alias == alias_val)
            .first()
        )
        if row is not None:
            target = session.get(Enterprise, row.enterprise_id)
            if target is not None:
                _add_alias(session, target, raw, source)
                return target, False, 0.95

    # 3) rapidfuzz 近似合并（对现有企业主体名比对）
    if canon:
        best_ent: Enterprise | None = None
        best_score = 0.0
        for exist in session.query(Enterprise).all():
            exist_canon = canonical_name(exist.name)
            if not exist_canon:
                continue
            score = float(fuzz.token_sort_ratio(canon, exist_canon))
            if score > best_score:
                best_ent, best_score = exist, score
        if best_ent is not None and best_score >= FUZZY_THRESHOLD:
            _add_alias(session, best_ent, raw, source)
            if canon != raw:
                _add_alias(session, best_ent, canon, "auto")
            return best_ent, False, round(best_score / 100.0, 4)

    # 4) 新建（name=原全名, source=source），并登记标准化主体名别名便于后续命中
    ent = Enterprise(name=raw, source=source)
    session.add(ent)
    session.flush()
    if canon and canon != raw:
        _add_alias(session, ent, canon, "auto")
    return ent, True, 0.0


def resolve_enterprise(
    session: Session, name: str, source: str = "csv"
) -> tuple[Enterprise, bool]:
    """把外部企业名解析到库内唯一 Enterprise，返回 (enterprise, is_new)。"""
    ent, is_new, _conf = _resolve(session, name, source)
    return ent, is_new


def _try_record_run(
    session: Session, input_data: dict, output_data: dict, duration_ms: int
) -> None:
    """尝试调用 app.agents.runs.record_run 记录运行日志。

    agents.runs 由 Agent 框架方提供，可能尚未存在或签名不同——任何失败都静默跳过，
    保证 normalize 引擎独立可测。
    """
    try:
        mod = import_module(".agents.runs", __package__ or "app")
        record_run = getattr(mod, "record_run")
        candidates: dict[str, Any] = {
            "session": session,
            "agent_name": "entity_resolver",
            "backend": "rule",
            "input_data": input_data,
            "input_json": json.dumps(input_data, ensure_ascii=False),
            "output_data": output_data,
            "output_json": json.dumps(output_data, ensure_ascii=False),
            "decision_summary": (
                f"{'新建' if output_data.get('is_new') else '合并'} 企业 #"
                f"{output_data.get('enterprise_id')} "
                f"(confidence={output_data.get('confidence')})"
            ),
            "duration_ms": duration_ms,
        }
        sig = inspect.signature(record_run)
        record_run(**{k: v for k, v in candidates.items() if k in sig.parameters})
    except Exception:  # noqa: BLE001 — 独立可测要求：记录失败不影响解析结果
        pass


def entity_resolver_agent(data: dict, session: Session) -> dict:
    """entity_resolver Agent 包装（docs/SPEC.md §3.2）。

    输入 {name, context?, source?} → 输出 {"enterprise_id", "is_new", "confidence"}。
    confidence 语义：1.0=精确名命中；0.95=别名命中；fuzz 比值/100=近似合并；0.0=新建。
    """
    t0 = time.perf_counter()
    payload = data or {}
    name = payload.get("name") or ""
    source = payload.get("source") or "csv"
    ent, is_new, confidence = _resolve(session, name, source)
    result: dict[str, Any] = {
        "enterprise_id": ent.id,
        "is_new": is_new,
        "confidence": round(confidence, 4),
    }
    duration_ms = int((time.perf_counter() - t0) * 1000)
    _try_record_run(session, {"name": name, "source": source}, result, duration_ms)
    return result
