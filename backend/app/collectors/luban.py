"""政采云系多省意向公开采集器（沪黔桂，SPEC-LUBAN，luban 平台配置驱动）。

湖南 /portal/searchHome 契约在沪黔双省同款验证成功（districtCode 可选）。
本模块把三省参数化进 LUBAN_PROVINCES：同一 searchHome 组件，逐省
{base, code, sub_codes, district, province, source_buyer} 六元组。

与湖南模式不同：意向标题无设备词——**全量入库**（建档+单位名，不做
词表过滤；日增 4-20 条/省，增量 searchHome 天然只回当日块）。

契约（docs/SPEC-LUBAN.md）：

  POST {base}/portal/searchHome    # 无鉴权（luban 列表组件同款）
  body = {"code", "subCodes", "districtCode"(空省不带该键), "pageSize",
          "needTotal": False, "needNewCnt": False, "isProvince": True}
  → {success, result: {data: {children: [{title, articleId,
     publishDateString, districtName, ...}]}}}

- detail 本 SPEC 不做（沪黔桂 detail 契约未验证——意向核心价值=单位名+
  日期，正文抽标的待 detail 契约逐省实弹后增强）。
- httpx 必须 trust_env=False + 剥代理环境变量双保险（sdgp/hunan 惯例）；
  限速 1.5s + resilience get_health(prov_key) 省级熔断；失败/非 JSON → []。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("vaclead.collectors.luban")

#: 三省配置（唯一权威，SPEC-LUBAN L1）：district 为空串 = 请求体不带
#: districtCode 键（上海实测空 district 才通）；guangxi 需要 districtCode
#: 圈定省本级。sub_codes：上海/贵州主码直达，广西需补意向子码。
LUBAN_PROVINCES: dict[str, dict] = {
    "shanghai": {
        "base": "https://www.zfcg.sh.gov.cn",
        "code": "77-379845", "sub_codes": [], "district": "",
        "province": "上海市", "source_buyer": "shanghai_buyer",
    },
    "guizhou": {
        "base": "http://www.ccgp-guizhou.gov.cn",
        "code": "ZcyAnnouncement10016", "sub_codes": [], "district": "",
        "province": "贵州省", "source_buyer": "guizhou_buyer",
    },
    "guangxi": {
        "base": "https://zfcg.gxzf.gov.cn",
        "code": "61-266648", "sub_codes": ["ZcyAnnouncement10016"],
        "district": "459900", "province": "广西壮族自治区",
        "source_buyer": "guangxi_buyer",
    },
}

SEARCH_HOME = "/portal/searchHome"   # POST 无鉴权（luban 列表组件）
REQUEST_INTERVAL_S = 1.5             # 礼貌间隔（sdgp/ggzy/hunan 同款）
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


def _post_home(url: str, body: dict, prov_key: str,
               client: httpx.Client | None = None) -> dict:
    """带限速、健康度与重试的 searchHome POST（hunan._post_home 同款积分制）。

    client 参数供测试注入 MockTransport；生产路径 trust_env=False 新建
    连接。冷却中（is_blocked）直接短路返回 {}；业务失败（success!=True）
    返回 {} 不 mark_bad——传输层是通的。
    """
    import os

    from .resilience import get_health

    # trust_env=False 之外再剥代理环境变量（双保险，sdgp/hunan 惯例）
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault(
        "NO_PROXY",
        "zfcg.sh.gov.cn,www.zfcg.sh.gov.cn,www.ccgp-guizhou.gov.cn,"
        "ccgp-guizhou.gov.cn,zfcg.gxzf.gov.cn",
    )

    health = get_health(prov_key)
    if health.is_blocked():
        return {}

    global _last_request_at
    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        try:
            headers = {"User-Agent": _UA, "Content-Type": "application/json"}
            if client is not None:
                resp = client.post(url, json=body, headers=headers)
            else:
                with httpx.Client(
                    timeout=httpx.Timeout(20.0), trust_env=False
                ) as own:
                    resp = own.post(url, json=body, headers=headers)
            _last_request_at = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream {resp.status_code}", request=resp.request, response=resp
                )
            if 400 <= resp.status_code < 500:
                logger.error(
                    "luban[%s] 上游拒绝 HTTP %s（不重试）：%s",
                    prov_key, resp.status_code, resp.text[:120],
                )
                health.mark_bad()
                return {}
            resp.raise_for_status()
            data = resp.json()
            _last_request_at = time.monotonic()
            if data.get("success") is not True:
                # luban 业务失败不重试不熔断（传输层通，hunan 同款）
                logger.warning(
                    "luban[%s] searchHome 业务失败 code=%s error=%s",
                    prov_key, data.get("code"), data.get("error"),
                )
                return {}
            health.mark_good()
            return data
        except (httpx.HTTPError, ValueError) as exc:  # 网络/JSON 解析
            _last_request_at = time.monotonic()
            health.mark_bad()
            if health.is_blocked():
                logger.error("luban[%s] 进入冷却，终止本轮 searchHome", prov_key)
                return {}
            if attempt < MAX_RETRIES:
                backoff = REQUEST_INTERVAL_S * (2 ** (attempt + 1))
                logger.warning("luban[%s] 请求失败(%s)，%.0fs 后重试", prov_key, exc, backoff)
                time.sleep(backoff)
            else:
                logger.error("luban[%s] searchHome 最终失败: %s", prov_key, exc)
                return {}
    return {}  # pragma: no cover


def fetch_luban_home(prov_key: str, page_size: int = 10,
                     client: httpx.Client | None = None) -> list[dict]:
    """拉单省当日意向块，拍平 children → [{title, article_id, pub_date,
    district_name}]（SPEC-LUBAN L1 固定四字段拍平）。

    pub_date 取 publishDateString（"2026-11-21"），缺失回退 pubDate(epoch ms)
    转北京日期。空 children/非 JSON/冷却/失败/未知省一律 []。
    """
    cfg = LUBAN_PROVINCES.get(prov_key)
    if cfg is None:
        logger.error("luban：未知省份 %s（LUBAN_PROVINCES 未登记）", prov_key)
        return []
    body = {
        "code": cfg["code"],
        "subCodes": list(cfg["sub_codes"]),
        "pageSize": max(1, page_size),
        "needTotal": False,
        "needNewCnt": False,
        "isProvince": True,
    }
    if cfg["district"]:
        # 空省不带该键（上海实测空 district 才通；guangxi 带省本级 459900）
        body["districtCode"] = cfg["district"]
    data = _post_home(f"{cfg['base']}{SEARCH_HOME}", body, prov_key, client)
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
