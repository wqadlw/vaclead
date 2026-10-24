"""采集任务路由：POI 拓客 / 招标扫描 / SSE 进度流。"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import admin_only, current_user
from ..db import SessionLocal, get_session
from ..models import Enterprise, SettingsKV, TaskCheckpoint, User, utcnow
from ..vertical import detect_industry, downstream_industries, poi_cities

from .imports import apply_icp, recompute_intent

router = APIRouter(tags=["tasks"])

# ---- SSE 进程内推送（学自 AI_Find_Customer sse.py/routes.py）----
# 任务线程回调通过 loop.call_soon_threadsafe 广播到订阅队列；
# checkpoint 轮询保留作断线兜底与重启恢复。
_TASK_QUEUES: dict[str, list[asyncio.Queue]] = {}
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
_TASK_LOGS: dict[str, list[str]] = {}
_TASK_EVENTS: dict[str, list[dict]] = {}


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """lifespan 启动时记录主事件循环，供任务线程广播。"""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def _broadcast(task_id: str, event: dict) -> None:
    """把事件推给该任务的所有订阅者（线程安全，无订阅者时静默）。"""
    _TASK_EVENTS.setdefault(task_id, []).append(event)
    if len(_TASK_EVENTS[task_id]) > 500:
        del _TASK_EVENTS[task_id][:200]
    if _MAIN_LOOP is None:
        return
    for q in list(_TASK_QUEUES.get(task_id, [])):
        try:
            _MAIN_LOOP.call_soon_threadsafe(q.put_nowait, event)
        except RuntimeError:
            pass  # 主循环已关闭


def _log(task_id: str, log: str) -> None:
    _TASK_LOGS.setdefault(task_id, []).append(log)
    if len(_TASK_LOGS[task_id]) > 300:
        del _TASK_LOGS[task_id][:100]
    _broadcast(task_id, {"type": "progress", "progress": None, "log": log})


def mark_task_cancelled(task_id: str) -> bool:
    """协作式取消标记；执行线程周期检查。"""
    with SessionLocal() as s:
        cp = s.get(TaskCheckpoint, task_id)
        if cp is None or cp.status not in ("running", "queued"):
            return False
        cp.status = "cancelling"
        cp.updated_at = utcnow()
        s.commit()
    _broadcast(task_id, {"type": "log", "log": "取消请求已受理，等待执行停止…"})
    return True


def is_cancel_requested(task_id: str) -> bool:
    with SessionLocal() as s:
        cp = s.get(TaskCheckpoint, task_id)
        return bool(cp and cp.status == "cancelling")


def _get_setting(session: Session, key: str) -> str | None:
    row = session.get(SettingsKV, key)
    return row.value if row and row.value else None


def _checkpoint(task_id: str, **fields) -> None:
    with SessionLocal() as s:
        cp = s.get(TaskCheckpoint, task_id)
        if cp is None:
            cp = TaskCheckpoint(task_id=task_id, kind=fields.get("kind", "poi"))
            s.add(cp)
        for k, v in fields.items():
            setattr(cp, k, v)
        cp.updated_at = utcnow()
        s.commit()


class PoiTaskIn(BaseModel):
    city: str
    industry_keys: list[str]
    keywords_override: list[str] | None = None
    districts: list[str] | None = None  # 给定时启用真四叉树（区县 bbox 递归）


class TaskCancelled(Exception):
    """协作式取消信号（学自 AI_Find_Customer JobCancelledError）。"""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"task {task_id} cancelled")
        self.task_id = task_id


def _run_poi_task(task_id: str, city: str, keywords: list[str], amap_key: str, districts: list[str] | None = None) -> None:
    """后台线程：采集 → 归一入库 → ICP 打分。进度写 checkpoint。"""
    import asyncio

    from ..collectors.amap_poi import collect_city_quadtree, collect_pois
    from ..normalize import resolve_enterprise

    async def _main() -> None:
        inserted = merged = 0
        logs: list[str] = []

        def cb(done: int, total: int, log: str) -> None:
            if done and total:
                pct = int(done / total * 80)
                _checkpoint(task_id, progress=max(pct, 5), state_json=json.dumps({"done": done, "total": total}, ensure_ascii=False))
                _broadcast(task_id, {"type": "progress", "progress": max(pct, 5), "log": log})
            _log(task_id, log)
            logs.append(log)
            if is_cancel_requested(task_id):
                raise TaskCancelled(task_id)

        if districts:
            pois = await collect_city_quadtree(city, districts, keywords, amap_key, progress_cb=cb)
        else:
            pois = await collect_pois(city, keywords, amap_key, progress_cb=cb)
        with SessionLocal() as s:
            for i, poi in enumerate(pois):
                if (i + 1) % 20 == 0 and is_cancel_requested(task_id):
                    raise TaskCancelled(task_id)
                ent, is_new = resolve_enterprise(s, poi["name"], source="poi")
                ent, is_new = resolve_enterprise(s, poi["name"], source="poi")
                if is_new:
                    inserted += 1
                else:
                    merged += 1
                ent.address = ent.address or poi.get("address")
                ent.city = ent.city or city
                ent.district = ent.district or poi.get("district")
                try:
                    ent.lat = ent.lat if ent.lat is not None else float(poi.get("lat") or 0) or None
                    ent.lng = ent.lng if ent.lng is not None else float(poi.get("lng") or 0) or None
                except (TypeError, ValueError):
                    pass
                ent.poi_id = ent.poi_id or poi.get("poi_id")
                ind = detect_industry(f"{poi['name']} {poi.get('address') or ''}")
                if ind is not None and ent.industry_key is None:
                    ent.industry_key = ind["key"]
                    ent.industry_name = ind["name"]
                apply_icp(ent)
                if (i + 1) % 50 == 0 or i == len(pois) - 1:
                    _checkpoint(
                        task_id,
                        progress=80 + int((i + 1) / max(len(pois), 1) * 19),
                        state_json=json.dumps({"inserted": inserted, "merged": merged}, ensure_ascii=False),
                    )
            s.commit()
        _checkpoint(
            task_id,
            status="done",
            progress=100,
            state_json=json.dumps(
                {"inserted": inserted, "merged": merged, "logs": logs[-20:]}, ensure_ascii=False
            ),
        )
        _broadcast(task_id, {"type": "done", "inserted": inserted, "merged": merged})

    try:
        asyncio.run(_main())
    except TaskCancelled:
        _checkpoint(
            task_id,
            status="cancelled",
            state_json=json.dumps({"logs": logs[-20:]}, ensure_ascii=False),
        )
        _broadcast(task_id, {"type": "done", "cancelled": True})
    except Exception as exc:  # noqa: BLE001 — 后台任务需兜底记录
        _checkpoint(task_id, status="error", state_json=json.dumps({"error": str(exc)}, ensure_ascii=False))
        _broadcast(task_id, {"type": "error", "message": str(exc)})


@router.post("/tasks/poi")
def start_poi_task(
    body: PoiTaskIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    if body.city not in poi_cities():
        raise HTTPException(422, f"城市不在支持列表：{body.city}")
    amap_key = _get_setting(session, "amap_key")
    if not amap_key:
        raise HTTPException(400, "缺少高德Key，请在设置页配置")
    keywords = body.keywords_override
    if not keywords:
        keywords = []
        for ind in downstream_industries():
            if ind["key"] in body.industry_keys:
                keywords.extend(ind["keywords"][:4])
    if not keywords:
        raise HTTPException(422, "未选择行业且无自定义关键词")
    task_id = uuid.uuid4().hex[:12]
    cp = TaskCheckpoint(task_id=task_id, kind="poi", status="running", progress=0)
    session.add(cp)
    session.commit()

    districts: list[str] | None = None
    if body.districts:
        # “全部区县”→ 用 district API 解析真实区县名；解析失败退化为城市级
        districts = _resolve_districts(body.city, amap_key) or None
        _log(task_id, f"四叉树细分：解析到 {len(districts or [])} 个区县")

    threading.Thread(
        target=_run_poi_task,
        args=(task_id, body.city, keywords[:20], amap_key, districts),
        daemon=True,
    ).start()
    return {"task_id": task_id}


def _resolve_districts(city: str, amap_key: str) -> list[str]:
    """把“全部区县”占位解析为该市真实区县名列表（district API，一次拉取）。"""
    import asyncio

    import httpx as _httpx

    from ..collectors.amap_poi import KeyRotator, fetch_district_bboxes

    async def _fetch():
        client = _httpx.AsyncClient(timeout=15.0)
        try:
            rotator = KeyRotator(amap_key)
            bboxes = await fetch_district_bboxes(client, rotator, city)
            return [k for k in bboxes.keys() if k and k != city]
        finally:
            await client.aclose()

    try:
        return asyncio.run(_fetch())
    except Exception:  # noqa: BLE001 — 解析失败则退化为城市级采集
        return []


class TenderScanIn(BaseModel):
    days: int = 7


@router.post("/tasks/tender-scan")
def start_tender_scan(
    body: TenderScanIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    key = _get_setting(session, "zhiliaoke_key")
    if not key:
        raise HTTPException(400, "缺少知了标讯Key，请使用CSV导入或在设置页配置")
    from ..vertical import device_strong_terms

    task_id = uuid.uuid4().hex[:12]
    session.add(TaskCheckpoint(task_id=task_id, kind="tender", status="running", progress=0))
    session.commit()

    def _worker() -> None:
        import asyncio

        from ..collectors.tender import fetch_zhiliaoke_tenders
        from .imports import _COMPANY_RE  # noqa: PLC2701

        async def _main() -> None:
            posts = await fetch_zhiliaoke_tenders(device_strong_terms()[:6], body.days, key)
            created = 0
            with SessionLocal() as s:
                from ..models import Signal
                from ..normalize import resolve_enterprise

                for p in posts:
                    title = p.get("title", "")
                    content = p.get("content", "")
                    hit_terms = [t for t in device_strong_terms() if t in title or t in content]
                    if not hit_terms:
                        continue
                    name = _COMPANY_RE.search(title) or _COMPANY_RE.search(content)
                    if not name:
                        continue
                    ent, _ = resolve_enterprise(s, name.group(1), source="tender")
                    exists = (
                        s.query(Signal)
                        .filter(Signal.url == p.get("url", ""), Signal.enterprise_id == ent.id)
                        .first()
                    )
                    if exists is None:
                        s.add(
                            Signal(
                                enterprise_id=ent.id,
                                type="tender",
                                title=title,
                                url=p.get("url", ""),
                                pub_date=p.get("pub_date"),
                                matched_terms=json.dumps(hit_terms, ensure_ascii=False),
                                hit_part="title" if any(t in title for t in hit_terms) else "body",
                                raw_excerpt=content[:200],
                                base_score=70.0 if any(t in title for t in hit_terms) else 50.0,
                            )
                        )
                        created += 1
                s.commit()
                _checkpoint(task_id, status="done", progress=100,
                            state_json=json.dumps({"signals_created": created}, ensure_ascii=False))

        asyncio.run(_main())

    threading.Thread(target=_worker, daemon=True).start()
    return {"task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    cp = session.get(TaskCheckpoint, task_id)
    if cp is None:
        raise HTTPException(404, "任务不存在")
    if cp.status not in ("running", "queued", "cancelling"):
        raise HTTPException(422, f"任务状态为 {cp.status}，无法取消")
    mark_task_cancelled(task_id)
    return {"ok": True, "status": "cancelling"}


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    cp = session.get(TaskCheckpoint, task_id)
    if cp is None:
        raise HTTPException(404, "任务不存在")

    queue: asyncio.Queue = asyncio.Queue()
    _TASK_QUEUES.setdefault(task_id, []).append(queue)

    async def gen() -> AsyncIterator[bytes]:
        try:
            # 1) 快照：当前状态 + 历史事件回放（晚加入不丢上下文）
            with SessionLocal() as s:
                cur = s.get(TaskCheckpoint, task_id)
            state = json.loads(cur.state_json or "{}") if cur else {}
            snap = {
                "type": "snapshot",
                "progress": cur.progress if cur else 0,
                "status": cur.status if cur else "unknown",
                "log": (state.get("log") or "") if isinstance(state, dict) else "",
            }
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n".encode()
            for ev in _TASK_EVENTS.get(task_id, []):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
            # 已结束：补终态即收
            if cur and cur.status in ("done", "error", "cancelled"):
                yield f"data: {json.dumps({'type': 'done' if cur.status == 'done' else 'error', 'message': state.get('error', '') if isinstance(state, dict) else ''}, ensure_ascii=False)}\n\n".encode()
                return
            # 2) 推送主通道：30s 无事件发 heartbeat 保活
            while True:
                with SessionLocal() as s:
                    cur = s.get(TaskCheckpoint, task_id)
                if cur is None or cur.status in ("done", "error", "cancelled"):
                    yield f"data: {json.dumps({'type': 'done' if (cur and cur.status == 'done') else 'error', 'message': (json.loads(cur.state_json or '{}').get('error', '') if cur else 'unknown')}, ensure_ascii=False)}\n\n".encode()
                    return
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=30)
                    ev = {**ev, "progress": ev.get("progress") if ev.get("progress") is not None else (cur.progress if cur else None)}
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n".encode()
        finally:
            try:
                _TASK_QUEUES.get(task_id, []).remove(queue)
            except ValueError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream")
