"""官方中标数据采集器：全国公共资源交易平台数据服务（data.ggzy.gov.cn）。

国家信息中心主办的官方结构化数据接口，实测（2026-10-25）无需鉴权、
无验证码、无 WAF。契约与合规边界见 docs/DATA_ACQUISITION.md：

  GET /yjcx/index/search?keyword=&pageNo=&pageSize=   → 主体（uniscid/entname/法人/成立/中标数）
  GET /yjcx/index/bid_list?uniscid=&pageNo=&pageSize= → 中标明细（项目/成交价/地区/类型/采购人）

合规守则：固定 UA 标识、限速 ≥1.5s/请求、指数退避、不绕任何防护。
一条中标记录双向入库：中标方（多为同行 → is_competitor 打标）与
采购人（真买家 → 一线线索）；记录本体落 signal(type=tender_win)。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger("vaclead.collectors.ggzy")

BASE_URL = "https://data.ggzy.gov.cn"
SEARCH_PATH = "/yjcx/index/search"     # ?keyword=&pageNo=&pageSize=
BID_LIST_PATH = "/yjcx/index/bid_list"  # ?uniscid=&pageNo=&pageSize=
_UA = "vaclead-acquisition/1.0 (+research; contact: local self-hosted)"
REQUEST_INTERVAL_S = 1.5
MAX_RETRIES = 2
PAGE_SIZE_CAP = 200  # 实测 pageSize=200 可用

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)


def _get(path: str, params: dict, client: httpx.Client | None = None) -> dict:
    """带限速、健康度与重试的 GET（吸收 crawlee Session 错误积分制）。

    client 参数供测试注入 MockTransport；生产路径每次新建连接（低频场景足够）。
    冷却中（is_blocked）直接短路返回空结构，不再打请求。
    """
    import os

    from .resilience import get_health

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "data.ggzy.gov.cn")

    health = get_health("ggzy")
    if health.is_blocked():
        return {"result": {"records": [], "total": 0, "pages": 0}}

    global _last_request_at
    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        try:
            if client is not None:
                resp = client.get(
                    BASE_URL + path, params=params, headers={"User-Agent": _UA}
                )
            else:
                with httpx.Client(timeout=httpx.Timeout(20.0)) as own:
                    resp = own.get(
                        BASE_URL + path, params=params, headers={"User-Agent": _UA}
                    )
            _last_request_at = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") is False:
                logger.warning("ggzy 业务失败 %s: %s", path, data.get("message"))
                return {"result": {"records": [], "total": 0, "pages": 0}}
            health.mark_good()
            return data
        except (httpx.HTTPError, ValueError) as exc:  # 网络/JSON 解析
            _last_request_at = time.monotonic()
            health.mark_bad()
            if health.is_blocked():
                logger.error("ggzy 进入冷却，终止本批 %s", path)
                return {"result": {"records": [], "total": 0, "pages": 0}}
            if attempt < MAX_RETRIES:
                backoff = REQUEST_INTERVAL_S * (2 ** (attempt + 1))
                logger.warning("ggzy 请求失败(%s)，%.0fs 后重试", exc, backoff)
                time.sleep(backoff)
            else:
                logger.error("ggzy 请求最终失败 %s: %s", path, exc)
                return {"result": {"records": [], "total": 0, "pages": 0}}
    return {"result": {"records": [], "total": 0, "pages": 0}}  # pragma: no cover


# ---------------------------------------------------------------------------
# 接口封装
# ---------------------------------------------------------------------------
def search_entities(
    keyword: str, page: int = 1, size: int = 50, client: httpx.Client | None = None
) -> dict:
    """关键词搜公共资源交易市场主体。返回 result dict（records/total/pages）。"""
    data = _get(SEARCH_PATH, {
        "keyword": keyword,
        "pageNo": max(1, page),
        "pageSize": min(max(1, size), PAGE_SIZE_CAP),
    }, client)
    return data.get("result") or {"records": [], "total": 0, "pages": 0}


def search_all_entities(
    keyword: str,
    max_pages: int = 20,
    size: int = 200,
    client: httpx.Client | None = None,
) -> Iterator[dict]:
    """分页迭代某关键词全部主体（带 max_pages 保险，防跑飞）。"""
    first = search_entities(keyword, page=1, size=size, client=client)
    yield from first.get("records") or []
    pages = min(int(first.get("pages") or 0), max_pages)
    for p in range(2, pages + 1):
        result = search_entities(keyword, page=p, size=size, client=client)
        records = result.get("records") or []
        if not records:
            break
        yield from records


def list_bids(
    uniscid: str, page: int = 1, size: int = 50, client: httpx.Client | None = None
) -> dict:
    """按统一社会信用代码拉中标明细。

    实测响应 records 在 result.data 下；兼容直接在 result 下的形态。
    """
    if not (uniscid or "").strip():
        return {"records": [], "total": 0, "pages": 0}
    data = _get(BID_LIST_PATH, {
        "uniscid": uniscid.strip(),
        "pageNo": max(1, page),
        "pageSize": min(max(1, size), PAGE_SIZE_CAP),
    }, client)
    result = data.get("result") or {}
    inner = result.get("data")
    if isinstance(inner, dict):
        return inner
    return result or {"records": [], "total": 0, "pages": 0}


# ---------------------------------------------------------------------------
# 字段归一
# ---------------------------------------------------------------------------
def normalize_bid_record(raw: dict) -> dict | None:
    """bid_list 记录 → {project_name, bid_price_wan, area, trade_type,
    tender_org, tender_agency, pub_date}；无项目名的丢弃。"""
    project = (raw.get("projectName") or "").strip()
    if not project:
        return None
    price = raw.get("bidPrice")
    try:
        price_wan = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price_wan = None
    if price_wan is not None and price_wan <= 0:
        price_wan = None  # 官方源常见 0/空占位，按未知处理避免"成交价0万元"
    return {
        "project_name": project,
        "bid_price_wan": price_wan,
        "area": (raw.get("areaCode") or "").strip() or None,
        "trade_type": (raw.get("tos") or "").strip() or None,
        "tender_org": (raw.get("tenderOrgName") or "").strip() or None,
        "tender_agency": (raw.get("tenderAgencyName") or "").strip() or None,
        "pub_date": (raw.get("createTime") or "").strip()[:10] or None,
        "deal_id": (raw.get("id") or "").strip() or None,
    }


def normalize_entity_record(raw: dict) -> dict | None:
    """search 记录 → {uscc, name, legal_rep, est_date, bid_count}；无企业名丢弃。"""
    name = (raw.get("entname") or "").strip()
    if not name:
        return None
    est = raw.get("estdate")
    est_date = None
    if est:
        try:
            # 实测 estdate 是 epoch 毫秒的浮点字符串（如 1.5092928E9 → 秒）
            import datetime as _dt

            est_num = float(est)
            if est_num > 1e12:
                est_num /= 1000.0
            est_date = _dt.datetime.fromtimestamp(est_num).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            est_date = None
    try:
        bid_count = int(raw.get("bidCount") or 0)
    except (TypeError, ValueError):
        bid_count = 0
    return {
        "uscc": (raw.get("uniscid") or "").strip() or None,
        "name": name,
        "legal_rep": (raw.get("name") or "").strip() or None,
        "est_date": est_date,
        "bid_count": bid_count,
    }
