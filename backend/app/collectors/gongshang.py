"""企业工商信息补全 Provider 协议（SPEC-B §5）。

设计：可插拔 provider——本轮零凭证即可跑 OfflineProvider；
ChinaCheck（免费 MCP，实测已需 token）/阿里云企查（按量付费）适配器
在 settings_kv 配置凭证后自动启用。绝不自建工商库、不爬登录后商业产品。

settings_kv 键：
  gongshang_provider   offline | chinacheck | aliyun_qcc（缺省 offline）
  chinacheck_token     ChinaCheck 凭证
  aliyun_qcc_key       阿里云市场企业信息 API Key
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("vaclead.collectors.gongshang")


class GongshangProvider(Protocol):
    """工商补全协议：uscc → 结构化工商字段（查不到返回 None，不抛异常）。"""

    name: str

    def enrich(self, uscc: str) -> dict[str, Any] | None: ...


class OfflineProvider:
    """库内离线补全：只归整已有字段（当前为 noop 占位，保证协议可测可降级）。"""

    name = "offline"

    def enrich(self, uscc: str) -> dict[str, Any] | None:
        if not (uscc or "").strip():
            return None
        # 无外部源时不发明数据：返回 None 表示无可补字段
        return None


class ChinaCheckProvider:
    """china-check.com 免费工商查询 MCP（2026-10-25 实测 initialize 需 token）。

    配置 chinacheck_token 后启用；接口为 MCP Streamable HTTP JSON-RPC。
    """

    name = "chinacheck"
    ENDPOINT = "https://www.china-check.com/api/mcp/mcp"

    def __init__(self, token: str) -> None:
        self.token = token

    def enrich(self, uscc: str) -> dict[str, Any] | None:
        import os

        import httpx

        if not (uscc or "").strip():
            return None
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(k, None)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_company_snapshot",
                "arguments": {"creditCode": uscc.strip()},
            },
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                resp = client.post(self.ENDPOINT, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("china-check 查询失败 uscc=%s: %s", uscc, exc)
            return None
        return _parse_chinacheck(data)


def _parse_chinacheck(data: dict[str, Any]) -> dict[str, Any] | None:
    """MCP 响应 → 标准字段；字段名以 china-check docs/data-fields.md 为准。"""
    try:
        content = data["result"]["content"][0]["text"]
        import json as _json

        snap = _json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(snap, dict):
        return None
    out: dict[str, Any] = {}
    cap = snap.get("registeredCapital")
    if cap:
        try:
            out["registered_capital_wan"] = float(str(cap).replace("万", "").strip())
        except ValueError:
            pass
    if snap.get("businessScope"):
        out["business_scope"] = str(snap["businessScope"])[:500]
    if snap.get("industry"):
        out["industry"] = str(snap["industry"])[:80]
    if snap.get("estDate") or snap.get("establishDate"):
        out["est_date"] = str(snap.get("estDate") or snap.get("establishDate"))[:10]
    return out or None


class AliyunQccProvider:
    """阿里云市场企业信息 API（按量付费，配置 aliyun_qcc_key 后启用）。骨架存根。"""

    name = "aliyun_qcc"

    def __init__(self, key: str) -> None:
        self.key = key

    def enrich(self, uscc: str) -> dict[str, Any] | None:
        raise NotImplementedError(
            "阿里云企查适配器待开通后按市场文档实现（appCode={}）".format(bool(self.key))
        )


def get_provider(session: Any) -> GongshangProvider:
    """按 settings_kv 选择 provider；未配置凭证自动降级 offline。"""
    from ..models import SettingsKV

    def _setting(key: str) -> str | None:
        row = session.get(SettingsKV, key)
        return row.value if row and row.value else None

    name = (_setting("gongshang_provider") or "offline").strip().lower()
    if name == "chinacheck":
        token = _setting("chinacheck_token")
        if token:
            return ChinaCheckProvider(token)
        logger.info("chinacheck_token 未配置，降级 offline")
    if name == "aliyun_qcc":
        key = _setting("aliyun_qcc_key")
        if key:
            return AliyunQccProvider(key)
    return OfflineProvider()
