"""山东政采分网直采采集器（ccgp-shandong.gov.cn，SPEC-SDGP）。

昊志本省（山东）直采：SPA bundle 逆推 + 网络捕获破译，契约实弹全通。
价值=①本省更快更全 ②ccgp 主源被冷却时的冗余备份。语义=政采（同 ccgp）。

契约（docs/SPEC-SDGP.md，实弹验证直接照抄）：

  POST https://www.ccgp-shandong.gov.cn:8087/api/website/site/getListByCode
  body = {"colCode": "0301", "area": "370000", "currentPage": 1,
          "pageSize": 20, "homePage": 0, "mergeType": 0, "title": "真空"}

- title=服务端关键词过滤，无验证码（搜索路径 searchAllByCode 有 captcha
  门——不用它）；参数严格：homePage 必填 0，乱加参数会 999/None。
- 栏目：0301=采购公告 / 0302=结果公告（0305 更正、0306 终止=噪音不采、
  2502 合同备用）。记录字段：date/title/areaName/areaCode/id（加密串）/
  buyKindCode/projectType——无中标方名称（详情接口 getNoticeBody 未破解，
  v1 不做详情）。
- httpx 必须 trust_env=False（shell 代理毒死直连的坑）；限速 1.5s +
  resilience get_health("sdgp") 域级熔断；502/拦截页 → mark_bad。
"""
from __future__ import annotations

import base64
import logging
import re
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger("vaclead.collectors.sdgp")

SDGP_URL = "https://www.ccgp-shandong.gov.cn:8087/api/website/site/getListByCode"
AREA_CODE = "370000"       # 山东全省
COL_TENDER = "0301"        # 采购公告
COL_RESULT = "0302"        # 结果公告
REQUEST_INTERVAL_S = 1.5   # 礼貌间隔（ggzy 同款）
MAX_RETRIES = 2
PAGE_SIZE = 20
_UA = "vaclead-acquisition/1.0 (+research; contact: local self-hosted)"

_last_request_at = 0.0


def _empty() -> dict:
    """空结果结构（每次新建，防共享 records 列表被调用方意外污染）。"""
    return {"records": [], "total": 0, "pages": 0}


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)


def _post(body: dict, client: httpx.Client | None = None) -> dict:
    """带限速、健康度与重试的 POST（ggzy._get 同款积分制）。

    client 参数供测试注入 MockTransport；生产路径 trust_env=False 新建连接
    （低频场景足够）。冷却中（is_blocked）直接短路返回空结构。
    """
    import os

    from .resilience import get_health

    # trust_env=False 之外再剥代理环境变量（双保险，ggzy 惯例）
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "ccgp-shandong.gov.cn,www.ccgp-shandong.gov.cn")

    health = get_health("sdgp")
    if health.is_blocked():
        return _empty()

    global _last_request_at
    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        try:
            headers = {"User-Agent": _UA, "Content-Type": "application/json"}
            if client is not None:
                resp = client.post(SDGP_URL, json=body, headers=headers)
            else:
                with httpx.Client(
                    timeout=httpx.Timeout(20.0), trust_env=False
                ) as own:
                    resp = own.post(SDGP_URL, json=body, headers=headers)
            _last_request_at = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream {resp.status_code}", request=resp.request, response=resp
                )
            if 400 <= resp.status_code < 500:
                # 实测拦截形态：blackIp 黑名单以 HTTP 400 + JSON 999 返回——
                # 请求本身已定型，重试无意义还添堵；mark_bad 一次即返回
                # （连续 4xx 由积分制自动升级为域级冷却）。
                logger.error(
                    "sdgp 上游拒绝 HTTP %s（不重试）：%s",
                    resp.status_code, resp.text[:120],
                )
                health.mark_bad()
                return _empty()
            resp.raise_for_status()
            data = resp.json()
            _last_request_at = time.monotonic()
            # 业务失败：success=false 或 code 非 200（乱加参数实测 999/None）。
            # 不 mark_bad——传输层是通的（ggzy 同款）；真正的拦截（黑名单等）
            # 走非 2xx 由 except 路径 mark_bad 熔断。
            code = data.get("code")
            if data.get("success") is False or (
                code is not None and str(code) != "200"
            ):
                logger.warning(
                    "sdgp 业务失败 col=%s code=%s msg=%s",
                    body.get("colCode"), code, data.get("msg") or data.get("message"),
                )
                return _empty()
            health.mark_good()
            return data
        except (httpx.HTTPError, ValueError) as exc:  # 网络/JSON 解析
            _last_request_at = time.monotonic()
            health.mark_bad()
            if health.is_blocked():
                logger.error("sdgp 进入冷却，终止本批 col=%s", body.get("colCode"))
                return _empty()
            if attempt < MAX_RETRIES:
                backoff = REQUEST_INTERVAL_S * (2 ** (attempt + 1))
                logger.warning("sdgp 请求失败(%s)，%.0fs 后重试", exc, backoff)
                time.sleep(backoff)
            else:
                logger.error("sdgp 请求最终失败 col=%s: %s", body.get("colCode"), exc)
                return _empty()
    return _empty()  # pragma: no cover


# ---------------------------------------------------------------------------
# 接口封装
# ---------------------------------------------------------------------------
def fetch_notices(
    col_code: str,
    keyword: str,
    page: int = 1,
    page_size: int = PAGE_SIZE,
    client: httpx.Client | None = None,
) -> dict:
    """按栏目+关键词拉一页公告。返回 result.data（records/total/pages）。

    响应形态：result.data 下 records/total/pages；兼容直接在 result 下的
    变体（ggzy.list_bids 同款容错）。失败/冷却/业务失败一律空结构。
    """
    body = {
        "colCode": col_code,
        "area": AREA_CODE,
        "currentPage": max(1, page),
        "pageSize": max(1, page_size),
        "homePage": 0,   # 实测必填 0，乱加/缺省参数会 999/None
        "mergeType": 0,
        "title": keyword,
    }
    data = _post(body, client)
    # 响应包裹形态三兼容（2026-11-15 生产首采 0 产出定位：实站=data.data，
    # Mock/测试=result.data，ggzy 惯例=result 直挂——三层全接防再断）。
    inner = data.get("data")
    if isinstance(inner, dict):
        payload = inner.get("data")
        if isinstance(payload, dict) and "records" in payload:
            return payload
        if "records" in inner:
            return inner
    result = data.get("result") or {}
    if isinstance(result, dict):
        if "records" in result:
            return result
        inner2 = result.get("data")
        if isinstance(inner2, dict) and "records" in inner2:
            return inner2
    return _empty()


def search_all(
    col_code: str,
    keyword: str,
    max_pages: int = 3,
    client: httpx.Client | None = None,
) -> Iterator[dict]:
    """分页迭代某栏目关键词全部公告（max_pages 保险，防跑飞；空页即停）。"""
    first = fetch_notices(col_code, keyword, page=1, client=client)
    yield from first.get("records") or []
    try:
        pages = int(first.get("pages") or 0)
    except (TypeError, ValueError):
        pages = 0
    pages = min(pages, max(1, max_pages))
    for p in range(2, pages + 1):
        result = fetch_notices(col_code, keyword, page=p, client=client)
        records = result.get("records") or []
        if not records:
            break
        yield from records


# ---------------------------------------------------------------------------
# 详情正文（2026-11-15 破译：GET /website/site/getDetail?id=&colCode=）
# 浏览器捕获实锤；body 为 base64 HTML。同 session 新鲜 id 秒调成功——
# 旧 id 过期即 999（列表页拿 id 后必须立刻调）。
# ---------------------------------------------------------------------------
DETAIL_URL = "https://www.ccgp-shandong.gov.cn:8087/api/website/site/getDetail"


def fetch_notice_detail(
    col_code: str,
    notice_id: str,
    client: httpx.Client | None = None,
) -> dict | None:
    """拉公告详情正文：{title, user_name(代理机构), date, body_text}。

    body 为 base64 HTML——解码后剥标签压空白；失败/过期(999)/冷却一律 None。
    必须用同 session 新鲜 id（过期 id 实测 999）——调用方从 fetch_notices
    拿到 id 后立即调，且传同一 client 保 cookie 连续。"""
    from .resilience import get_health

    health = get_health("sdgp")
    if health.is_blocked():
        return None
    try:
        headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        params = {"id": notice_id, "colCode": col_code}
        if client is not None:
            resp = client.get(DETAIL_URL, params=params, headers=headers)
        else:
            with httpx.Client(timeout=httpx.Timeout(20.0), trust_env=False) as own:
                resp = own.get(DETAIL_URL, params=params, headers=headers)
        _last_request_at = time.monotonic()
        if resp.status_code != 200:
            return None
        inner = (resp.json().get("data") or {})
        if inner.get("code") != 100:
            return None
        payload = inner.get("data") or {}
        body_b64 = payload.get("body") or ""
        try:
            html = base64.b64decode(body_b64).decode("utf-8", "ignore")
        except (ValueError, UnicodeDecodeError):
            html = ""
        body_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        return {
            "title": (payload.get("title") or "").strip(),
            "user_name": (payload.get("userName") or "").strip() or None,
            "date": (payload.get("date") or "").strip()[:10] or None,
            "body_text": body_text,
        }
    except (httpx.HTTPError, ValueError):
        health.mark_bad()
        return None


# ---------------------------------------------------------------------------
# 字段归一
# ---------------------------------------------------------------------------
def normalize_notice(raw: dict) -> dict | None:
    """栏目记录 → {title, date, area_name, notice_id}；无标题丢弃。

    date 实测 "2026-11-05 20:22:08"，截到日期（Signal.pub_date 口径）。
    详情正文经 fetch_notice_detail 另取（2026-11-15 破译 getDetail）。
    """
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "date": (raw.get("date") or "").strip()[:10] or None,
        "area_name": (raw.get("areaName") or "").strip() or None,
        "notice_id": (raw.get("id") or "").strip() or None,
    }
