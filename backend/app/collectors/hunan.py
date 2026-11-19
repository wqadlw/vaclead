"""湖南政采采集器（ccgp-hunan.gov.cn，SPEC-HUNAN，luban 平台）。

湖南 = luban 平台（湘皖桂吉四省同款组件），接入模式与山东不同：
**无关键词接口——每轮拉当日最新块 → 本地 sdgp_terms 词表过滤 →
命中拉详情全文 → 电话入联系人**。

契约（docs/SPEC-HUNAN.md，2026-11-19 Playwright + httpx 实弹固化，
scripts/hunan_list_cap.mjs 可复跑）：

  POST http://www.ccgp-hunan.gov.cn/portal/searchHome   # 无鉴权
  body = {"code": "160-396330", "subCodes": ["160-945988"],
          "districtCode": "430000", "pageSize": 10,
          "needTotal": False, "needNewCnt": False, "isProvince": True}
  → {success, result: {data: {children: [{title, articleId,
     pubDate(epoch ms), publishDateString, districtName, ...}]}}}

  GET http://www.ccgp-hunan.gov.cn/portal/detail
      ?articleId={urlencode}&parentId=622785&timestamp={ms}   # 无鉴权
  → {success, result: {data: {title, content: "<HTML 正文>", ...}}}
  （正文实测在 result.data.content —— HTML 直出，剥标签压空白）

- articleId 含 ``/`` ``+`` ``=``——GET 参数必须 quote(safe="") 全编码
  （实弹：编码后 200 正常 JSON；裸拼会被 ``+`` 拆成空格 / ``/`` 断路径）。
- httpx 必须 trust_env=False（shell 代理毒死直连的坑，sdgp 同款）+ 剥代理
  环境变量双保险；限速 1.5s + resilience get_health("hunan") 域级熔断。
- 旧 articleId 过期机制未实测（响应有 expireFlag/expiredAt 字段）——
  detail 失败一律 None 不重试。
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger("vaclead.collectors.hunan")

HUNAN_URL = "http://www.ccgp-hunan.gov.cn"
SEARCH_HOME = "/portal/searchHome"   # POST 无鉴权（luban 列表组件）
DETAIL_URL = "/portal/detail"        # GET 无鉴权
DISTRICT_CODE = "430000"             # 湖南全省
CATEGORY_CODE = "160-396330"         # 项目信息
SUB_CODES = ["160-945988"]           # 招标公告子栏（其余子码下一轮验证）
PARENT_ID = "622785"                 # 详情/溯源链接 parentId（实弹固定值）
REQUEST_INTERVAL_S = 1.5             # 礼貌间隔（sdgp/ggzy 同款）
MAX_RETRIES = 2
_UA = "vaclead-acquisition/1.0 (+research; contact: local self-hosted)"

_CN_TZ = timezone(timedelta(hours=8))  # publishDateString 为北京时间口径

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)


def _ms_to_date(ms) -> str | None:
    """epoch 毫秒 → 北京时间日期串（publishDateString 缺失时兜底）。"""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=_CN_TZ).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _post_home(body: dict, client: httpx.Client | None = None) -> dict:
    """带限速、健康度与重试的 searchHome POST（sdgp._post 同款积分制）。

    client 参数供测试注入 MockTransport；生产路径 trust_env=False 新建
    连接。冷却中（is_blocked）直接短路返回 {}；业务失败（success!=True）
    返回 {} 不 mark_bad——传输层是通的。
    """
    import os

    from .resilience import get_health

    # trust_env=False 之外再剥代理环境变量（双保险，sdgp 惯例）
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "ccgp-hunan.gov.cn,www.ccgp-hunan.gov.cn")

    health = get_health("hunan")
    if health.is_blocked():
        return {}

    global _last_request_at
    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        try:
            headers = {"User-Agent": _UA, "Content-Type": "application/json"}
            if client is not None:
                resp = client.post(f"{HUNAN_URL}{SEARCH_HOME}", json=body, headers=headers)
            else:
                with httpx.Client(
                    timeout=httpx.Timeout(20.0), trust_env=False
                ) as own:
                    resp = own.post(
                        f"{HUNAN_URL}{SEARCH_HOME}", json=body, headers=headers
                    )
            _last_request_at = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream {resp.status_code}", request=resp.request, response=resp
                )
            if 400 <= resp.status_code < 500:
                logger.error(
                    "hunan 上游拒绝 HTTP %s（不重试）：%s",
                    resp.status_code, resp.text[:120],
                )
                health.mark_bad()
                return {}
            resp.raise_for_status()
            data = resp.json()
            _last_request_at = time.monotonic()
            if data.get("success") is not True:
                # luban 业务失败不重试不熔断（传输层通，sdgp 同款）
                logger.warning(
                    "hunan searchHome 业务失败 code=%s error=%s",
                    data.get("code"), data.get("error"),
                )
                return {}
            health.mark_good()
            return data
        except (httpx.HTTPError, ValueError) as exc:  # 网络/JSON 解析
            _last_request_at = time.monotonic()
            health.mark_bad()
            if health.is_blocked():
                logger.error("hunan 进入冷却，终止本轮 searchHome")
                return {}
            if attempt < MAX_RETRIES:
                backoff = REQUEST_INTERVAL_S * (2 ** (attempt + 1))
                logger.warning("hunan 请求失败(%s)，%.0fs 后重试", exc, backoff)
                time.sleep(backoff)
            else:
                logger.error("hunan searchHome 最终失败: %s", exc)
                return {}
    return {}  # pragma: no cover


# ---------------------------------------------------------------------------
# 接口封装
# ---------------------------------------------------------------------------
def fetch_home_blocks(page_size: int = 10, client: httpx.Client | None = None) -> list[dict]:
    """拉当日最新公告块，拍平 children → [{title, article_id, pub_date,
    district_name}]（SPEC-HUNAN H1 固定四字段拍平）。

    pub_date 取 publishDateString（"2026-11-19"），缺失回退 pubDate(epoch ms)
    转北京日期。空 children/非 JSON/冷却/失败一律 []。
    """
    body = {
        "code": CATEGORY_CODE,
        "subCodes": list(SUB_CODES),
        "districtCode": DISTRICT_CODE,
        "pageSize": max(1, page_size),
        "needTotal": False,
        "needNewCnt": False,
        "isProvince": True,
    }
    data = _post_home(body, client)
    result = data.get("result") or {}
    inner = result.get("data") or {}
    if not isinstance(inner, dict):
        inner = {}
    children = inner.get("children") or []
    blocks: list[dict] = []
    for ch in children:
        if not isinstance(ch, dict):
            continue
        title = (ch.get("title") or "").strip()
        article_id = (ch.get("articleId") or "").strip()
        if not title or not article_id:
            continue
        blocks.append({
            "title": title,
            "article_id": article_id,
            "pub_date": (ch.get("publishDateString") or "").strip()[:10]
            or _ms_to_date(ch.get("pubDate")),
            "district_name": (ch.get("districtName") or "").strip() or None,
        })
    return blocks


def detail_link(article_id: str, parent_id: str = PARENT_ID) -> str:
    """articleId → 可溯源真实详情页链接（Signal.url 用；quote 全编码红线）。"""
    return (
        f"{HUNAN_URL}/luban/front/detail?parentId={parent_id}"
        f"&articleId={quote(str(article_id), safe='')}"
    )


def fetch_detail(
    article_id: str,
    parent_id: str = PARENT_ID,
    client: httpx.Client | None = None,
) -> dict | None:
    """拉公告详情全文：{title, body_text}；失败/过期一律 None 不重试。

    响应（2026-11-19 实弹）：{success, result: {data: {title, content:
    "<HTML>", ...}}}——正文在 result.data.content，HTML 直出，剥标签压空白。
    articleId 含 / + = 必须全编码（quote safe=""）。旧 id 过期机制未实测，
    失败不重试不熔断重试（调用方拉到 id 后立即调用）。
    """
    import os

    from .resilience import get_health

    # trust_env=False 之外再剥代理环境变量（双保险，sdgp 惯例）
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "ccgp-hunan.gov.cn,www.ccgp-hunan.gov.cn")

    health = get_health("hunan")
    if health.is_blocked():
        return None
    _throttle()
    global _last_request_at
    try:
        url = (
            f"{HUNAN_URL}{DETAIL_URL}?articleId={quote(str(article_id), safe='')}"
            f"&parentId={parent_id}&timestamp={int(time.time() * 1000)}"
        )
        headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        if client is not None:
            resp = client.get(url, headers=headers)
        else:
            with httpx.Client(timeout=httpx.Timeout(20.0), trust_env=False) as own:
                resp = own.get(url, headers=headers)
        _last_request_at = time.monotonic()
        if resp.status_code != 200:
            health.mark_bad()
            return None
        data = resp.json()
        if data.get("success") is not True:
            logger.warning("hunan detail 业务失败 error=%s", data.get("error"))
            return None
        payload = ((data.get("result") or {}).get("data") or {})
        html = payload.get("content") or ""
        # 正文头部实测带 <style> 公告排版 CSS——先整块剥除再剥标签，
        # 防样式文本混入 body_text（电话抽取/证据摘要的噪音源）。
        html = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", html)
        body_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        health.mark_good()
        return {
            "title": (payload.get("title") or "").strip(),
            "body_text": body_text,
        }
    except (httpx.HTTPError, ValueError) as exc:
        _last_request_at = time.monotonic()
        health.mark_bad()
        logger.warning("hunan detail 失败（不重试）article_id=%s: %s", article_id, exc)
        return None


def filter_by_terms(blocks: list[dict], terms: list[str]) -> list[dict]:
    """本地词表过滤：title 含任一词即命中（无远端关键词接口，SPEC-HUNAN）。"""
    out = []
    for b in blocks:
        title = b.get("title") or ""
        if any(t and t in title for t in terms):
            out.append(b)
    return out
