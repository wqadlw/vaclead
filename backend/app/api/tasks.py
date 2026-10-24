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
            logs.append(log)

        if districts:
            pois = await collect_city_quadtree(city, districts, keywords, amap_key, progress_cb=cb)
        else:
            pois = await collect_pois(city, keywords, amap_key, progress_cb=cb)
        with SessionLocal() as s:
            for i, poi in enumerate(pois):
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

    try:
        asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001 — 后台任务需兜底记录
        _checkpoint(task_id, status="error", state_json=json.dumps({"error": str(exc)}, ensure_ascii=False))


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
    threading.Thread(
        target=_run_poi_task,
        args=(task_id, body.city, keywords[:20], amap_key, body.districts),
        daemon=True,
    ).start()
    return {"task_id": task_id}


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


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    if session.get(TaskCheckpoint, task_id) is None:
        raise HTTPException(404, "任务不存在")

    async def gen() -> AsyncIterator[bytes]:
        last_progress = -1
        for _ in range(600):  # 最长 5 分钟
            with SessionLocal() as s:
                cp = s.get(TaskCheckpoint, task_id)
            if cp is None:
                break
            if cp.progress != last_progress:
                last_progress = cp.progress
                state = json.loads(cp.state_json or "{}")
                log = ""
                if isinstance(state, dict):
                    log = state.get("log", "") or (
                        f"进度 {cp.progress}%"
                        + (f"（新增{state.get('inserted', '?')}家）" if "inserted" in state else "")
                    )
                yield f"data: {json.dumps({'type': 'progress', 'progress': cp.progress, 'log': log}, ensure_ascii=False)}\n\n".encode()
            if cp.status == "done":
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n".encode()
                return
            if cp.status == "error":
                state = json.loads(cp.state_json or "{}")
                yield f"data: {json.dumps({'type': 'error', 'message': state.get('error', '未知错误')}, ensure_ascii=False)}\n\n".encode()
                return
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps({'type': 'error', 'message': '任务超时未完成'}, ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(gen(), media_type="text/event-stream")
