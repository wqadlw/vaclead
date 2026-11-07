"""拟建项目采集器（SPEC-BR）：江苏省投资项目在线审批监管平台备案公示。

- 官方主动公开数据（合规）：项目代码/项目名称/审批部门/备案证号/日期
- 语义：项目备案 = 新建/扩产 = 设备采购前兆（比招标公告早 1-3 个月）
- 站点形态：传统 jQuery 表单站（非 SPA）——POST .do 端点即返回 HTML 列表，
  服务端关键词过滤 + 日期倒序（第 1 页=最新），比粤鄂"全量再筛"更省
- 侦察实证（2026-11-07）：全库 40,429 页≈80 万项目；「真空」关键词召回
  干式真空泵扩产/真空镀膜机研发中心等——全是设备买家侧项目
"""
from __future__ import annotations

import re
import time

import httpx

from .proposal_hb import is_residential_noise  # 光伏使用侧噪音（湖北同款实测）

JS_LIST_URL = (
    "https://tzxm.fzggw.jiangsu.gov.cn/portalopenPublicInformation.do"
    "?method=querybeianExamineAll"
)
REQUEST_INTERVAL_S = 1.5  # 官方平台礼貌限速

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": JS_LIST_URL,
}

# 行首项目代码：2611-320826-04-01-651808（年份-区划-级次-类型-流水）
_CODE_RE = re.compile(r"^\d{4}-\d{4,6}-\d{2}-\d{2}-\d+\s*")

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < REQUEST_INTERVAL_S:
        time.sleep(REQUEST_INTERVAL_S - elapsed)
    _last_request_at = time.monotonic()


def _parse_date(raw: str) -> str | None:
    """2026/11/06 → 2026-11-06；异常返回 None（由调用方跳过该字段）。"""
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", (raw or "").strip())
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _parse_rows(html: str) -> list[dict]:
    """从列表 HTML 提取备案行：[项目代码+项目名, 审批部门, 备案证号, 日期]。"""
    import re as _re

    text = _re.sub(r"<script.*?</script>", "", html or "", flags=_re.S)
    out: list[dict] = []
    for tr in _re.findall(r"<tr[^>]*>(.*?)</tr>", text, _re.S):
        cells = [
            _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", c)).strip()
            for c in _re.findall(r"<td[^>]*>(.*?)</td>", tr, _re.S)
        ]
        if len(cells) < 4 or not cells[0]:
            continue
        code_m = _CODE_RE.match(cells[0])
        project = cells[0][code_m.end():].strip() if code_m else cells[0].strip()
        if not project:
            continue
        out.append({
            "code": code_m.group(0).strip() if code_m else "",
            "project": project,
            "approve_org": cells[1].strip(),
            "beian_no": cells[2].strip(),
            "ba_date": _parse_date(cells[3]),
        })
    return out


def fetch_js_beian(
    keyword: str,
    page_no: int = 1,
    page_size: int = 100,
    client: httpx.Client | None = None,
) -> list[dict]:
    """按关键词抓江苏备案公示一页（服务端过滤+日期倒序）。

    返回 [{code, project, approve_org, beian_no, ba_date}]；网络/解析异常
    返回空列表（由调用方记数不中断整轮）。噪音（光伏使用侧）在采集端滤除。
    """
    own = client is None
    if own:
        client = httpx.Client(timeout=20, headers=_HEADERS, verify=True)
    try:
        _throttle()
        r = client.post(
            JS_LIST_URL,
            data={
                "apply_project_name": keyword,
                "pageNo": page_no,
                "pageSize": page_size,
            },
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        return [
            row
            for row in _parse_rows(r.text)
            if not is_residential_noise(row["project"])
        ]
    except Exception:  # noqa: BLE001 — 单页失败不中断整轮
        return []
    finally:
        if own:
            client.close()
