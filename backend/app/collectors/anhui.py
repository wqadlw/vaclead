"""安徽政采采集器（ccgp-anhui.gov.cn，SPEC-AHUI，portal/category 形态）。

安徽 = 政采云 portal/category 形态（与湖南 searchHome 不同）：**标准分页
+ author 字段直接给采购人单位名**——无需标题抽取，数据比山东还全。

契约（docs/SPEC-AHUI.md，2026-11-21 httpx 实弹五发验证）：

  POST https://www.ccgp-anhui.gov.cn/portal/category   # 无鉴权无验证码
  body = {"pageNo": 1, "pageSize": 20,
          "categoryCode": "ZcyAnnouncement3012",
          "districtCode": ["340000"], "leaf": "0"}
  → {success, result: {data: {total: 1963, data: [{articleId(加密串),
     author(=采购人单位名), title, pathName(公告类型名),
     publishDate(epoch ms), content, districtName}]}}}

  **注意**：records 在 ``result.data.data``（``result.data.total`` 与其
  平级）——不是常规的顶层 records/children，拍平取数别按常规写。

- **必须 HTTPS**：HTTP 全部 301 CWAP-waf（实弹）；base 固定 https://。
- **pageSize ≤ 15**：20 稳定 429（CWAP-waf 反爬规则实弹，15/10/5 均
  200）——分页量大靠页数不靠单页条数。
- 列表响应里 content 常为 None——详情正文不在列表接口，本 SPEC 不做
  详情（A1 说明）；详情页路由 /site/article?articleId= 实弹验证 301 到
  mall 首页（非文章页）——Signal.url 落 ""（SPEC 约定的验证不通回退）。
- httpx 必须 trust_env=False（shell 代理毒死直连的坑，sdgp/hunan 同款）
  + 剥代理环境变量双保险；限速 1.5s + resilience get_health("anhui")
  域级熔断。
- **全量入库模式**（同 luban 决策——title 无设备词不筛）：author 直接
  resolve 建档（source=anhui_buyer，省份=安徽省只补空）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("vaclead.collectors.anhui")

ANHUI_URL = "https://www.ccgp-anhui.gov.cn"   # 必须 HTTPS（HTTP 301 CWAP-waf）
CATEGORY_PATH = "/portal/category"            # POST 无鉴权（标准分页组件）
DISTRICT_CODES = ["340000"]                   # 安徽全省（实测 body 数组形态）
CATEGORY_CODE = "ZcyAnnouncement3012"         # 采购/结果公告（1963 条实测）
CATEGORY_RESULT = "ZcyAnnouncement3015"       # 结果公告大池（16,080；枚举待续）
PAGE_SIZE = 15  # 每页条数——**实弹红线：pageSize=20 稳定 429**（CWAP-waf 反
                # 爬规则，≤15 均 200），别照模板改回 20
REQUEST_INTERVAL_S = 1.5                      # 礼貌间隔（sdgp/ggzy/hunan 同款）
MAX_RETRIES = 2
_UA = "vaclead-acquisition/1.0 (+research; contact: local self-hosted)"

_CN_TZ = timezone(timedelta(hours=8))  # publishDate 为北京时间口径

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)


def _ms_to_date(ms) -> str | None:
    """epoch 毫秒 → 北京时间日期串（publishDate 缺失/脏值兜底 None）。"""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=_CN_TZ).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _post_category(body: dict, client: httpx.Client | None = None) -> dict:
    """带限速、健康度与重试的 /portal/category POST（hunan._post_home 同款积分制）。

    client 参数供测试注入 MockTransport；生产路径 trust_env=False 新建
    连接。冷却中（is_blocked）直接短路返回 {}；业务失败（success!=True）
    返回 {} 不 mark_bad——传输层是通的。
    """
    import os

    from .resilience import get_health

    # trust_env=False 之外再剥代理环境变量（双保险，sdgp/hunan 惯例）
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "ccgp-anhui.gov.cn,www.ccgp-anhui.gov.cn")

    health = get_health("anhui")
    if health.is_blocked():
        return {}

    global _last_request_at
    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        try:
            headers = {"User-Agent": _UA, "Content-Type": "application/json"}
            if client is not None:
                resp = client.post(f"{ANHUI_URL}{CATEGORY_PATH}", json=body, headers=headers)
            else:
                with httpx.Client(
                    timeout=httpx.Timeout(20.0), trust_env=False
                ) as own:
                    resp = own.post(
                        f"{ANHUI_URL}{CATEGORY_PATH}", json=body, headers=headers
                    )
            _last_request_at = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream {resp.status_code}", request=resp.request, response=resp
                )
            if 400 <= resp.status_code < 500:
                logger.error(
                    "anhui 上游拒绝 HTTP %s（不重试）：%s",
                    resp.status_code, resp.text[:120],
                )
                health.mark_bad()
                return {}
            resp.raise_for_status()
            data = resp.json()
            _last_request_at = time.monotonic()
            if data.get("success") is not True:
                # 业务失败不重试不熔断（传输层通，hunan/luban 同款）
                logger.warning(
                    "anhui category 业务失败 error=%s", data.get("error"),
                )
                return {}
            health.mark_good()
            return data
        except (httpx.HTTPError, ValueError) as exc:  # 网络/JSON 解析
            _last_request_at = time.monotonic()
            health.mark_bad()
            if health.is_blocked():
                logger.error("anhui 进入冷却，终止本轮 category")
                return {}
            if attempt < MAX_RETRIES:
                backoff = REQUEST_INTERVAL_S * (2 ** (attempt + 1))
                logger.warning("anhui 请求失败(%s)，%.0fs 后重试", exc, backoff)
                time.sleep(backoff)
            else:
                logger.error("anhui category 最终失败: %s", exc)
                return {}
    return {}  # pragma: no cover


def fetch_category(
    page_no: int = 1,
    category_code: str = CATEGORY_CODE,
    page_size: int = PAGE_SIZE,
    client: httpx.Client | None = None,
) -> dict:
    """拉单页公告列表，拍平 data.data → {"total": int|None, "records": [...]}。

    每条拍平为 {title, author, article_id, pub_date, path_name,
    district_name, content}——author=采购人单位名直接可建档（无需标题
    抽取，SPEC-AHUI 核心优势）；pub_date 取 publishDate(epoch ms) 转北京
    日期（luban._ms_to_date 同款）。content 列表响应常为 None（详情正文
    不在列表接口，本 SPEC 不做详情）。空页/非 JSON/冷却/失败一律
    {"total": None, "records": []}。
    """
    body = {
        "pageNo": max(1, page_no),
        "pageSize": max(1, page_size),
        "categoryCode": category_code,
        "districtCode": list(DISTRICT_CODES),
        "leaf": "0",
    }
    data = _post_category(body, client)
    result = data.get("result") or {}
    inner = result.get("data") or {}
    if not isinstance(inner, dict):
        inner = {}
    raw_records = inner.get("data") or []
    total = inner.get("total")
    records: list[dict] = []
    for rec in raw_records:
        if not isinstance(rec, dict):
            continue
        title = (rec.get("title") or "").strip()
        if not title:
            continue
        records.append({
            "title": title,
            "author": (rec.get("author") or "").strip(),
            "article_id": (rec.get("articleId") or "").strip(),
            "pub_date": _ms_to_date(rec.get("publishDate")),
            "path_name": (rec.get("pathName") or "").strip() or None,
            "district_name": (rec.get("districtName") or "").strip() or None,
            "content": rec.get("content"),  # 列表响应常 None（A1：不做详情）
        })
    return {"total": total if isinstance(total, int) else None, "records": records}
