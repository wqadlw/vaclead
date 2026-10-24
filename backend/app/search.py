"""Meilisearch 检索层（对标探迹筛选体验：毫秒级多面组合筛选）。

架构：SQLite 仍是唯一事实源（交易/持久化），Meili 是检索投影。
- sync_all(): 全量重建索引（企业导入/采集后调用）
- upsert_enterprise(): 单条增量同步
- search(): 多条件+分面（facet）检索
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger("vaclead.meili")

_MEILI_URL = os.environ.get("MEILI_URL", "http://127.0.0.1:7700")
_MEILI_KEY = os.environ.get("MEILI_KEY", "")
INDEX_NAME = "enterprises"

FILTERABLE = [
    "industry_key", "province", "city", "district",
    "is_competitor", "source", "status",
    "icp_score", "intent_score", "registered_capital_wan", "last_signal_date",
]
SORTABLE = ["icp_score", "intent_score", "registered_capital_wan", "updated_at"]

FACET_CONFIG = ["industry_key", "province", "city", "source"]


@lru_cache(maxsize=1)
def _client():
    import meilisearch

    # 本机服务禁走代理（http_proxy 会把 127.0.0.1 打到代理上返回 502）
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    # requests 的 trust_env 只能 monkeypatch 全局会话：直接归零代理环境变量
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    return meilisearch.Client(_MEILI_URL, _MEILI_KEY, timeout=10)


def available() -> bool:
    """Meili 不可用时所有检索 API 自动回退 SQLite（平台不因检索引擎宕机不可用）。"""
    try:
        _client().health()
        return True
    except Exception:  # noqa: BLE001
        return False


def _doc_of(e) -> dict:
    """Enterprise ORM → Meili 文档。"""
    return {
        "id": e.id,
        "name": e.name,
        "uscc": e.uscc or "",
        "province": e.province or "",
        "city": e.city or "",
        "district": e.district or "",
        "address": e.address or "",
        "industry_key": e.industry_key or "",
        "industry_name": e.industry_name or "",
        "business_scope": e.business_scope or "",
        "registered_capital_wan": e.registered_capital_wan or 0,
        "icp_score": e.icp_score,
        "intent_score": e.intent_score,
        "is_competitor": e.is_competitor,
        "status": e.status,
        "source": e.source,
        "signal_count": 0,  # 由 sync 时聚合填充
        "last_signal_date": "",  # sync 时聚合填充（供 signal_days 过滤）
        "updated_at": e.updated_at or "",
    }


def ensure_index() -> None:
    client = _client()
    try:
        idx = client.get_index(INDEX_NAME)
    except Exception:  # noqa: BLE001 — 首次创建（create 是异步任务，需等待生效）
        task = client.create_index(INDEX_NAME, {"primaryKey": "id"})
        client.wait_for_task(task.task_uid if hasattr(task, "task_uid") else task["taskUid"])
        idx = client.get_index(INDEX_NAME)
    task = idx.update_settings(
        {
            "searchableAttributes": ["name", "business_scope", "address", "industry_name"],
            "filterableAttributes": FILTERABLE,
            "sortableAttributes": SORTABLE,
            "faceting": {"maxValuesPerFacet": 100},
        }
    )
    client.wait_for_task(task.task_uid if hasattr(task, "task_uid") else task["taskUid"])


def sync_all(session) -> dict:
    """全量重建（信号计数聚合一次算好）。"""
    ensure_index()
    from sqlalchemy import func

    from .models import Enterprise, Signal

    counts: dict[int, int] = {}
    for eid, cnt in (
        session.query(Signal.enterprise_id, func.count()).group_by(Signal.enterprise_id).all()
    ):
        counts[eid] = cnt
    last_dates: dict[int, str] = {}
    for eid, d in (
        session.query(Signal.enterprise_id, func.max(Signal.pub_date))
        .group_by(Signal.enterprise_id)
        .all()
    ):
        last_dates[eid] = d or ""
    docs = []
    for e in session.query(Enterprise).all():
        d = _doc_of(e)
        d["signal_count"] = counts.get(e.id, 0)
        d["last_signal_date"] = last_dates.get(e.id, "")
        docs.append(d)
    if docs:
        _client().index(INDEX_NAME).add_documents_in_batches(docs, batch_size=500)
    return {"indexed": len(docs)}


def upsert_enterprise(session, enterprise_id: int) -> None:
    """单条增量同步（导入/评分更新后调用；失败静默，不阻断主流程）。"""
    try:
        from .models import Enterprise, Signal
        from sqlalchemy import func

        e = session.get(Enterprise, enterprise_id)
        if e is None:
            return
        d = _doc_of(e)
        d["signal_count"] = (
            session.query(func.count())
            .select_from(Signal)
            .filter(Signal.enterprise_id == e.id)
            .scalar()
            or 0
        )
        _client().index(INDEX_NAME).add_documents([d])
    except Exception as exc:  # noqa: BLE001
        logger.warning("meili upsert failed eid=%s: %s", enterprise_id, exc)


def delete_enterprise(enterprise_id: int) -> None:
    try:
        _client().index(INDEX_NAME).delete_document(enterprise_id)
    except Exception:  # noqa: BLE001
        pass


def search(
    q: str = "",
    *,
    industry_key: str | None = None,
    province: str | None = None,
    city: str | None = None,
    capital_min: float | None = None,
    capital_max: float | None = None,
    score_min: float | None = None,
    signal_days: int | None = None,
    is_competitor: int = -1,
    page: int = 1,
    page_size: int = 20,
    facets: bool = False,
) -> dict:
    """多条件组合检索 + 可选分面统计（对标探迹筛选器：每个条件可看"选中后还剩多少"）。"""
    filters: list[str] = []
    if industry_key:
        filters.append(f'industry_key = "{industry_key}"')
    if province:
        filters.append(f'province = "{province}"')
    if city:
        filters.append(f'city = "{city}"')
    if capital_min is not None:
        filters.append(f"registered_capital_wan >= {capital_min}")
    if capital_max is not None:
        filters.append(f"registered_capital_wan <= {capital_max}")
    if score_min is not None:
        filters.append(f"icp_score >= {score_min}")
    if is_competitor in (0, 1):
        filters.append(f"is_competitor = {is_competitor}")
    if signal_days:
        import datetime as _dt

        cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=signal_days)).strftime(
            "%Y-%m-%d"
        )
        filters.append(f'last_signal_date >= "{cutoff}"')

    opts: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "showRankingScore": False,
    }
    if filters:
        opts["filter"] = " AND ".join(filters)
    if facets:
        opts["facets"] = FACET_CONFIG
    if signal_days:
        # last_signal_date 排序无意义，默认按意图分
        opts["sort"] = ["intent_score:desc"]
    else:
        opts["sort"] = ["icp_score:desc"]

    r = _client().index(INDEX_NAME).search(q or "", opts)
    items = r.get("hits", [])
    facet_counts = r.get("facetDistribution", {}) if facets else None
    return {
        "items": items,
        "total": r.get("estimatedTotalHits", 0),
        "page": page,
        "page_size": page_size,
        "facets": facet_counts,
    }
