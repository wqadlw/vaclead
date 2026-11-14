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

    def __init__(self, token: str, transport: Any = None) -> None:
        self.token = token
        self._transport = transport  # 测试注入 MockTransport；生产恒 None

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """JSON-RPC tools/call 同通道封装（超时 20s；网络异常返回 None）。"""
        import os

        import httpx

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
            "params": {"name": tool, "arguments": arguments},
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0), transport=self._transport) as client:
                resp = client.post(self.ENDPOINT, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("china-check %s 调用失败: %s", tool, exc)
            return None

    def enrich(self, uscc: str) -> dict[str, Any] | None:
        if not (uscc or "").strip():
            return None
        data = self._call(
            "get_company_snapshot", {"creditCode": uscc.strip()}
        )
        return _parse_chinacheck(data) if data else None

    def search_by_name(self, name: str) -> dict[str, Any] | None:
        """按名搜索（tools.md 的 search_chinese_company，JSON-RPC 同通道）。

        SPEC-MULTI S2：enrich Stage A 按名找 uscc 的免费兜底通道。
        返回 {uscc, name?, legal_rep?}；未命中/异常一律返回 None（兜底
        通道不抛异常——失败只降级不阻塞流水线）。超时 20s。
        """
        if not (name or "").strip():
            return None
        data = self._call("search_chinese_company", {"name": name.strip()})
        return _parse_chinacheck_search(data) if data else None


def _capital_wan(raw: Any) -> float | None:
    """注册资本/实缴资本文本 → 万元数值（"5000万人民币"/"1,200万"/"800" 容错）。"""
    try:
        return float(
            str(raw).replace("万", "").replace("人民币", "").replace("元", "")
            .replace(",", "").strip()
        )
    except ValueError:
        return None


def _parse_chinacheck(data: dict[str, Any]) -> dict[str, Any] | None:
    """MCP 响应 → 标准字段；字段名以 china-check docs/data-fields.md 为准。

    SPEC-MULTI S2 深解析扩展：registrationStatus / legalRepresentative /
    staffSize / paidInCapital / registeredAddress / formerNames——
    原 4 字段（注册资本/经营范围/行业/成立日期）解析行为不变。"""
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
        cap_wan = _capital_wan(cap)
        if cap_wan is not None:
            out["registered_capital_wan"] = cap_wan
    if snap.get("businessScope"):
        out["business_scope"] = str(snap["businessScope"])[:500]
    if snap.get("industry"):
        out["industry"] = str(snap["industry"])[:80]
    if snap.get("estDate") or snap.get("establishDate"):
        out["est_date"] = str(snap.get("estDate") or snap.get("establishDate"))[:10]
    # ---- SPEC-MULTI S2 深解析 ----
    if snap.get("registrationStatus"):
        out["biz_status"] = str(snap["registrationStatus"]).strip()[:20]
    if snap.get("legalRepresentative"):
        out["legal_rep"] = str(snap["legalRepresentative"]).strip()[:60]
    if snap.get("staffSize"):
        out["staff_size"] = str(snap["staffSize"]).strip()[:40]
    paid = snap.get("paidInCapital")
    if paid:
        paid_wan = _capital_wan(paid)
        if paid_wan is not None:
            out["paid_in_capital_wan"] = paid_wan
    if snap.get("registeredAddress"):
        out["address"] = str(snap["registeredAddress"])[:300]
    if isinstance(snap.get("formerNames"), list) and snap["formerNames"]:
        out["former_names"] = [str(n) for n in snap["formerNames"] if n]
    return out or None


def _parse_chinacheck_search(data: dict[str, Any]) -> dict[str, Any] | None:
    """search_chinese_company 响应 → {uscc, name?, legal_rep?}；未命中 None。

    响应形状容错：list 直给 / {list|records|data|items: [...]} / 单对象。
    uscc 取 creditCode/uniscid/uscc 任一；无 uscc 视为未命中。"""
    try:
        content = data["result"]["content"][0]["text"]
        import json as _json

        snap = _json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    rows: list[Any] | None = None
    if isinstance(snap, list):
        rows = snap
    elif isinstance(snap, dict):
        if snap.get("creditCode") or snap.get("uniscid"):
            rows = [snap]
        else:
            for k in ("list", "records", "data", "items"):
                v = snap.get(k)
                if isinstance(v, list) and v:
                    rows = v
                    break
    if not rows or not isinstance(rows[0], dict):
        return None
    first = rows[0]
    uscc = str(
        first.get("creditCode") or first.get("uniscid") or first.get("uscc") or ""
    ).strip()
    if not uscc:
        return None
    out: dict[str, Any] = {"uscc": uscc}
    name = str(first.get("name") or first.get("entname") or "").strip()
    if name:
        out["name"] = name
    rep = str(
        first.get("legalRepresentative") or first.get("legal_rep") or ""
    ).strip()
    if rep:
        out["legal_rep"] = rep
    return out


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


# ---------------------------------------------------------------------------
# SPEC-MULTI S2：解析结果写回（只补空不覆盖 + 字段级 PROV 打标 + biz_dead）
# ---------------------------------------------------------------------------
PROV_SOURCE = "工商补数·chinacheck"
#: 线索死刑状态（注销/吊销/迁出/清算）——打 biz_dead 标签由前端红标警示
BIZ_DEAD_STATES = {"注销", "吊销", "迁出", "清算"}


def apply_gongshang_fields(ent: Any, data: dict[str, Any]) -> list[str]:
    """chinacheck 解析结果写回企业（SPEC-MULTI S2）。

    红线：**只补空不覆盖**（与 GSH 对照表回填同语义）；每个写入字段
    mark_prov(source="工商补数·chinacheck", by="enrich")。biz_status 落
    死刑态时 tags 追加 biz_dead（幂等，不动 intent_score——历史信号仍有效）。
    返回本次实际写入的字段名列表。
    """
    import json as _json

    from ..models import utcnow

    filled: list[str] = []

    def _mark(field: str) -> None:
        ent.mark_prov(field, source=PROV_SOURCE, by="enrich")
        filled.append(field)

    if data.get("registered_capital_wan") is not None and ent.registered_capital_wan is None:
        ent.registered_capital_wan = float(data["registered_capital_wan"])
        _mark("registered_capital_wan")
    for field, key in (
        ("business_scope", "business_scope"),
        ("industry_name", "industry"),
        ("established_at", "est_date"),
        ("legal_rep", "legal_rep"),
        ("staff_size", "staff_size"),
        ("address", "address"),
    ):
        if data.get(key) and not getattr(ent, field, None):
            setattr(ent, field, str(data[key]))
            _mark(field)
    if data.get("paid_in_capital_wan") is not None and ent.paid_in_capital_wan is None:
        ent.paid_in_capital_wan = float(data["paid_in_capital_wan"])
        _mark("paid_in_capital_wan")
    if data.get("biz_status") and not ent.biz_status:
        ent.biz_status = str(data["biz_status"])
        ent.biz_status_at = utcnow()
        _mark("biz_status")
        if ent.biz_status in BIZ_DEAD_STATES:
            try:
                tags = _json.loads(ent.tags or "[]")
            except ValueError:
                tags = []
            if isinstance(tags, list) and "biz_dead" not in tags:
                tags.append("biz_dead")
                ent.tags = _json.dumps(tags, ensure_ascii=False)
    names = data.get("former_names") or []
    if names:
        try:
            have = _json.loads(ent.former_names or "[]")
        except ValueError:
            have = []
        if not have:  # 只补空不覆盖
            merged = [str(n) for n in names if n]
            if merged:
                ent.former_names = _json.dumps(merged, ensure_ascii=False)
                _mark("former_names")
    return filled
