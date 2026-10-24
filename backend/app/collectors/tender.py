"""招标公告采集器：知了标讯开放 API + CSV 导入（契约见 docs/SPEC.md §4）。

两者都产出 {title, content, url, pub_date} 列表交给 signal_parser；
CSV 导入额外携带可选的 enterprise_name。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Sequence
from datetime import datetime

import httpx

logger = logging.getLogger("vaclead.collectors.tender")

ZHILIAOKE_LIST_URL = "https://api.zhiliaobiao.com/open/api/list"
MAX_PAGES_PER_KEYWORD = 20  # 每关键词分页保险上限

# ---------------------------------------------------------------------------
# 知了标讯开放接口字段映射 —— 以实际开放文档为准（https://www.zhiliaobiao.com）。
# 若文档字段名不同，只需改这里（可配置），调用方接口不变。
# ---------------------------------------------------------------------------
ZHILIAOKE_REQ_FIELDS: dict[str, str] = {
    "keyword": "keyword",   # 搜索词
    "page": "page",         # 页码（从 1 开始）
    "days": "days",         # 时间范围（近 N 天）
    "key": "key",           # 开放平台 API Key
}
# 响应里公告列表可能的容器键（依次尝试 data.<key> 与顶层 <key>）
ZHILIAOKE_RESP_LIST_KEYS: tuple[str, ...] = ("list", "items", "records", "rows")
ZHILIAOKE_RESP_TOTAL_KEYS: tuple[str, ...] = ("total", "totalCount", "count")

# 常见日期格式 → 归一到 YYYY-MM-DD
_DATE_PATTERNS: tuple[str, ...] = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
    "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
)
_CN_DATE_RE = re.compile(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def normalize_date(raw: str) -> str | None:
    """把各种常见日期写法归一为 YYYY-MM-DD；无法解析返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    m = _CN_DATE_RE.match(s)  # 2026年10月24日（可带后续时间）
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 知了标讯开放 API
# ---------------------------------------------------------------------------
def _check_status(data: dict) -> None:
    """知了约定 status=0 为成功；非 0 抛 RuntimeError。"""
    try:
        status = int(data.get("status"))
    except (TypeError, ValueError):
        status = -1
    if status != 0:
        msg = data.get("message") or data.get("msg") or data.get("info") or ""
        raise RuntimeError(f"知了标讯 API 返回异常 status={status} msg={msg}")


def _extract_items(data: dict) -> list[dict]:
    for container in (data.get("data") if isinstance(data.get("data"), dict) else None, data):
        if not isinstance(container, dict):
            continue
        for key in ZHILIAOKE_RESP_LIST_KEYS:
            items = container.get(key)
            if isinstance(items, list):
                return [it for it in items if isinstance(it, dict)]
    return []


def _extract_total(data: dict) -> int:
    for container in (data.get("data") if isinstance(data.get("data"), dict) else None, data):
        if not isinstance(container, dict):
            continue
        for key in ZHILIAOKE_RESP_TOTAL_KEYS:
            try:
                return int(container.get(key))
            except (TypeError, ValueError):
                continue
    return 0


def _parse_item(item: dict) -> dict:
    return {
        "title": str(item.get("title") or item.get("name") or "").strip(),
        "content": str(
            item.get("content") or item.get("contentDesc") or item.get("description") or ""
        ).strip(),
        "url": str(item.get("url") or item.get("link") or item.get("urlLink") or "").strip(),
        "pub_date": normalize_date(
            str(item.get("pub_date") or item.get("publishTime")
                or item.get("createTime") or item.get("pubdate") or "")
        )
        or "",
    }


async def fetch_zhiliaoke_tenders(
    keywords: Sequence[str],
    days: int,
    api_key: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """按设备词调知了标讯开放 API，拉取近 N 天公告，产出标准信号源列表。

    POST {ZHILIAOKE_LIST_URL} JSON {keyword, page, days, key}
    （字段名以实际文档为准，见 ZHILIAOKE_REQ_FIELDS）。逐关键词分页
    拉取至列表为空或 total 取尽。status 非 0 抛 RuntimeError。
    """
    if not api_key or not str(api_key).strip():
        raise RuntimeError("缺少知了标讯 API Key")
    if isinstance(keywords, str):
        keywords = [keywords]
    kw_list = [str(kw).strip() for kw in keywords if kw and str(kw).strip()]
    days = int(days)
    f = ZHILIAOKE_REQ_FIELDS

    async def _run(client: httpx.AsyncClient) -> list[dict]:
        out: list[dict] = []
        for kw in kw_list:
            got = 0
            for page in range(1, MAX_PAGES_PER_KEYWORD + 1):
                payload = {
                    f["keyword"]: kw,
                    f["page"]: page,
                    f["days"]: days,
                    f["key"]: str(api_key).strip(),
                }
                resp = await client.post(ZHILIAOKE_LIST_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                _check_status(data)
                items = _extract_items(data)
                if not items:
                    break
                out.extend(_parse_item(it) for it in items)
                got += len(items)
                total = _extract_total(data)
                if total and got >= total:
                    break
        return out

    if http_client is not None:
        return await _run(http_client)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        return await _run(client)


# ---------------------------------------------------------------------------
# CSV 导入
# ---------------------------------------------------------------------------
def _decode_csv_bytes(data: bytes) -> str:
    """导入文件解码（按探测优先级）：UTF-8 BOM > UTF-16 BOM（Excel“Unicode 文本”另存）>
    UTF-8 严格 > GBK 兜底。覆盖国内 Excel 常见导出格式。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def parse_tender_csv(file_bytes: bytes, stats: dict | None = None) -> list[dict]:
    """解析导入的招标 CSV（UTF-8 BOM / UTF-16 / GBK 自动探测）。

    列：title,content,url,pub_date,enterprise_name(可选)。
    行级容错：缺 title、缺 pub_date 或日期无法归一的行跳过并计数；
    计数写入可选 stats 参数：{"total": n, "parsed": m, "skipped": k}。
    pub_date 统一归一为 YYYY-MM-DD。
    """
    if stats is not None:
        stats.clear()
        stats.update({"total": 0, "parsed": 0, "skipped": 0})
    text = _decode_csv_bytes(file_bytes)

    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for row in reader:
        if not isinstance(row, dict):
            continue
        if stats is not None:
            stats["total"] += 1
        title = str(row.get("title") or "").strip()
        pub_raw = str(row.get("pub_date") or "").strip()
        if not title or not pub_raw:
            if stats is not None:
                stats["skipped"] += 1
            continue
        pub_date = normalize_date(pub_raw)
        if pub_date is None:
            if stats is not None:
                stats["skipped"] += 1
            continue
        rows.append({
            "title": title,
            "content": str(row.get("content") or "").strip(),
            "url": str(row.get("url") or "").strip(),
            "pub_date": pub_date,
            "enterprise_name": str(row.get("enterprise_name") or "").strip(),
        })
    if stats is not None:
        stats["parsed"] = len(rows)
    return rows
