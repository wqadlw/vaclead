"""拟建项目采集器（SPEC-BS）：浙江省投资项目在线审批监管平台办理结果公示。

- 官方主动公开数据（合规）：项目代码/项目名称/事项名/办理状态/办理部门/时间
- 语义：备案=扩张前兆；规划许可/水保等事项=项目推进中（全生命周期审批节点流）
- 站点形态：JSP+JSON（POST itemList 即得 {itemList, counts}）；
  站点 JS 中验证码逻辑已注释、实测未启用（侦察实证 2026-11-07）
- 独有坑：项目名可能带出让文号前缀（"富政工出【2025】16号XX公司新建…"），
  抽单位前必须剥离
"""
from __future__ import annotations

import re
import time

import httpx

from .proposal_hb import is_residential_noise  # 光伏使用侧噪音（湖北同款实测）

ZJ_LIST_URL = "https://tzxm.zjzwfw.gov.cn/publicannouncement.do?method=itemList"
REQUEST_INTERVAL_S = 1.5  # 官方平台礼貌限速

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://tzxm.zjzwfw.gov.cn/tzxmweb/zwtpages/resultsPublicity/"
    "notice_of_publicity_new.html?page=2",
    "X-Requested-With": "XMLHttpRequest",
}

# 出让文号前缀：富政工出【2025】16号 / 浙发改〔2024〕88号 等
_DOCNO_PREFIX = re.compile(r"^[^，。；\s]{1,12}[〔\[【]\d{4}[〕\]】]\d{1,4}号\s*")

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < REQUEST_INTERVAL_S:
        time.sleep(REQUEST_INTERVAL_S - elapsed)
    _last_request_at = time.monotonic()


def strip_docno_prefix(project: str) -> str:
    """剥离项目名前的出让文号前缀（单位抽取的前置净化）。"""
    return _DOCNO_PREFIX.sub("", (project or "").strip())


def _clean_time(raw: str) -> str | None:
    """2026-10-22 09:14:07.0 → 2026-10-22（ISO 日）。"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", (raw or "").strip())
    return m.group(1) if m else None


def fetch_zj_items(
    keyword: str,
    page_no: int = 1,
    client: httpx.Client | None = None,
) -> list[dict]:
    """按关键词抓浙江办理结果公示一页（JSON，固定 10 行/页）。

    返回 [{deal_code, project, item_name, deal_name, deal_time, dept_name}]；
    网络异常/非 JSON 返回空列表（调用方记数不中断整轮）。噪音在采集端滤除。
    """
    own = client is None
    if own:
        client = httpx.Client(timeout=20, headers=_HEADERS, verify=True)
    try:
        _throttle()
        r = client.post(
            ZJ_LIST_URL,
            data={
                "pageFlag": "1",
                "pageNo": page_no,
                "area_code": "",
                "area_flag": "1",
                "deal_code": "",
                "item_name": keyword,
            },
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        payload = r.json()
        if not isinstance(payload, list) or not payload:
            return []
        out: list[dict] = []
        for it in payload[0].get("itemList") or []:
            project = strip_docno_prefix(str(it.get("apply_project_name") or ""))
            if not project or is_residential_noise(project):
                continue
            out.append({
                "deal_code": str(it.get("deal_code") or ""),
                "project": project,
                "item_name": str(it.get("ITEM_NAME") or ""),
                "deal_name": str(it.get("DEAL_NAME") or ""),
                "deal_time": _clean_time(str(it.get("DEAL_TIME") or "")),
                "dept_name": str(it.get("DEPT_NAME") or ""),
            })
        return out
    except Exception:  # noqa: BLE001 — 单页失败不中断整轮
        return []
    finally:
        if own:
            client.close()
