"""拟建项目采集器（SPEC-AB）：广东省投资项目在线审批监管平台备案公示。

- 官方主动公开数据（合规）：单位名称/备案时间/备案编号/年度
- 语义：单位完成项目备案 = 正在扩张/新建 = 设备采购前兆（比招标公告早 1-3 个月）
- 网关前缀 /tzxmspweb 来自站点 /js/common.js（root 变量）
"""
from __future__ import annotations

import time

import httpx

PROPOSAL_LIST_URL = (
    "https://tzxm.gd.gov.cn/tzxmspweb/api/publicityInformation/selectConsutinCpyPage"
)
PROPOSAL_PAGE_URL = (
    "https://tzxm.gd.gov.cn/PublicityInformation/ConsutingCpResultsList.html"
)
REQUEST_INTERVAL_S = 2.0  # 官方平台礼貌限速

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Content-Type": "application/json",
    "Referer": PROPOSAL_PAGE_URL,
}

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    import time as _time

    elapsed = _time.monotonic() - _last_request_at
    if elapsed < REQUEST_INTERVAL_S:
        _time.sleep(REQUEST_INTERVAL_S - elapsed)
    _last_request_at = _time.monotonic()


def fetch_proposal_page(
    page_no: int = 1,
    page_size: int = 20,
    client: httpx.Client | None = None,
) -> list[dict]:
    """拉一页备案公示 → 结构化记录列表（无数据返回空列表）。"""
    global _last_request_at
    _throttle()
    body = {"pageNo": page_no, "pageSize": page_size}
    try:
        if client is not None:
            resp = client.post(PROPOSAL_LIST_URL, headers=_HEADERS, json=body)
        else:
            with httpx.Client(timeout=httpx.Timeout(25.0)) as own:
                resp = own.post(PROPOSAL_LIST_URL, headers=_HEADERS, json=body)
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    payload = resp.json()
    if not isinstance(payload, dict) or payload.get("code") != "0":
        return []
    data = payload.get("data") or {}
    out: list[dict] = []
    for r in data.get("list") or []:
        name = (r.get("deptName") or "").strip()
        if len(name) < 4:
            continue
        out.append({
            "name": name,
            "ba_time": r.get("baTime"),
            "ba_code": r.get("baCode") or "",
            "ba_num": r.get("baNum"),
            "work_time": r.get("workTime"),
        })
    return out
