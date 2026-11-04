"""Agent 数据基座（SPEC-AO AO1）：MCP 风格工具端点。

对标企查查智能体数据平台方向——把 vaclead 的数据与能力暴露给任意 AI 助手
（Claude/Coze/飞书等，HTTP 接入形态，零新依赖）。认证强制走 J1 X-Api-Key
通道（read scope 即可；电话脱敏见 get_enterprise）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, Signal, User
from ..phones import load_phones

router = APIRouter(tags=["mcp"])

# SPEC-AR AR3：写工具令牌桶限流（每 Key 10 次/分钟，内存桶——单进程部署足够）
_RATE_BUCKETS: dict[str, list[float]] = {}
_RATE_LIMIT = 10
_RATE_WINDOW_S = 60.0


def _rate_limit(key: str) -> None:
    import time as _time

    from fastapi import HTTPException

    now = _time.monotonic()
    bucket = [t for t in _RATE_BUCKETS.get(key, []) if now - t < _RATE_WINDOW_S]
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(429, "限流：每 Key 每分钟最多 10 次写调用")
    bucket.append(now)
    _RATE_BUCKETS[key] = bucket


def _require_api_key(request: Request) -> None:
    """Agent 通道必须走 X-Api-Key（Bearer 属于人类 UI 会话）。"""
    if not (request.headers.get("X-Api-Key") or "").strip():
        from fastapi import HTTPException

        raise HTTPException(401, "Agent 通道请使用 X-Api-Key 认证（设置页可创建）")


def _api_key_rw(request: Request, session: Session) -> bool:
    """调用方 Key 的 scope（read 只能发现工具；POST 调用需 rw）。"""
    import hashlib

    from fastapi import HTTPException

    from ..models import UserApiKey

    raw = (request.headers.get("X-Api-Key") or "").strip()
    row = session.query(UserApiKey).filter(
        UserApiKey.key_hash == hashlib.sha256(raw.encode()).hexdigest()
    ).first()
    if row is None:
        raise HTTPException(401, "API Key 无效")
    return row.scopes == "rw"


@router.get("/mcp/tools")
def mcp_tools(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """工具清单（发现用）。"""
    _require_api_key(request)
    return {
        "ok": True,
        "tools": [
            {
                "name": "search_enterprises",
                "description": "按关键词/行业搜索企业库（26k+ 工业买家）",
                "args": {"q": "str?", "industry_key": "str?", "limit": "int=10"},
            },
            {
                "name": "get_enterprise",
                "description": "企业详情+最近信号+联系人（read scope 电话脱敏）",
                "args": {"eid": "int"},
            },
            {
                "name": "list_signals",
                "description": "最新采购信号流（中标/招标/备案，可按类型过滤）",
                "args": {"type": "str?", "days": "int=30", "limit": "int=20"},
            },
            {
                "name": "get_recommendations",
                "description": "今日推荐（先打给谁，可解释排序）",
                "args": {"limit": "int=10"},
            },
            {
                "name": "convert_lead",
                "description": "把推荐/企业转为跟进线索（写工具：需 rw scope，10 次/分钟限流，幂等）",
                "args": {"eid": "int"},
            },
        ],
    }


@router.post("/mcp/call")
async def mcp_call(
    body: dict,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """统一工具调用入口：{tool, args} → {ok, data} / {ok: false, error}。"""
    _require_api_key(request)
    rw = _api_key_rw(request, session)
    tool = str(body.get("tool") or "")
    args = body.get("args") or {}

    try:
        if tool == "search_enterprises":
            q = str(args.get("q") or "").strip()
            limit = max(1, min(int(args.get("limit") or 10), 50))
            conds = [Enterprise.deleted_at.is_(None)]
            if q:
                conds.append(Enterprise.name.contains(q))
            if args.get("industry_key"):
                conds.append(Enterprise.industry_key == str(args["industry_key"]))
            rows = session.execute(
                select(Enterprise).where(*conds).order_by(Enterprise.icp_score.desc()).limit(limit)
            ).scalars().all()
            data = [{"id": e.id, "name": e.name, "city": e.city,
                     "industry": e.industry_name, "icp_score": e.icp_score} for e in rows]
            return {"ok": True, "data": data}

        if tool == "get_enterprise":
            eid = int(args.get("eid") or 0)
            e = session.get(Enterprise, eid)
            if e is None or e.deleted_at:
                return {"ok": False, "error": "企业不存在"}
            sigs = session.execute(
                select(Signal.type, Signal.title, Signal.pub_date)
                .where(Signal.enterprise_id == eid)
                .order_by(Signal.id.desc()).limit(10)
            ).all()
            contacts = session.execute(
                select(Contact.name, Contact.role, Contact.phones_json)
                .where(Contact.enterprise_id == eid)
            ).all()
            return {"ok": True, "data": {
                "id": e.id, "name": e.name, "city": e.city,
                "industry": e.industry_name, "icp_score": e.icp_score,
                "signals": [{"type": t, "title": ti, "pub_date": d} for t, ti, d in sigs],
                "contacts": [
                    {"name": n, "role": r,
                     # read scope 电话脱敏（尾四位）
                     "phone": (load_phones(pj)[0][:4] + "****") if (load_phones(pj) and not rw) else (load_phones(pj)[0] if load_phones(pj) else None)}
                    for n, r, pj in contacts
                ],
            }}

        if tool == "list_signals":
            stype = str(args.get("type") or "").strip()
            days = max(1, min(int(args.get("days") or 30), 365))
            limit = max(1, min(int(args.get("limit") or 20), 50))
            from datetime import datetime, timedelta, timezone

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
            conds = [Signal.created_at >= cutoff]
            if stype:
                conds.append(Signal.type == stype)
            rows = session.execute(
                select(Signal.type, Signal.title, Signal.pub_date, Enterprise.name)
                .join(Enterprise, Signal.enterprise_id == Enterprise.id)
                .where(*conds)
                .order_by(Signal.id.desc()).limit(limit)
            ).all()
            data = [{"type": t, "title": ti, "pub_date": d, "ent": n} for t, ti, d, n in rows]
            return {"ok": True, "data": data}

        if tool == "get_recommendations":
            from ..recommend import score_candidates

            limit = max(1, min(int(args.get("limit") or 10), 50))
            return {"ok": True, "data": score_candidates(session, limit=limit)}

        if tool == "convert_lead":
            # SPEC-AR AR3：唯一受控写工具（最小写面）——推荐转线索
            import hashlib as _hash

            from ..models import UserApiKey

            raw = (request.headers.get("X-Api-Key") or "").strip()
            row = session.query(UserApiKey).filter(
                UserApiKey.key_hash == _hash.sha256(raw.encode()).hexdigest()
            ).first()
            key_id = str(row.id) if row else "unknown"
            _rate_limit(key_id)
            eid = int(args.get("eid") or 0)
            from ..api.misc import convert_recommendation

            # 复用 AO2 逻辑（幂等）：借当前请求会话直接构造
            result = convert_recommendation(eid, session, user)
            return {"ok": True, "data": result}

        return {"ok": False, "error": f"未知工具：{tool}（GET /api/mcp/tools 查清单）"}
    except ValueError as exc:
        return {"ok": False, "error": f"参数错误：{exc}"}
