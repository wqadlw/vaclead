"""高德地图 POI 采集器（契约见 docs/SPEC.md §4）。

要点：
- 调 /v3/place/text 关键词检索：params = keywords, city, citylimit='true',
  offset='25', page, key；逐关键词翻页至 count 用尽或单关键词累计 200 条即停；
- 简化四叉树（区县粒度）：城市级超限的关键词由调用方改用
  collect_city_by_district(city, districts, keywords, ...) 细化——同一 API，
  仅把 `city` 参数换成区县名；
- amap_key 支持逗号分隔多 key 轮询（KeyRotator 线程安全计数器）；
- QPS 节流：相邻请求间隔 >= MIN_REQUEST_INTERVAL（默认 0.34s ≈ 3 QPS，
  asyncio.sleep 实现）；
- status != 1 / count == 0 / 空页 均优雅跳过（记日志不抛错）；
- 结果按 poi_id 全局去重，产出候选企业 dict（GCJ02 坐标直接存）；
- progress_cb(done, total, log_text) 逐采集单元回报进度；
- http_client 可注入 httpx.AsyncClient（测试用 MockTransport，全程离线）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

logger = logging.getLogger("vaclead.collectors.amap")

AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"
AMAP_PLACE_POLYGON_URL = "https://restapi.amap.com/v3/place/polygon"
AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
PAGE_SIZE = 25                    # 高德单页条数（offset）
MAX_PER_KEYWORD = 200             # 单关键词条数上限（8 页 × 25）
MIN_REQUEST_INTERVAL = 0.34       # 相邻请求最小间隔（秒），QPS ≤ 3
REQUEST_TIMEOUT = httpx.Timeout(15.0)
MAX_QUADTREE_DEPTH = 6            # 四叉树递归深度上限（城市尺度足够）

ProgressCB = Callable[[int, int, str], None]

# QPS 节流用的单调时钟时间戳。采集循环内为顺序 await，无需额外加锁。
_last_request_at = 0.0


class KeyRotator:
    """高德 key 轮询器：逗号分隔多 key，线程安全计数器轮转。"""

    def __init__(self, amap_key: str) -> None:
        keys = [k.strip() for k in (amap_key or "").split(",") if k.strip()]
        if not keys:
            raise ValueError("amap_key 为空：至少需要一个高德 Key")
        self._keys: list[str] = keys
        self._counter = 0
        self._lock = threading.Lock()
        self.used_keys: set[str] = set()

    @property
    def size(self) -> int:
        return len(self._keys)

    def next_key(self) -> str:
        with self._lock:
            key = self._keys[self._counter % len(self._keys)]
            self._counter += 1
            self.used_keys.add(key)
            return key


async def _throttle() -> None:
    """保证相邻请求间隔 >= MIN_REQUEST_INTERVAL（QPS 节流）。"""
    global _last_request_at
    if MIN_REQUEST_INTERVAL > 0:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def _normalize_keywords(keywords: str | Sequence[str]) -> list[str]:
    if isinstance(keywords, str):
        keywords = [keywords]
    return [kw.strip() for kw in keywords if kw and kw.strip()]


def _parse_poi(poi: dict, default_city: str = "") -> dict:
    """把高德 poi 节点解析为候选企业 dict。location 'lng,lat' 拆为 lat/lng。"""
    location = str(poi.get("location") or "").strip()
    lat: float | None = None
    lng: float | None = None
    if "," in location:
        lng_s, _, lat_s = location.partition(",")
        try:
            lng = float(lng_s)
            lat = float(lat_s)
        except ValueError:
            lng = lat = None
    return {
        "name": str(poi.get("name") or "").strip(),
        "address": str(poi.get("address") or "").strip(),
        "location": location,          # 原始 "lng,lat"（GCJ02）
        "lat": lat,                    # 拆分后的纬度
        "lng": lng,                    # 拆分后的经度
        "typecode": str(poi.get("typecode") or "").strip(),
        "poi_id": str(poi.get("id") or "").strip(),
        "city": str(poi.get("cityname") or default_city or "").strip(),
        "district": str(poi.get("adname") or "").strip(),  # 区县名（adname/adcode 字段）
    }


async def _fetch_page(
    client: httpx.AsyncClient,
    rotator: KeyRotator,
    *,
    keyword: str,
    location: str | None,
    page: int,
    polygon: str | None = None,
) -> dict | None:
    """请求一页检索结果；polygon 给定时走多边形搜索，否则走 city + citylimit。

    网络/解码失败返回 None（由上层优雅跳过）。
    """
    if polygon:
        params = {
            "polygon": polygon,
            "keywords": keyword,
            "offset": str(PAGE_SIZE),
            "page": str(page),
            "key": rotator.next_key(),
        }
        url = AMAP_PLACE_POLYGON_URL
    else:
        params = {
            "keywords": keyword,
            "city": location,
            "citylimit": "true",
            "offset": str(PAGE_SIZE),
            "page": str(page),
            "key": rotator.next_key(),
        }
        url = AMAP_PLACE_TEXT_URL
    await _throttle()
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("高德请求失败 keyword=%s location=%s page=%s: %s", keyword, location, page, exc)
        return None


async def _collect_keyword(
    client: httpx.AsyncClient,
    rotator: KeyRotator,
    *,
    keyword: str,
    location: str | None,
    default_city: str,
    seen_ids: set[str],
    max_items: int = MAX_PER_KEYWORD,
    polygon: str | None = None,
) -> tuple[list[dict], bool]:
    """单个（关键词 × 地域/多边形）单元翻页采集。

    返回 (新增 POI 列表, 是否因达单关键词条数上限截断)。
    status!=1 / count==0 / 空页 → 优雅终止；poi_id 全局去重。
    """
    collected: list[dict] = []
    fetched = 0
    page = 1
    truncated = False
    while True:
        data = await _fetch_page(
            client, rotator, keyword=keyword, location=location, page=page, polygon=polygon
        )
        if data is None:
            break
        if str(data.get("status", "0")) != "1":
            logger.warning(
                "高德返回异常 status=%s infocode=%s info=%s keyword=%s location=%s，跳过",
                data.get("status"), data.get("infocode"), data.get("info"), keyword, location,
            )
            break
        try:
            count = int(str(data.get("count") or 0))
        except ValueError:
            count = 0
        pois = data.get("pois") or []
        if count <= 0 or not pois:
            break  # 空 count / 空页：优雅结束
        for poi in pois:
            if not isinstance(poi, dict):
                continue
            poi_id = str(poi.get("id") or "").strip()
            if not poi_id or poi_id in seen_ids:
                continue
            seen_ids.add(poi_id)
            collected.append(_parse_poi(poi, default_city=default_city))
        fetched += len(pois)
        if fetched >= count:
            break  # count 用尽
        if fetched >= max_items:
            truncated = True  # 单关键词超上限截断，调用方可按区县细化重试
            break
        page += 1
    return collected, truncated


def _report(
    progress_cb: ProgressCB | None,
    done: int,
    total: int,
    keyword: str,
    new_pois: list[dict],
    all_pois: list[dict],
    truncated: bool,
    scope: str,
) -> None:
    if progress_cb is None:
        return
    log = f"[{scope}] 关键词「{keyword}」新增 {len(new_pois)} 条 POI（累计 {len(all_pois)}）"
    if truncated:
        log += f"；已达单关键词 {MAX_PER_KEYWORD} 条上限，建议按区县粒度细化重采"
    progress_cb(done, total, log)


async def _run_with_client(http_client: httpx.AsyncClient | None, run_fn) -> list[dict]:
    if http_client is not None:
        return await run_fn(http_client)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        return await run_fn(client)


async def collect_pois(
    city: str,
    keywords: Sequence[str],
    amap_key: str,
    progress_cb: ProgressCB | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """按城市逐关键词采集 POI，产出候选企业列表（poi_id 全局去重）。

    progress_cb(done, total, log_text)：done/total 为已完成/总关键词数。
    """
    rotator = KeyRotator(amap_key)
    kw_list = _normalize_keywords(keywords)
    total = len(kw_list)

    async def _run(client: httpx.AsyncClient) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for i, kw in enumerate(kw_list):
            new_pois, truncated = await _collect_keyword(
                client, rotator, keyword=kw, location=city, default_city=city, seen_ids=seen
            )
            out.extend(new_pois)
            _report(progress_cb, i + 1, total, kw, new_pois, out, truncated, scope=city)
        return out

    return await _run_with_client(http_client, _run)


async def collect_city_by_district(
    city: str,
    districts: Sequence[str],
    keywords: Sequence[str],
    amap_key: str,
    progress_cb: ProgressCB | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """简化四叉树的区县粒度细化采集：同一 API，`city` 参数逐个换区县名。

    适用于城市级单关键词超 200 条被截断的场景；结果与调用方已有数据
    一样按 poi_id 去重（本函数内部自去重）。progress 的 done/total
    以（关键词 × 区县）采集单元计。
    """
    rotator = KeyRotator(amap_key)
    kw_list = _normalize_keywords(keywords)
    dist_list = [d.strip() for d in districts if d and d.strip()]
    units = [(kw, dist) for kw in kw_list for dist in dist_list]
    total = len(units)

    async def _run(client: httpx.AsyncClient) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for i, (kw, dist) in enumerate(units):
            new_pois, truncated = await _collect_keyword(
                client, rotator, keyword=kw, location=dist, default_city=city, seen_ids=seen
            )
            out.extend(new_pois)
            _report(progress_cb, i + 1, total, kw, new_pois, out, truncated, scope=f"{city}·{dist}")
        return out

    return await _run_with_client(http_client, _run)


# ---- 真四叉树：多边形搜索 + 区县 bbox 递归（借鉴 GaoDe-poi-crawler） ----

BBox = tuple[float, float, float, float]  # (min_lng, min_lat, max_lng, max_lat)


def parse_polyline_bbox(polyline: str) -> BBox | None:
    """高德 district polyline（'lng,lat;lng,lat|...'）→ 包围盒。"""
    min_lng = min_lat = max_lng = max_lat = None
    for ring in polyline.split("|"):
        for point in ring.split(";"):
            parts = point.split(",")
            if len(parts) != 2:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            min_lng = lng if min_lng is None else min(min_lng, lng)
            min_lat = lat if min_lat is None else min(min_lat, lat)
            max_lng = lng if max_lng is None else max(max_lng, lng)
            max_lat = lat if max_lat is None else max(max_lat, lat)
    if min_lng is None:
        return None
    return (min_lng, min_lat, max_lng, max_lat)


def _bbox_to_polygon_str(bbox: BBox) -> str:
    min_lng, min_lat, max_lng, max_lat = bbox
    return (
        f"{min_lng},{min_lat}|{max_lng},{min_lat}|{max_lng},{max_lat}|{min_lng},{max_lat}"
    )


def _split_bbox(bbox: BBox) -> list[BBox]:
    """四分：返回 [西北, 东北, 西南, 东南] 四个子包围盒。"""
    min_lng, min_lat, max_lng, max_lat = bbox
    mid_lng = (min_lng + max_lng) / 2
    mid_lat = (min_lat + max_lat) / 2
    return [
        (min_lng, mid_lat, mid_lng, max_lat),  # 西北
        (mid_lng, mid_lat, max_lng, max_lat),  # 东北
        (min_lng, min_lat, mid_lng, mid_lat),  # 西南
        (mid_lng, min_lat, max_lng, mid_lat),  # 东南
    ]


async def fetch_district_bboxes(
    client: httpx.AsyncClient,
    rotator: KeyRotator,
    city: str,
) -> dict[str, BBox]:
    """拉取城市下辖区县的包围盒（district API 的 polyline 字段）。"""
    await _throttle()
    try:
        resp = await client.get(
            AMAP_DISTRICT_URL,
            params={"keywords": city, "subdistrict": "2", "key": rotator.next_key(), "extensions": "all"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("高德 district 查询失败 city=%s: %s", city, exc)
        return {}
    if str(data.get("status", "0")) != "1":
        return {}
    out: dict[str, BBox] = {}
    top = (data.get("districts") or [{}])[0]
    for d in top.get("districts") or []:
        # 地级市下可能再包一层"市辖区"
        children = d.get("districts") or []
        if children and not d.get("polyline"):
            for sub in children:
                if sub.get("polyline"):
                    bbox = parse_polyline_bbox(sub["polyline"])
                    if bbox:
                        out[str(sub.get("name") or "").strip()] = bbox
            continue
        if d.get("polyline"):
            bbox = parse_polyline_bbox(d["polyline"])
            if bbox:
                out[str(d.get("name") or "").strip()] = bbox
    return out


async def collect_city_quadtree(
    city: str,
    districts: Sequence[str],
    keywords: Sequence[str],
    amap_key: str,
    progress_cb: ProgressCB | None = None,
    http_client: httpx.AsyncClient | None = None,
    *,
    district_bboxes: dict[str, BBox] | None = None,
    max_depth: int = MAX_QUADTREE_DEPTH,
    max_per_keyword: int = MAX_PER_KEYWORD,
) -> list[dict]:
    """真四叉树采集：区县级 text 搜索，截断时按区县 bbox 四分递归多边形搜索。

    单元总数 = 关键词 × 区县；截断的单元在深度上限内递归细分，
    保证宽词在大城市不因单关键词 200 条上限漏数据。poi_id 全局去重。
    """
    rotator = KeyRotator(amap_key)
    kw_list = _normalize_keywords(keywords)
    dist_list = [d.strip() for d in districts if d and d.strip()]
    units = [(kw, dist) for kw in kw_list for dist in dist_list]
    total = len(units)

    async def _run(client: httpx.AsyncClient) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        bboxes = district_bboxes if district_bboxes is not None else await fetch_district_bboxes(client, rotator, city)

        async def _quad(kw: str, dist: str, bbox: BBox, depth: int) -> int:
            poly = _bbox_to_polygon_str(bbox)
            new_pois, trunc = await _collect_keyword(
                client, rotator, keyword=kw, location=None, default_city=city,
                seen_ids=seen, max_items=max_per_keyword, polygon=poly,
            )
            out.extend(new_pois)
            if progress_cb:
                progress_cb(0, 0, f"[{city}·{dist}] 「{kw}」多边形深度{depth} 新增 {len(new_pois)} 条")
            if trunc and depth < max_depth:
                sub = 0
                for qb in _split_bbox(bbox):
                    sub += await _quad(kw, dist, qb, depth + 1)
                return sub
            return 1

        done = 0
        for i, (kw, dist) in enumerate(units):
            new_pois, trunc = await _collect_keyword(
                client, rotator, keyword=kw, location=dist, default_city=city,
                seen_ids=seen, max_items=max_per_keyword,
            )
            out.extend(new_pois)
            leaf_units = 1
            if trunc and dist in bboxes:
                leaf_units = 0
                for qb in _split_bbox(bboxes[dist]):
                    leaf_units += await _quad(kw, dist, qb, 1)
            done += leaf_units
            if progress_cb:
                _report(progress_cb, min(i + 1, total), total, kw, new_pois, out, trunc, scope=f"{city}·{dist}")
        return out

    return await _run_with_client(http_client, _run)
