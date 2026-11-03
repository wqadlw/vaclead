"""拟建项目采集器·湖北省（SPEC-AG）：投资项目在线审批监管平台备案查询。

- 官方主动公开数据（合规）：单位名/项目名/申报时间/备案卡号
- 语义：单位完成项目备案 = 新建/扩产 = 设备采购前兆（比招标公告早 1-3 个月）
- 与广东（proposal_gd 单位级）互为镜像；湖北为**项目级**（项目名直接可得）

站点 API 逆向（aPaaS Vue，HPAAS_CUSTOM_WEB）：
- 端点 POST /api/proxy/custom/hb/hb_aiapp/xMod/filing_search/customIndex
- 请求头三件：authtoken: null（匿名）/ compkey: hb / extoken（见下）
- **extoken = ((Date.now() % 1e9 + k*1e9) * 911 + 20170706).toString(36)**，
  k∈{0..9}——app.js axios 拦截器纯客户端时间签名，服务端按时间窗校验；
  伪造随机或过期 token 返回 res=400007
- screenKey 必需（SCR_l4xunf59n2=备案查询）；textSearch 关键词过滤格式
  未打通（400007），采集策略=按 -declaration_date 拉最新 + 本地词表过滤
"""
from __future__ import annotations

import time

import httpx

HB_FILING_URL = (
    "https://tzxm.hubei.gov.cn/api/proxy/custom/hb/hb_aiapp/xMod/filing_search/customIndex"
)
HB_XXGK_URL = "https://tzxm.hubei.gov.cn/xxgk"
REQUEST_INTERVAL_S = 2.0  # 官方平台礼貌限速

_XE = 911
_WE = 20170706
_CE = 1_000_000_000

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Content-Type": "application/json",
    "Referer": HB_XXGK_URL,
}

_last_request_at = 0.0

_SCREEN_FILING = "SCR_l4xunf59n2"

#: 光伏使用侧噪音（W51 真库两轮实测：户用庭院/村组代备案/工商业自投分布式——
#: 光伏项目买组件逆变器，不在工业设备词表客户射程）。区分制造侧：
#: "光伏装备/组件制造"类项目是真前兆，必须保留。
import re as _re

_USE_SIDE = _re.compile(r"发电|电站|分布式|庭院|屋顶|千瓦|[kK][wW]")
_VILLAGE_ADDR = _re.compile(r"[村组]\d+")


def is_residential_noise(project: str) -> bool:
    """光伏使用侧项目识别（采集端过滤，不入库）；制造侧保留。"""
    if not project:
        return False
    if "光伏" not in project and "太阳能" not in project:
        return False
    if _re.search(r"(装备|制造|生产|设备)", project):  # 制造侧（如"光伏装备产业园"）
        return False
    if _USE_SIDE.search(project):
        return True
    return bool(_VILLAGE_ADDR.search(project))


def b36(x: int) -> str:
    """base36 编码（JS Number.prototype.toString(36) 同序：0-9a-z）。"""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if x == 0:
        return "0"
    out = ""
    while x:
        x, r = divmod(x, 36)
        out = digits[r] + out
    return out


def compute_extoken(now_ms: int | None = None, k: int | None = None) -> str:
    """复刻站点 axios 拦截器的 extoken 时间签名（SPEC-AG 逆向成果）。

    ((ms % 1e9 + k*1e9) * 911 + 20170706).toString(36)，k∈{0..9}。
    now_ms/k 可注入用于测试。
    """
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    kk = int(k if k is not None else time.gmtime().tm_sec % 10)
    n = (ms % _CE) + (kk % 10) * _CE
    return b36(n * _XE + _WE)


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < REQUEST_INTERVAL_S:
        time.sleep(REQUEST_INTERVAL_S - elapsed)
    _last_request_at = time.monotonic()


def _payload(page_no: int, page_size: int) -> dict:
    return {
        "switchLoginRequired": "off",
        "countLimit": 1000,
        "screenKey": _SCREEN_FILING,
        "advSearch": {
            "logic": "and",
            "conditions": [
                {"logic": "text", "textSearch": ""},
                {"logic": "and", "conditions": []},
                {"logic": "and", "conditions": []},
            ],
        },
        "fields": ["project_code", "project_name", "unit_name", "declaration_date"],
        "sort": "-declaration_date",
        "pagination": {"page": page_no, "pagesize": page_size},
        "indexHints": [],
    }


def fetch_hb_filing_page(
    page_no: int = 1,
    page_size: int = 20,
    client: httpx.Client | None = None,
) -> list[dict]:
    """拉一页备案查询 → [{unit, project, declared_at, project_code}]。

    无数据/网络失败/非 0 返回空列表（调用方按页 break）。
    """
    global _last_request_at
    _throttle()
    headers = dict(_HEADERS)
    headers["extoken"] = compute_extoken()
    headers["authtoken"] = "null"
    headers["compkey"] = "hb"
    try:
        if client is not None:
            resp = client.post(HB_FILING_URL, headers=headers, json=_payload(page_no, page_size))
        else:
            with httpx.Client(timeout=httpx.Timeout(25.0)) as own:
                resp = own.post(HB_FILING_URL, headers=headers, json=_payload(page_no, page_size))
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    try:
        payload = resp.json()
    except ValueError:
        return []
    if not isinstance(payload, dict) or payload.get("res") != 0:
        return []
    data = payload.get("data") or {}
    out: list[dict] = []
    for r in data.get("list") or []:
        unit = (r.get("unit_name") or "").strip()
        project = (r.get("project_name") or "").strip()
        if len(unit) < 4:
            continue
        if is_residential_noise(project):
            continue
        out.append({
            "unit": unit,
            "project": project,
            "declared_at": r.get("declaration_date"),
            "project_code": r.get("project_code") or "",
        })
    return out
