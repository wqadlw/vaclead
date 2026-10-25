"""采集器离线单元测试 —— 全部使用 httpx.MockTransport，不访问真实网络。

覆盖：
- 高德：正常翻页 / 第二页空 / 超 200 截断 / 多 key 轮询 / status=0 优雅跳过 /
  progress_cb 被调用 / collect_city_by_district 区县粒度
- 知了标讯：正常返回（含分页取尽）/ status 非 0 抛 RuntimeError
- CSV：正常行 / UTF-8 BOM / 缺列跳过并计数 / 日期归一
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.collectors import amap_poi, ccgp, ggzy, tender


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """测试提速：关闭 QPS 节流间隔（不改变生产行为）。"""
    monkeypatch.setattr(amap_poi, "MIN_REQUEST_INTERVAL", 0.0)


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_poi(i: int, prefix: str = "真空设备") -> dict:
    """构造一条高德 poi 节点（location 格式 'lng,lat'）。"""
    return {
        "id": f"B0FF{i:06d}",
        "name": f"苏州{i:03d}{prefix}公司",
        "location": f"120.{i % 10000:04d},31.{(i * 7) % 10000:04d}",
        "address": f"工业园区科技路{i}号",
        "typecode": "170000",
        "pname": "江苏省",
        "cityname": "苏州市",
        "adname": "工业园区",
        "pcode": "320000",
        "citycode": "0512",
        "adcode": "320571",
    }


def amap_page(pois: list[dict], count: int | None = None, status: str = "1") -> httpx.Response:
    ok = status == "1"
    return httpx.Response(200, json={
        "status": status,
        "info": "OK" if ok else "INVALID_USER_KEY",
        "infocode": "10000" if ok else "10001",
        "count": str(count if count is not None else len(pois)),
        "pois": pois,
    })


# ===========================================================================
# 高德 POI 采集
# ===========================================================================
def test_amap_pagination_until_count_exhausted():
    """翻页至 count 用尽：count=30 → 第 1 页 25 条 + 第 2 页 5 条后停止。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        calls.append(params)
        page = int(params["page"])
        pois = [make_poi(i) for i in (range(0, 25) if page == 1 else range(25, 30))]
        return amap_page(pois, count=30)

    progress: list[tuple[int, int, str]] = []

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_pois(
                "苏州", ["真空泵"], "KEY1",
                progress_cb=lambda d, t, log: progress.append((d, t, log)),
                http_client=client,
            )
        finally:
            await client.aclose()

    result = run(main())

    # 翻页行为与请求参数
    assert [c["page"] for c in calls] == ["1", "2"]
    assert calls[0]["keywords"] == "真空泵"
    assert calls[0]["city"] == "苏州"
    assert calls[0]["citylimit"] == "true"
    assert calls[0]["offset"] == "25"
    assert calls[0]["key"] == "KEY1"

    # 结果与字段解析（location 拆 lat/lng）
    assert len(result) == 30
    first = result[0]
    assert first["poi_id"] == "B0FF000000"
    assert first["name"] == "苏州000真空设备公司"
    assert first["address"] == "工业园区科技路0号"
    assert first["typecode"] == "170000"
    assert first["city"] == "苏州市"
    assert first["district"] == "工业园区"
    assert first["lng"] == 120.0
    assert first["lat"] == 31.0
    assert len({p["poi_id"] for p in result}) == 30  # 去重

    # progress_cb 被调用：done/total 推进、日志含数量
    assert progress and progress[-1][0] == 1 and progress[-1][1] == 1
    assert "30" in progress[-1][2]


def test_amap_second_page_empty_stops_gracefully():
    """第二页空（count 尚未用尽）：优雅停止，不抛错。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        calls.append(params)
        if int(params["page"]) == 1:
            return amap_page([make_poi(i) for i in range(25)], count=40)
        return amap_page([], count=40)  # 第二页空

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_pois("苏州", ["真空泵"], "KEY1", http_client=client)
        finally:
            await client.aclose()

    result = run(main())
    assert len(result) == 25
    assert [c["page"] for c in calls] == ["1", "2"]


def test_amap_truncates_at_200():
    """单关键词超 200 条截断：count=999 恒有数据 → 8 页 × 25 = 200 即停。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        calls.append(params)
        page = int(params["page"])
        pois = [make_poi(page * 1000 + i) for i in range(25)]
        return amap_page(pois, count=999)

    progress: list[tuple[int, int, str]] = []

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_pois(
                "苏州", ["真空泵"], "KEY1",
                progress_cb=lambda d, t, log: progress.append((d, t, log)),
                http_client=client,
            )
        finally:
            await client.aclose()

    result = run(main())
    assert len(result) == 200
    assert len(calls) == 8  # 200 / 25
    assert "上限" in progress[-1][2]  # 截断在日志中提示区县细化


def test_amap_multi_key_rotation():
    """逗号分隔多 key 轮询：两个 key 都被使用过。"""
    keys_used: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        keys_used.append(params["key"])
        return amap_page([make_poi(len(keys_used))], count=1)

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_pois(
                "苏州", ["真空泵", "冻干机"], "KEY_A,KEY_B", http_client=client
            )
        finally:
            await client.aclose()

    result = run(main())
    assert len(result) == 2
    assert set(keys_used) == {"KEY_A", "KEY_B"}  # 两个 key 都被用过
    assert keys_used == ["KEY_A", "KEY_B"]       # 依次轮转


def test_amap_status_zero_skipped_gracefully():
    """status=0：优雅跳过（返回空列表、不抛错），progress_cb 仍被调用。"""
    progress: list[tuple[int, int, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "0", "info": "INVALID_USER_KEY", "infocode": "10001",
            "count": "0", "pois": [],
        })

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_pois(
                "苏州", ["真空泵"], "BAD_KEY",
                progress_cb=lambda d, t, log: progress.append((d, t, log)),
                http_client=client,
            )
        finally:
            await client.aclose()

    result = run(main())
    assert result == []
    assert len(progress) == 1
    assert progress[0][0] == 1 and progress[0][1] == 1


def test_collect_city_by_district_uses_district_as_city():
    """区县粒度细化：city 参数换成区县名，跨区县按 poi_id 去重。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        calls.append(params)
        district = params["city"]
        if district == "工业园区":
            pois = [make_poi(1), make_poi(2)]
        else:  # 吴中区与工业园区有一条同 id（重复公告/连锁店场景）
            pois = [make_poi(2), make_poi(3)]
        return amap_page(pois, count=len(pois))

    progress: list[tuple[int, int, str]] = []

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_city_by_district(
                "苏州", ["工业园区", "吴中区"], ["真空泵"], "KEY1",
                progress_cb=lambda d, t, log: progress.append((d, t, log)),
                http_client=client,
            )
        finally:
            await client.aclose()

    result = run(main())
    # city 参数依次为区县名（而非城市名）
    assert [c["city"] for c in calls] == ["工业园区", "吴中区"]
    # 跨区县去重：1、2、3 各一条
    assert sorted(p["poi_id"] for p in result) == ["B0FF000001", "B0FF000002", "B0FF000003"]
    # 进度以（关键词 × 区县）单元计
    assert [(d, t) for d, t, _ in progress] == [(1, 2), (2, 2)]


# ===========================================================================
# 知了标讯 API
# ===========================================================================
def test_zhiliaoke_fetch_and_pagination():
    """正常返回 + 分页取尽；校验 POST JSON 请求体字段。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content.decode("utf-8")))
        page = calls[-1]["page"]
        items = [
            {"title": "真空泵采购公告", "content": "采购真空机组 2 套",
             "url": "https://b.example/1", "publishTime": "2026-10-20 09:30:00"},
            {"title": "冻干机维保招标" + str(page), "content": "冷冻干燥设备维保",
             "url": f"https://b.example/{page}-2", "createTime": "2026/10/21"},
        ]
        return httpx.Response(200, json={"status": 0, "message": "ok",
                                         "data": {"total": 4, "list": items}})

    async def main():
        client = mock_client(handler)
        try:
            return await tender.fetch_zhiliaoke_tenders(
                ["真空泵"], 30, "ZK_KEY", http_client=client
            )
        finally:
            await client.aclose()

    rows = run(main())
    # 第 1 页 2 条 + 第 2 页 2 条 = 4 条；total=4 取尽后停（无第 3 页）
    assert len(rows) == 4
    assert len(calls) == 2
    assert calls[0] == {"keyword": "真空泵", "page": 1, "days": 30, "key": "ZK_KEY"}
    assert calls[1]["page"] == 2
    # 字段映射 + 日期归一
    assert rows[0] == {
        "title": "真空泵采购公告",
        "content": "采购真空机组 2 套",
        "url": "https://b.example/1",
        "pub_date": "2026-10-20",
    }
    assert rows[1]["pub_date"] == "2026-10-21"


def test_zhiliaoke_error_status_raises():
    """status 非 0 → RuntimeError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 1, "message": "key 无效"})

    async def main():
        client = mock_client(handler)
        try:
            return await tender.fetch_zhiliaoke_tenders(
                ["真空泵"], 7, "BAD", http_client=client
            )
        finally:
            await client.aclose()

    with pytest.raises(RuntimeError, match="知了标讯"):
        run(main())


# ===========================================================================
# CSV 导入解析
# ===========================================================================
def test_csv_parse_normal_rows():
    body = (
        "title,content,url,pub_date,enterprise_name\r\n"
        "真空泵采购项目,需采购真空泵 2 台,https://t/1,2026-10-20,苏州XX半导体有限公司\r\n"
        "冻干车间扩建,冷冻干燥设备一批,https://t/2,2026-10-21,\r\n"
    ).encode("utf-8")
    rows = tender.parse_tender_csv(body)
    assert len(rows) == 2
    assert rows[0] == {
        "title": "真空泵采购项目",
        "content": "需采购真空泵 2 台",
        "url": "https://t/1",
        "pub_date": "2026-10-20",
        "enterprise_name": "苏州XX半导体有限公司",
    }
    assert rows[1]["enterprise_name"] == ""  # 可选列缺省为空串
    assert rows[1]["pub_date"] == "2026-10-21"


def test_csv_with_utf8_bom():
    """UTF-8 BOM 头不影响表头解析。"""
    body = "\ufefftitle,content,url,pub_date\n真空烧结炉采购,内容,https://t/3,2026-10-22\n".encode("utf-8")
    rows = tender.parse_tender_csv(body)
    assert len(rows) == 1
    assert rows[0]["title"] == "真空烧结炉采购"
    assert rows[0]["pub_date"] == "2026-10-22"


def test_csv_skips_bad_rows_and_counts():
    """缺 title / 缺 pub_date / 日期无法归一的行跳过并计数。"""
    stats: dict = {}
    body = (
        "title,content,url,pub_date\r\n"
        "正常公告,内容,https://t/4,2026-10-01\r\n"
        ",缺标题,https://t/5,2026-10-02\r\n"
        "缺日期公告,内容,https://t/6,\r\n"
        "坏日期公告,内容,https://t/7,not-a-date\r\n"
    ).encode("utf-8")
    rows = tender.parse_tender_csv(body, stats=stats)
    assert len(rows) == 1
    assert rows[0]["title"] == "正常公告"
    assert stats == {"total": 4, "parsed": 1, "skipped": 3}


def test_csv_date_normalization():
    body = (
        "title,pub_date\r\n"
        "A,2026.3.5\r\n"
        "B,2026年10月24日\r\n"
        "C,2026/10/24 12:30:00\r\n"
        "D,2026-10-24T09:00:00\r\n"
        "E,20261024\r\n"
    ).encode("utf-8")
    rows = tender.parse_tender_csv(body)
    assert [r["pub_date"] for r in rows] == [
        "2026-03-05", "2026-10-24", "2026-10-24", "2026-10-24", "2026-10-24",
    ]


def test_amap_empty_keyword_list_returns_empty():
    """空关键词列表：不发请求，返回空。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return amap_page([])

    async def main():
        client = mock_client(handler)
        try:
            return await amap_poi.collect_pois("苏州", [], "KEY1", http_client=client)
        finally:
            await client.aclose()

    result = run(main())
    assert result == []
    assert calls == []


# ---- 真四叉树（多边形搜索 + 区县 bbox 递归，借鉴 GaoDe-poi-crawler） ----

def test_parse_polyline_bbox():
    bbox = amap_poi.parse_polyline_bbox("120.5,31.1;120.9,31.1|120.5,31.5;120.9,31.5")
    assert bbox == (120.5, 31.1, 120.9, 31.5)
    assert amap_poi.parse_polyline_bbox("") is None


def test_split_bbox_corners():
    quads = amap_poi._split_bbox((120.0, 31.0, 121.0, 32.0))
    assert len(quads) == 4
    assert (120.0, 31.5, 120.5, 32.0) in quads  # 西北
    poly = amap_poi._bbox_to_polygon_str(quads[0])
    assert poly.count("|") == 3 and "120.0,31.5" in poly


def test_quadtree_recurses_on_truncation():
    """城市 text 截断 → 区县 bbox 四分 → 多边形搜索兜底。"""
    calls = {"text": 0, "polygon": 0, "district": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "config/district" in url:
            calls["district"] += 1
            return httpx.Response(200, json={
                "status": "1",
                "districts": [{"name": "苏州市", "districts": [
                    {"name": "虎丘区", "polyline": "120.4,31.2;120.6,31.2|120.4,31.4;120.6,31.4"},
                ]}],
            })
        if "place/text" in url:
            calls["text"] += 1
            page = int(request.url.params.get("page", "1"))
            pois = [
                {"id": f"t{page}_{i}", "name": f"苏州企{page}_{i}", "location": "120.5,31.3", "adname": "虎丘区"}
                for i in range(25)
            ]
            return httpx.Response(200, json={"status": "1", "count": "400", "pois": pois})
        if "place/polygon" in url:
            calls["polygon"] += 1
            pois = [
                {"id": f"p{calls['polygon']}_{i}", "name": f"多边形企{calls['polygon']}_{i}", "location": "120.5,31.3", "adname": "虎丘区"}
                for i in range(5)
            ]
            return httpx.Response(200, json={"status": "1", "count": "5", "pois": pois})
        return httpx.Response(200, json={"status": "0"})

    transport = httpx.MockTransport(handler)

    async def main():
        client = httpx.AsyncClient(transport=transport)
        try:
            return await amap_poi.collect_city_quadtree(
                "苏州市", ["虎丘区"], ["真空泵"], "KEY_A",
                http_client=client,
                max_per_keyword=25,
            )
        finally:
            await client.aclose()

    out = run(main())
    assert calls["district"] == 1
    assert calls["text"] == 1          # 25 条上限：第一页即截断
    assert calls["polygon"] >= 4       # bbox 至少四分一次
    names = {p["name"] for p in out}
    assert any(n.startswith("苏州企") for n in names)
    assert any(n.startswith("多边形企") for n in names)


# ===========================================================================
# 官方中标数据（data.ggzy.gov.cn）—— docs/DATA_ACQUISITION.md 契约
# ===========================================================================
def _ggzy_client(monkeypatch, handler):
    """构造 MockTransport 注入的 ggzy client 并关闭限速。"""
    monkeypatch.setattr(ggzy, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(ggzy, "_last_request_at", 0.0)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ggzy_search_entities_shape(monkeypatch):
    """search_entities 字段透传 + 分页参数校验。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.params["pageNo"] == "1"
        assert request.url.params["pageSize"] == "50"
        return httpx.Response(200, json={
            "success": True, "code": 200,
            "result": {
                "records": [{
                    "uniscid": "91320924MA1T6WGX7T",
                    "estdate": 1509292800000,  # epoch ms 浮点字符串形式
                    "entname": "盐城市视远真空设备有限公司",
                    "name": "黄耀锋", "bidCount": 1,
                }],
                "total": 49, "size": 50, "current": 1, "pages": 1,
            },
        })

    client = _ggzy_client(monkeypatch, handler)
    try:
        result = ggzy.search_entities("真空设备", client=client)
    finally:
        client.close()
    assert result["total"] == 49
    rec = ggzy.normalize_entity_record(result["records"][0])
    assert rec == {
        "uscc": "91320924MA1T6WGX7T",
        "name": "盐城市视远真空设备有限公司",
        "legal_rep": "黄耀锋",
        "est_date": "2017-10-30",
        "bid_count": 1,
    }
    assert "data.ggzy.gov.cn" in calls[0]
    assert "yjcx/index/search" in calls[0]


def test_ggzy_search_all_paginates(monkeypatch):
    """pages=2 → 逐页拉全；第 2 页空则停。"""
    state = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["page"] = int(request.url.params["pageNo"])
        if state["page"] == 1:
            recs = [{"uniscid": f"U{i}", "entname": f"真空设备厂{i}", "bidCount": 1}
                    for i in range(2)]
            return httpx.Response(200, json={"success": True,
                                             "result": {"records": recs, "total": 3, "pages": 2}})
        return httpx.Response(200, json={"success": True,
                                         "result": {"records": [], "total": 3, "pages": 2}})

    client = _ggzy_client(monkeypatch, handler)
    try:
        rows = list(ggzy.search_all_entities("真空", max_pages=5, client=client))
    finally:
        client.close()
    assert len(rows) == 2
    assert state["page"] == 2  # 第 2 页空后停


def test_ggzy_bid_list_and_normalization(monkeypatch):
    """bid_list 明细归一：金额转 float、日期截断、空项目名丢弃。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "yjcx/index/bid_list" in str(request.url)
        assert request.url.params["uniscid"] == "U1"
        return httpx.Response(200, json={"success": True, "result": {"data": {
            "records": [{
                "id": "ABC123", "projectName": "某高校真空镀膜机采购项目成交公告",
                "bidPrice": "453.34", "areaCode": "射阳县", "tos": "土地使用权",
                "tenderOrgName": "射阳县自然资源局", "createTime": "2020-01-10",
            }, {"id": "X", "projectName": "", "bidPrice": "1"}],  # 无项目名 → 丢
            "total": 1, "size": 50, "current": 1, "pages": 1,
        }}})

    client = _ggzy_client(monkeypatch, handler)
    try:
        result = ggzy.list_bids("U1", client=client)
    finally:
        client.close()
    bids = [ggzy.normalize_bid_record(r) for r in result["records"]]
    assert bids[0] == {
        "project_name": "某高校真空镀膜机采购项目成交公告",
        "bid_price_wan": 453.34,
        "area": "射阳县",
        "trade_type": "土地使用权",
        "tender_org": "射阳县自然资源局",
        "tender_agency": None,
        "pub_date": "2020-01-10",
        "deal_id": "ABC123",
    }
    assert len([b for b in bids if b]) == 1


def test_ggzy_retry_on_5xx(monkeypatch):
    """500 一次后重试成功。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"success": True,
                                         "result": {"records": [{"entname": "恢复真空设备公司"}],
                                                    "total": 1, "pages": 1}})

    client = _ggzy_client(monkeypatch, handler)
    try:
        result = ggzy.search_entities("真空", client=client)
    finally:
        client.close()
    assert state["n"] == 2
    assert result["total"] == 1


def test_ggzy_business_failure_returns_empty(monkeypatch):
    """success=false → 空结果不抛异常。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "参数错误"})

    client = _ggzy_client(monkeypatch, handler)
    try:
        result = ggzy.search_entities("x", client=client)
    finally:
        client.close()
    assert result == {"records": [], "total": 0, "pages": 0}


def test_ggzy_empty_uscc_short_circuits(monkeypatch):
    """空 uniscid 不发请求。"""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发请求")

    assert ggzy.list_bids("  ") == {"records": [], "total": 0, "pages": 0}


# ===========================================================================
# 政采网（search.ccgp.gov.cn/bxsearch）—— 吸收自 refs/CrawlerForCCGP
# ===========================================================================
def _ccgp_client(monkeypatch, handler):
    monkeypatch.setattr(ccgp, "REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(ccgp, "INTERVAL_JITTER_S", 0.0)
    monkeypatch.setattr(ccgp, "_last_request_at", 0.0)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ccgp_fixture_html():
    from pathlib import Path

    p = Path(__file__).parent / "fixtures_ccgp_list.html"
    if not p.exists():
        pytest.skip("无 ccgp 真实样本 fixture")
    return p.read_text(encoding="utf-8")


def test_ccgp_parse_real_fixture():
    """真实搜索结果页样本：20 条全解析，采购人/类型/省份/日期齐全。"""
    from app.collectors.ccgp import parse_list_html

    items = parse_list_html(_ccgp_fixture_html())
    assert len(items) == 20
    first = items[0]
    assert "真空室中标公告" in first["title"]
    assert first["buyer"] == "中国科学技术大学"
    assert first["notice_type"] == "中标公告"
    assert first["pub_date"] == "2026-10-23"
    assert first["url"].startswith("http://www.ccgp.gov.cn/")
    # 摘要净化（无标签）
    assert "<" not in first["summary"]
    # 磋商公告类型识别
    assert any(it["notice_type"] == "竞争性磋商公告" for it in items)


def test_ccgp_search_via_mock(monkeypatch):
    """search_ccgp 请求参数与 HTML 解析联动（MockTransport）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.params["kw"] == "真空"
        assert request.url.params["searchtype"] == "1"
        assert "search.ccgp.gov.cn" in str(request.url)
        return httpx.Response(200, text=_ccgp_fixture_html())

    client = _ccgp_client(monkeypatch, handler)
    try:
        items = ccgp.search_ccgp("真空", page=1, days=90, client=client)
    finally:
        client.close()
    assert len(items) == 20
    assert len(calls) == 1


def test_ccgp_empty_page_returns_no_items(monkeypatch):
    """结果列表为空壳（无 ul）→ 空列表不抛异常。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>无数据</body></html>")

    client = _ccgp_client(monkeypatch, handler)
    try:
        items = ccgp.search_ccgp("真空镀膜机", client=client)
    finally:
        client.close()
    assert items == []


def test_ccgp_retry_on_non200(monkeypatch):
    """非 200 → 重试后成功。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=_ccgp_fixture_html())

    client = _ccgp_client(monkeypatch, handler)
    try:
        items = ccgp.search_ccgp("真空", client=client)
    finally:
        client.close()
    assert state["n"] == 2
    assert len(items) == 20


def test_ccgp_all_paginates(monkeypatch):
    """max_pages=2 → 第二页空即停。"""
    state = {"pages": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["pages"] = int(request.url.params["page_index"])
        if state["pages"] == 1:
            return httpx.Response(200, text=_ccgp_fixture_html())
        return httpx.Response(200, text="<html>空</html>")

    client = _ccgp_client(monkeypatch, handler)
    try:
        rows = list(ccgp.search_ccgp_all("真空", max_pages=2, client=client))
    finally:
        client.close()
    assert state["pages"] == 2
    assert len(rows) == 20
