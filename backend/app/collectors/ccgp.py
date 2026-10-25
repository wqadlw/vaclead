"""中国政府采购网（ccgp.gov.cn）公告采集器。

政采网 search 端点（search.ccgp.gov.cn/bxsearch）实测（2026-10-25）普通 HTTP +
完整浏览器请求头即可访问，无需浏览器；列表页自带 采购人/代理机构/公告类型/省份/品目。
吸收自 refs/CrawlerForCCGP（请求头与限速策略）与 refs/ZhaoBiaoSpider（站点图谱）。
合规守则见 docs/DATA_ACQUISITION.md：固定 UA、2-6s 随机间隔、只取公开列表。
"""
from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Iterator

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("vaclead.collectors.ccgp")

SEARCH_URL = "http://search.ccgp.gov.cn/bxsearch"
REQUEST_INTERVAL_S = 3.0  # 政采网比 ggzy 敏感，基准 3s
INTERVAL_JITTER_S = 3.0   # + 0~3s 随机
MAX_PAGES_DEFAULT = 5

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Referer": "http://search.ccgp.gov.cn/",
    "Upgrade-Insecure-Requests": "1",
}

_TAG_RE = re.compile(r"<[^>]+>")
_last_request_at = 0.0


def _ua() -> str:
    """两个稳定 UA 轮换（不做伪装指纹，仅常规浏览器标识）。"""
    return random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    ])


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL_S + random.uniform(0, INTERVAL_JITTER_S) - (
        time.monotonic() - _last_request_at
    )
    if wait > 0:
        time.sleep(wait)


def _fetch_page(keyword: str, page: int, start: str, end: str,
                client: httpx.Client | None = None) -> str:
    """拉取一页搜索 HTML；健康度管理（吸收 crawlee Session 错误积分制）。

    限流拦截页判死：mark_bad×3 → 域级冷却（指数 1min→1h），冷却期所有请求短路。
    start/end 格式 YYYY:MM:DD（政采网冒号分隔）。
    """
    import os

    from .resilience import get_health

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "ccgp.gov.cn,search.ccgp.gov.cn,www.ccgp.gov.cn")

    health = get_health("ccgp")
    if health.is_blocked():
        return ""

    params = {
        "searchtype": 1,     # 1=精确关键词（标题），2=全文
        "page_index": max(1, page),
        "bidSort": 0,
        "pinMu": 0,
        "bidType": 0,
        "dbselect": "bidx",
        "kw": keyword,
        "start_time": start,
        "end_time": end,
        "timeType": 6,       # 自定义时间段（配合 start/end）
        "displayZone": "",
        "zoneId": "",
        "pppStatus": 0,
        "agentName": "",
    }
    global _last_request_at
    for attempt in range(3):
        _throttle()
        try:
            if client is not None:
                resp = client.get(SEARCH_URL, params=params,
                                  headers={**_HEADERS, "User-Agent": _ua()})
            else:
                with httpx.Client(timeout=httpx.Timeout(25.0), follow_redirects=True) as own:
                    resp = own.get(SEARCH_URL, params=params,
                                   headers={**_HEADERS, "User-Agent": _ua()})
            _last_request_at = time.monotonic()
            if resp.status_code == 200:
                text = resp.text
                if "频繁访问" in text:
                    # 限流拦截页（HTTP 200 但内容为"频繁访问!"）：记满积分触发冷却
                    health.mark_bad()
                    health.mark_bad()
                    logger.warning(
                        "ccgp 限流拦截（积分 %.1f，冷却=%s）",
                        health.error_score, health.is_blocked(),
                    )
                    if health.is_blocked():
                        return ""
                    time.sleep(REQUEST_INTERVAL_S * (attempt + 1))
                    continue
                health.mark_good()
                return text
            health.mark_bad()
            logger.warning("ccgp HTTP %s（第 %s 次，积分 %.1f）",
                           resp.status_code, attempt + 1, health.error_score)
            if health.is_blocked():
                return ""
        except httpx.HTTPError as exc:
            _last_request_at = time.monotonic()
            health.mark_bad()
            logger.warning("ccgp 请求失败(%s) 第 %s 次（积分 %.1f）",
                           exc, attempt + 1, health.error_score)
            if health.is_blocked():
                return ""
        time.sleep(REQUEST_INTERVAL_S * (attempt + 1))
    return ""


# ---------------------------------------------------------------------------
# 公告详情页（联系方式是法定公开内容，本就写给投标人——SPEC-D 数据解锁）
# ---------------------------------------------------------------------------
_DETAIL_FIELDS = (
    ("project_contact", r"项目联系人\s*([^\s]+)"),
    ("project_phone", r"项目联系电话\s*([\d\-转,，、 ]+)"),
    ("buyer_name", r"采购单位\s*([^\s]+?)(?:\s*(?:行政区域|采购单位地址|代理机构))"),
    ("buyer_address", r"采购单位地址\s*([^\s]+?)(?:\s*采购单位联系方式)"),
    ("buyer_contact", r"采购单位联系方式\s*(.+?)(?=\s*代理机构名称)"),
    ("agency_name", r"代理机构名称\s*([^\s]+?)(?:\s*代理机构地址)"),
    ("agency_contact", r"代理机构联系方式\s*(.+?)(?:\s*(?:十一|其他|附录)|$)"),
    ("region", r"行政区域\s*([^\s]+)"),
)


def fetch_notice_detail(url: str, client: httpx.Client | None = None) -> dict | None:
    """抓公告详情页并抽取联系方式段；被限流/无内容返回 None。"""
    if not (url or "").strip():
        return None
    html = _fetch_page_url(url, client)
    if not html or "频繁访问" in html:
        logger.warning("ccgp 详情页限流或为空：%s", url[:60])
        return None
    text = re.sub(r"\s+", " ", _TAG_RE.sub(" ", html))
    out: dict[str, str | None] = {}
    for key, pattern in _DETAIL_FIELDS:
        m = re.search(pattern, text)
        out[key] = m.group(1).strip() if m else None
    return out if any(v for v in out.values()) else None


def _fetch_page_url(url: str, client: httpx.Client | None = None) -> str:
    """任意 ccgp URL 抓取（节流+重试，供详情页用）。"""
    global _last_request_at
    for attempt in range(3):
        _throttle()
        try:
            if client is not None:
                resp = client.get(url, headers={**_HEADERS, "User-Agent": _ua()})
            else:
                with httpx.Client(timeout=httpx.Timeout(25.0), follow_redirects=True) as own:
                    resp = own.get(url, headers={**_HEADERS, "User-Agent": _ua()})
            _last_request_at = time.monotonic()
            if resp.status_code == 200:
                if "频繁访问" in resp.text:
                    logger.warning("ccgp 限流拦截页：%s", url[:60])
                    return ""
                return resp.text
            logger.warning("ccgp HTTP %s %s", resp.status_code, url[:60])
        except httpx.HTTPError as exc:
            _last_request_at = time.monotonic()
            logger.warning("ccgp 请求失败(%s) %s", exc, url[:60])
        time.sleep(REQUEST_INTERVAL_S * (attempt + 1))
    return ""


def parse_list_html(html: str) -> list[dict]:
    """解析搜索结果列表 → 结构化条目。

    每条：title/url/pub_date/notice_type/province/category/summary/buyer/agent。
    政采网列表自带采购人——无需进详情页即可归到买家主体。
    """
    if not html or "vT-srch-result-list-bid" not in html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    ul = soup.find("ul", class_="vT-srch-result-list-bid")
    if ul is None:
        return []
    items: list[dict] = []
    for li in ul.find_all("li"):
        a = li.find("a", href=True)
        if a is None:
            continue
        title = _TAG_RE.sub("", a.decode_contents() or "").strip()
        url = (a.get("href") or "").strip()
        p = li.find("p")
        summary = _TAG_RE.sub("", p.decode_contents() or "").strip() if p else ""
        span = li.find("span")
        pub_date = buyer = agent = notice_type = province = category = None
        if span is not None:
            span_text = _TAG_RE.sub("|", span.decode_contents() or "")
            # 段结构：时间 | 采购人：X | 代理机构：Y <br/> 类型 | 省份 | 品目
            span_text = re.sub(r"\s+", " ", span_text).replace("<br/>", "|").replace("<br>", "|")
            parts = [seg.strip() for seg in span_text.split("|") if seg.strip()]
            if parts:
                m = re.match(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", parts[0])
                if m:
                    pub_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            for seg in parts:
                if seg.startswith("采购人："):
                    buyer = seg[len("采购人："):].strip() or None
                elif seg.startswith("采购人:"):
                    buyer = seg[len("采购人:"):].strip() or None
                elif seg.startswith("代理机构："):
                    agent = seg[len("代理机构："):].strip() or None
            # 类型/省份/品目：从尾部非日期段识别
            tail = [s for s in parts if not re.match(r"^\d{4}[.\-]", s)
                    and not s.startswith("采购人") and not s.startswith("代理机构")]
            known_types = ("中标公告", "成交公告", "招标公告", "竞争性磋商公告",
                           "竞争性谈判公告", "询价公告", "更正公告", "废标公告",
                           "资格预审公告", "单一来源", "合同公告", "终止公告")
            for seg in tail:
                if notice_type is None and any(t in seg for t in known_types):
                    notice_type = next(t for t in known_types if t in seg)
                    continue
                if category is None and ("/" in seg or "品目" in seg):
                    category = seg
                elif province is None and len(seg) <= 5 and not seg.isdigit():
                    province = seg
        if not title or not url:
            continue
        items.append({
            "title": title, "url": url, "pub_date": pub_date,
            "notice_type": notice_type, "province": province, "category": category,
            "summary": summary[:400], "buyer": buyer, "agent": agent,
        })
    return items


def search_ccgp(keyword: str, page: int = 1, days: int = 90,
                client: httpx.Client | None = None) -> list[dict]:
    """关键词搜政采网公告（单页）。days=近 N 天。"""
    import datetime as _dt

    end = _dt.date.today()
    start = end - _dt.timedelta(days=days)
    html = _fetch_page(keyword, page,
                       start.strftime("%Y:%m:%d"), end.strftime("%Y:%m:%d"), client)
    items = parse_list_html(html)
    if not items:
        logger.info("ccgp 第%s页无结果（kw=%s）", page, keyword)
    return items


def search_ccgp_all(keyword: str, days: int = 90, max_pages: int = MAX_PAGES_DEFAULT,
                    client: httpx.Client | None = None) -> Iterator[dict]:
    """分页迭代（max_pages 保险上限）。"""
    for p in range(1, max(1, max_pages) + 1):
        items = search_ccgp(keyword, page=p, days=days, client=client)
        if not items:
            break
        yield from items
