"""采集任务路由：POI 拓客 / 招标扫描 / SSE 进度流。"""
from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger("vaclead.api.tasks")

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

    # 计数器放外层作用域：TaskCancelled 兜底分支需要读取（审计修复）
    inserted = 0
    merged = 0

    async def _main() -> None:
        nonlocal inserted, merged
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
        # 注意：logs 定义在 _main() 内部，此处拿不到（审计发现的历史 NameError）——
        # 只落状态与摘要计数，不引用 logs。
        _checkpoint(
            task_id,
            status="cancelled",
            state_json=json.dumps(
                {"inserted": inserted, "merged": merged}, ensure_ascii=False
            ),
        )
        _broadcast(task_id, {"type": "done", "cancelled": True})
    except Exception as exc:  # noqa: BLE001 — 后台任务需兜底记录
        logger.exception("POI task %s failed", task_id)
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

    def _worker() -> dict:  # SPEC-E E2：返回 totals 交给 lifecycle
        import asyncio

        from ..collectors.tender import fetch_zhiliaoke_tenders
        from .task_runtime import signal_exists

        totals: dict = {"signals_created": 0}

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
                    from .imports import extract_enterprise_name

                    name = extract_enterprise_name(title, content) or f"待归属·{title[:18].strip()}"
                    ent, _ = resolve_enterprise(s, name, source="tender")
                    if not signal_exists(s, ent.id, p.get("url", ""), title):
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
                totals["signals_created"] = created

        asyncio.run(_main())
        return totals

    from .task_runtime import spawn

    spawn(task_id, _worker, label="tender-scan")
    return {"task_id": task_id}


class GgzyHarvestIn(BaseModel):
    """官方中标数据采集（data.ggzy.gov.cn，契约见 docs/DATA_ACQUISITION.md）。"""
    keywords: list[str] | None = None  # 缺省用设备强词表
    max_pages: int = 3                 # 每关键词分页上限（pageSize=200）
    pull_bid_details: bool = True      # 对有中标记录的主体拉明细
    max_bids_per_ent: int = 20         # 每主体明细拉取上限


@router.post("/tasks/ggzy-harvest")
def start_ggzy_harvest(
    body: GgzyHarvestIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    from ..collectors.ggzy import (
        list_bids,
        normalize_bid_record,
        normalize_entity_record,
        search_all_entities,
    )
    from ..models import Signal
    from ..normalize import resolve_enterprise
    from ..vertical import device_strong_terms, device_weak_terms, ggzy_name_terms, signal_weights

    keywords = [k.strip() for k in (body.keywords or []) if k.strip()]
    if not keywords:
        keywords = ggzy_name_terms()  # 名称检索须用品类宽词（细词召回≈0）
    keywords = keywords[:12]  # 单任务上限：限速 1.5s/req，防长跑
    strong = device_strong_terms()
    weak = device_weak_terms()
    weights = signal_weights()
    title_pts = float(weights.get("tender_title_hit", 70))
    body_pts = float(weights.get("tender_body_hit", 50))
    weak_discount = float(weights.get("weak_discount", 0.5))

    task_id = uuid.uuid4().hex[:12]
    session.add(TaskCheckpoint(task_id=task_id, kind="ggzy", status="running", progress=0))
    session.commit()

    def _signal_for_project(title: str) -> dict | None:
        """项目名与设备词匹配 → {terms, hit_part, score}；不命中返回 None。"""
        t_hits = [t for t in strong if t in title]
        if t_hits:
            return {"terms": t_hits, "hit_part": "title", "score": title_pts}
        w_hits = [t for t in weak if t in title]
        if w_hits:
            return {"terms": w_hits, "hit_part": "title", "score": title_pts * weak_discount}
        return None

    def _worker() -> dict:  # SPEC-E E2：返回 totals 交给 lifecycle
        totals = {"entities": 0, "new_entities": 0, "bids": 0, "signals": 0, "buyers": 0}
        affected: set[int] = set()
        competitor_ids: set[int] = set()  # 采集端基于中标事实的同行打标（apply_icp 不许覆盖）
        try:
            for kw_idx, kw in enumerate(keywords):
                if is_cancel_requested(task_id):
                    raise TaskCancelled(task_id)
                _log(task_id, f"[{kw_idx + 1}/{len(keywords)}] 检索官方主体：{kw}")
                entities = list(search_all_entities(kw, max_pages=body.max_pages, size=200))
                _log(task_id, f"  官方返回 {len(entities)} 家主体")
                for raw in entities:
                    if is_cancel_requested(task_id):
                        raise TaskCancelled(task_id)
                    rec = normalize_entity_record(raw)
                    if rec is None:
                        continue
                    with SessionLocal() as s:
                        ent, is_new = resolve_enterprise(
                            s, rec["name"], source="ggzy", uscc=rec["uscc"]
                        )
                        if is_new:
                            totals["new_entities"] += 1
                        totals["entities"] += 1
                        if rec["uscc"] and not ent.uscc:
                            ent.uscc = rec["uscc"]
                        ent.source_batch_id = ent.source_batch_id or f"ggzy-{task_id}"
                        # 中标方带设备词 → 供应商/同行打标（is_competitor=1 保留不推荐）
                        if any(t in rec["name"] for t in strong):
                            ent.is_competitor = 1
                            competitor_ids.add(ent.id)
                        bids: list[dict] = []
                        if (
                            body.pull_bid_details
                            and rec["uscc"]
                            and rec["bid_count"] > 0
                        ):
                            result = list_bids(
                                rec["uscc"], page=1, size=min(body.max_bids_per_ent, 200)
                            )
                            totals["bids"] += int(result.get("total") or 0)
                            for b in result.get("records") or []:
                                nb = normalize_bid_record(b)
                                if nb is not None:
                                    bids.append(nb)
                        for nb in bids:
                            m = _signal_for_project(nb["project_name"])
                            if m is None:
                                continue  # 与招标导入同语义：设备词不命中不落信号
                            price_txt = (
                                f"成交价{nb['bid_price_wan']}万元"
                                if nb["bid_price_wan"] is not None
                                else "成交价未知"
                            )
                            org_txt = f"，采购人：{nb['tender_org']}" if nb["tender_org"] else ""
                            url = (
                                f"https://data.ggzy.gov.cn/#/yjcx/bid/{nb['deal_id']}"
                                if nb["deal_id"]
                                else ""
                            )
                            exists = (
                                s.query(Signal)
                                .filter(
                                    Signal.enterprise_id == ent.id,
                                    Signal.url == url,
                                    Signal.title == nb["project_name"],
                                )
                                .first()
                            )
                            if exists is None:
                                s.add(Signal(
                                    enterprise_id=ent.id,
                                    type="tender_win",
                                    title=nb["project_name"],
                                    url=url,
                                    pub_date=nb["pub_date"],
                                    hit_part=m["hit_part"],
                                    matched_terms=json.dumps(m["terms"], ensure_ascii=False),
                                    raw_excerpt=f"{nb['trade_type'] or '中标'}｜{price_txt}{org_txt}",
                                    evidence_claim=(
                                        f"该主体在公共资源交易平台中标「{nb['project_name']}」，{price_txt}{org_txt}"
                                    ),
                                    base_score=m["score"],
                                ))
                                totals["signals"] += 1
                                affected.add(ent.id)
                            # 采购人是真买家：独立建档 + 采购信号（可归属则建，零丢失）
                            buyer = nb["tender_org"]
                            if buyer and len(buyer) >= 4 and buyer != ent.name:
                                buyer_ent, buyer_new = resolve_enterprise(
                                    s, buyer, source="ggzy_buyer"
                                )
                                if buyer_new:
                                    totals["buyers"] += 1
                                buyer_sig_exists = (
                                    s.query(Signal)
                                    .filter(
                                        Signal.enterprise_id == buyer_ent.id,
                                        Signal.url == url,
                                        Signal.title == nb["project_name"],
                                    )
                                    .first()
                                )
                                if buyer_sig_exists is None:
                                    bm = _signal_for_project(nb["project_name"])
                                    s.add(Signal(
                                        enterprise_id=buyer_ent.id,
                                        type="tender_buy",
                                        title=nb["project_name"],
                                        url=url,
                                        pub_date=nb["pub_date"],
                                        hit_part=(bm or m or {"hit_part": "body"})["hit_part"],
                                        matched_terms=json.dumps(
                                            (bm or m or {"terms": []})["terms"],
                                            ensure_ascii=False,
                                        ),
                                        raw_excerpt=(
                                            f"作为采购人由「{ent.name}」中标成交｜{price_txt}"
                                        ),
                                        evidence_claim=(
                                            f"该单位公开采购「{nb['project_name']}」，"
                                            f"由{ent.name}中标，{price_txt}"
                                        ),
                                        base_score=(bm or m or {"score": body_pts})["score"],
                                    ))
                                    totals["signals"] += 1
                                    affected.add(buyer_ent.id)
                        s.flush()
                        s.commit()
                pct = 5 + int((kw_idx + 1) / len(keywords) * 90)
                _checkpoint(task_id, progress=pct,
                            state_json=json.dumps({"keyword": kw, **totals}, ensure_ascii=False))
            for eid in affected:
                with SessionLocal() as s:
                    e2 = s.get(Enterprise, eid)
                    if e2 is not None:
                        apply_icp(e2)
                        if eid in competitor_ids:
                            e2.is_competitor = 1  # 中标事实优先于名称规则
                        recompute_intent(s, eid)
                        s.commit()
            return totals  # 生命周期（cancelled/failed/done）由 task_runtime.lifecycle 接管
        except TaskCancelled:
            raise  # 取消向上传播给 lifecycle 落 cancelled 态
        except Exception:
            raise  # 异常向上传播给 lifecycle 落 failed 态

    from .task_runtime import spawn

    spawn(task_id, _worker, label="ggzy-harvest")
    return {"task_id": task_id, "keywords": keywords}


class CcgpScanIn(BaseModel):
    """政采网公告扫描（search.ccgp.gov.cn/bxsearch，普通 HTTP）。"""
    keywords: list[str] | None = None  # 缺省用设备强词表
    days: int = 90                     # 近 N 天
    max_pages: int = 3                 # 每关键词分页上限


@router.post("/tasks/ccgp-scan")
def start_ccgp_scan(
    body: CcgpScanIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    from ..collectors.ccgp import search_ccgp_all
    from .imports import extract_enterprise_name
    from ..models import Signal
    from ..normalize import resolve_enterprise
    from ..vertical import device_strong_terms, device_weak_terms, signal_weights

    keywords = [k.strip() for k in (body.keywords or []) if k.strip()]
    if not keywords:
        keywords = device_strong_terms()
    keywords = keywords[:12]  # 单任务上限：限速 3-6s/req，防长跑
    strong = device_strong_terms()
    weak = device_weak_terms()
    weights = signal_weights()
    title_pts = float(weights.get("tender_title_hit", 70))
    body_pts = float(weights.get("tender_body_hit", 50))
    weak_discount = float(weights.get("weak_discount", 0.5))

    task_id = uuid.uuid4().hex[:12]
    session.add(TaskCheckpoint(task_id=task_id, kind="ccgp", status="running", progress=0))
    session.commit()

    def _match(title: str, summary: str) -> dict | None:
        t_hits = [t for t in strong if t in title]
        if t_hits:
            return {"terms": t_hits, "hit_part": "title", "score": title_pts}
        w_hits = [t for t in weak if t in title]
        if w_hits:
            return {"terms": w_hits, "hit_part": "title", "score": title_pts * weak_discount}
        b_hits = [t for t in strong if t in summary]
        if b_hits:
            return {"terms": b_hits, "hit_part": "body", "score": body_pts}
        return None

    def _worker() -> dict:  # SPEC-E E2：返回 totals 交给 lifecycle
        totals = {"announcements": 0, "buyers": 0, "signals": 0, "suppliers": 0}
        affected: set[int] = set()
        competitor_ids: set[int] = set()
        try:
            for kw_idx, kw in enumerate(keywords):
                if is_cancel_requested(task_id):
                    raise TaskCancelled(task_id)
                _log(task_id, f"[{kw_idx + 1}/{len(keywords)}] 检索政采公告：{kw}")
                page = 0
                for item in search_ccgp_all(kw, days=body.days, max_pages=body.max_pages):
                    if is_cancel_requested(task_id):
                        raise TaskCancelled(task_id)
                    page += 1
                    totals["announcements"] += 1
                    m = _match(item["title"], item["summary"])
                    if m is None:
                        continue
                    with SessionLocal() as s:
                        buyer = item.get("buyer")
                        buyer_ent = None
                        if buyer and len(buyer) >= 4:
                            buyer_ent, buyer_new = resolve_enterprise(
                                s, buyer, source="ccgp_buyer"
                            )
                            if buyer_new:
                                totals["buyers"] += 1
                        elif item["summary"]:
                            # 无采购人字段时从摘要抽归属（复用导入抽取器）
                            guessed = extract_enterprise_name(item["title"], item["summary"])
                            if guessed:
                                buyer_ent, buyer_new = resolve_enterprise(
                                    s, guessed, source="ccgp_buyer"
                                )
                                if buyer_new:
                                    totals["buyers"] += 1
                        if buyer_ent is None:
                            buyer_ent, _ = resolve_enterprise(
                                s, f"待归属·{item['title'][:18].strip()}", source="ccgp_buyer"
                            )
                        exists = (
                            s.query(Signal)
                            .filter(
                                Signal.enterprise_id == buyer_ent.id,
                                Signal.url == item["url"],
                                Signal.title == item["title"],
                            )
                            .first()
                        )
                        if exists is None:
                            s.add(Signal(
                                enterprise_id=buyer_ent.id,
                                type="tender",
                                title=item["title"],
                                url=item["url"],
                                pub_date=item["pub_date"],
                                hit_part=m["hit_part"],
                                matched_terms=json.dumps(m["terms"], ensure_ascii=False),
                                raw_excerpt=item["summary"][:200],
                                evidence_claim=(
                                    f"政采网{item['notice_type'] or '公告'}：该单位就"
                                    f"「{item['title'][:40]}」发布公告"
                                    + (f"，代理机构 {item['agent']}" if item["agent"] else "")
                                ),
                                base_score=m["score"],
                            ))
                            totals["signals"] += 1
                            affected.add(buyer_ent.id)
                        # 中标/成交公告：从摘要抽中标供应商 → 供应商侧信号（同行情报）
                        if item["notice_type"] in ("中标公告", "成交公告", "合同公告"):
                            winner = extract_enterprise_name(
                                item["title"], item["summary"]
                            )
                            if winner and len(winner) >= 4 and winner != (buyer or ""):
                                sup_ent, sup_new = resolve_enterprise(
                                    s, winner, source="ccgp"
                                )
                                if sup_new:
                                    totals["suppliers"] += 1
                                if any(t in winner for t in strong):
                                    sup_ent.is_competitor = 1
                                    competitor_ids.add(sup_ent.id)
                                sup_exists = (
                                    s.query(Signal)
                                    .filter(
                                        Signal.enterprise_id == sup_ent.id,
                                        Signal.url == item["url"],
                                        Signal.title == item["title"],
                                    )
                                    .first()
                                )
                                if sup_exists is None:
                                    s.add(Signal(
                                        enterprise_id=sup_ent.id,
                                        type="tender_win",
                                        title=item["title"],
                                        url=item["url"],
                                        pub_date=item["pub_date"],
                                        hit_part=m["hit_part"],
                                        matched_terms=json.dumps(
                                            m["terms"], ensure_ascii=False
                                        ),
                                        raw_excerpt=item["summary"][:200],
                                        evidence_claim=(
                                            f"政采网{item['notice_type']}：该主体中标"
                                            f"「{item['title'][:40]}」"
                                            + (f"（采购人 {buyer}）" if buyer else "")
                                        ),
                                        base_score=m["score"],
                                    ))
                                    totals["signals"] += 1
                                    affected.add(sup_ent.id)
                        s.flush()
                        s.commit()
                pct = 5 + int((kw_idx + 1) / len(keywords) * 90)
                _checkpoint(task_id, progress=pct,
                            state_json=json.dumps({"keyword": kw, **totals}, ensure_ascii=False))
            for eid in affected:
                with SessionLocal() as s:
                    e2 = s.get(Enterprise, eid)
                    if e2 is not None:
                        apply_icp(e2)
                        if eid in competitor_ids:
                            e2.is_competitor = 1
                        recompute_intent(s, eid)
                        s.commit()
            return totals  # 生命周期（尾部钩子/三分支收尾）由 task_runtime.lifecycle 接管
        except TaskCancelled:
            raise
        except Exception:
            raise

    from .task_runtime import spawn

    spawn(task_id, _worker, label="ccgp-scan")
    return {"task_id": task_id, "keywords": keywords}


class GongshangEnrichIn(BaseModel):
    limit: int = 200  # 单批最多补全多少家有 uscc 的企业


@router.post("/tasks/enrich-gongshang")
def start_gongshang_enrich(
    body: GongshangEnrichIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """工商补全（SPEC-B §5）：对有 uscc 且缺注册资本的企业按当前 provider 批量补全。

    offline provider 不发明数据，实际补全需在设置页配置 chinacheck_token /
    aliyun_qcc_key；本端点同步执行（provider 内置限速与失败容错）。
    """
    from ..collectors.gongshang import get_provider
    from ..models import Enterprise

    provider = get_provider(session)
    rows = (
        session.query(Enterprise)
        .filter(Enterprise.uscc.isnot(None), Enterprise.uscc != "")
        .filter(Enterprise.registered_capital_wan.is_(None))
        .limit(min(body.limit, 1000))
        .all()
    )
    if not rows:
        return {"task_id": None, "updated": 0, "skipped": "无待补全企业"}
    updated = 0
    for e in rows:
        try:
            data = provider.enrich(e.uscc or "")
        except NotImplementedError:
            raise HTTPException(
                400, f"provider {provider.name} 适配器未实现，请在设置页配置可用凭证"
            )
        if not data:
            continue
        if data.get("registered_capital_wan") is not None:
            e.registered_capital_wan = float(data["registered_capital_wan"])
        if data.get("business_scope"):
            e.business_scope = data["business_scope"]
        if data.get("industry") and not e.industry_name:
            e.industry_name = data["industry"]
        apply_icp(e)
        updated += 1
    session.commit()
    return {"task_id": None, "updated": updated, "provider": provider.name, "scanned": len(rows)}




class BackfillContactsIn(BaseModel):
    limit: int = 100


@router.post("/tasks/backfill-contacts")
def backfill_contacts(
    body: BackfillContactsIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """联系人回填（SPEC-D 2）：对 source=ggzy 的企业用官方接口补法定代表人。

    幂等：uq_contact 唯一约束去重；官方接口按企业名精确查询，1.5s 限速。
    """
    from ..collectors.ggzy import search_entities
    from ..models import Contact, Enterprise

    rows = (
        session.query(Enterprise)
        .filter(Enterprise.source == "ggzy")
        .limit(min(body.limit, 500))
        .all()
    )
    if not rows:
        return {"updated": 0, "skipped": "无 ggzy 企业"}
    created = 0
    for e in rows:
        result = search_entities(e.name, page=1, size=1)
        records = result.get("records") or []
        if not records:
            continue
        rec = normalize_entity = None
        from ..collectors.ggzy import normalize_entity_record

        rec = normalize_entity_record(records[0])
        if rec is None or not rec.get("legal_rep"):
            continue
        exists = (
            session.query(Contact)
            .filter(
                Contact.enterprise_id == e.id,
                Contact.name == rec["legal_rep"],
                Contact.role == "legal_rep",
            )
            .first()
        )
        if exists is None:
            session.add(Contact(
                enterprise_id=e.id,
                name=rec["legal_rep"],
                role="legal_rep",
                title="法定代表人",
                source="ggzy",
                evidence_claim=f"国家信息中心公共资源交易数据服务：该企业法定代表人为 {rec['legal_rep']}",
            ))
            created += 1
    session.commit()
    return {"updated": created, "scanned": len(rows)}




class NoticeContactsIn(BaseModel):
    limit: int = 10


@router.post("/tasks/notice-contacts")
def backfill_notice_contacts(
    body: NoticeContactsIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """公告详情联系方式回填（SPEC-D 数据解锁）：ccgp_buyer 企业 → 详情页抓
    项目联系人/电话/地址/区域 → contact 表 + enterprise 地址区县。幂等可重跑。

    联系方式是政采公告法定公开内容（写给投标人），合规边界不变：限速、
    失败退避、限流即停。"""
    import time as _time

    from ..collectors.ccgp import fetch_notice_detail
    from ..models import Contact, Enterprise, Signal

    ents = (
        session.query(Enterprise)
        .filter(Enterprise.source == "ccgp_buyer")
        .limit(min(body.limit, 50))
        .all()
    )
    if not ents:
        return {"updated": 0, "skipped": "无 ccgp_buyer 企业"}
    contacts_created = 0
    ents_updated = 0
    fetched = 0
    for e in ents:
        if fetched >= body.limit:
            break
        sig = (
            session.query(Signal)
            .filter(Signal.enterprise_id == e.id, Signal.url != "")
            .first()
        )
        if sig is None:
            continue
        detail = fetch_notice_detail(sig.url)
        fetched += 1
        if detail is None:
            continue
        changed = False
        if detail.get("buyer_address") and not e.address:
            e.address = detail["buyer_address"]
            changed = True
        if detail.get("region") and not e.city:
            e.city = detail["region"]
            changed = True
        if changed:
            ents_updated += 1
        pairs = []
        if detail.get("buyer_contact"):
            # 采购单位联系方式通常是采购负责人——决策链关键人（SPEC-J J2）
            pairs.append(("采购联系人", detail["buyer_contact"], "ccgp_buyer",
                          f"政采公告「{sig.title[:30]}」采购单位联系方式", "decision_maker"))
        if detail.get("project_contact"):
            pairs.append(("项目联系人", detail["project_contact"],
                          detail.get("project_phone") or "ccgp",
                          f"政采公告「{sig.title[:30]}」项目联系方式", "handler"))
        if detail.get("agency_name"):
            pairs.append(("代理机构", detail["agency_name"],
                          detail.get("agency_contact") or "ccgp",
                          f"政采公告「{sig.title[:30]}」代理机构", "handler"))
        for role, name, contact_info, claim, drole in pairs:
            exists = (
                session.query(Contact)
                .filter(
                    Contact.enterprise_id == e.id,
                    Contact.name == name,
                    Contact.role == role,
                )
                .first()
            )
            # 公告正文的法定公开邮箱 → 回填到该企业联系人（verified：公告直接公开）
            notice_email = (detail.get("email") or "").strip() or None
            if exists is None:
                session.add(Contact(
                    enterprise_id=e.id, name=name, role=role,
                    title=contact_info[:80] if contact_info else None,
                    email=notice_email,
                    decision_role=drole,
                    verify_state="verified",
                    source="ccgp", evidence_claim=claim,
                ))
                contacts_created += 1
            elif notice_email and not exists.email:
                exists.email = notice_email
                exists.decision_role = exists.decision_role or drole
                exists.verify_state = "verified"
        _time.sleep(1.0)  # 详情页额外礼貌间隔
    session.commit()
    return {
        "contacts_created": contacts_created,
        "enterprises_updated": ents_updated,
        "pages_fetched": fetched,
        "scanned": len(ents),
    }


class EnrichPipelineIn(BaseModel):
    """fire-enrich 式编排补全（吸收其 orchestrator 阶段化模式）。

    阶段：A discovery（ggzy 官方接口按名找回 uscc+法人）→ B profile（中标画像聚合）
    → C gongshang（有 uscc 缺注册资本走 Provider，未配置凭证自动跳过）。
    逐企业 SSE 进度 + 阶段命中统计。
    """
    limit: int = 50


@router.post("/tasks/enrich-pipeline")
def start_enrich_pipeline(
    body: EnrichPipelineIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    from ..collectors.ggzy import normalize_entity_record, search_entities
    from ..collectors.gongshang import get_provider
    from ..jobs import enrich_stats
    from ..models import Contact

    provider = get_provider(session)

    task_id = uuid.uuid4().hex[:12]
    session.add(TaskCheckpoint(task_id=task_id, kind="enrich", status="running", progress=0))
    session.commit()

    def _worker() -> dict:  # SPEC-E E2：返回 totals 交给 lifecycle
        totals = {"scanned": 0, "uscc_found": 0, "legal_added": 0,
                  "stat_built": 0, "gongshang_updated": 0, "failed": 0}
        try:
            with SessionLocal() as s:
                ents = s.query(Enterprise).limit(min(body.limit, 500)).all()
                targets = [e for e in ents if not e.uscc or e.registered_capital_wan is None]
            _log(task_id, f"补全流水线启动：{len(targets)} 家待补（provider={provider.name}）")
            for i, ent_ref in enumerate(targets):
                if is_cancel_requested(task_id):
                    raise TaskCancelled(task_id)
                stage_hits: list[str] = []
                try:
                    with SessionLocal() as s:
                        e = s.get(Enterprise, ent_ref.id)
                        if e is None:
                            continue
                        # Stage A: discovery
                        if not e.uscc:
                            result = search_entities(e.name, page=1, size=1)
                            records = result.get("records") or []
                            if records:
                                rec = normalize_entity_record(records[0])
                                if rec and rec["uscc"]:
                                    e.uscc = rec["uscc"]
                                    totals["uscc_found"] += 1
                                    stage_hits.append("uscc")
                                if rec and rec["legal_rep"]:
                                    dup = (
                                        s.query(Contact)
                                        .filter(
                                            Contact.enterprise_id == e.id,
                                            Contact.role == "legal_rep",
                                        )
                                        .first()
                                    )
                                    if dup is None:
                                        s.add(Contact(
                                            enterprise_id=e.id,
                                            name=rec["legal_rep"],
                                            role="legal_rep",
                                            title="法定代表人",
                                            source="ggzy",
                                            evidence_claim=(
                                                "国家信息中心公共资源交易数据服务："
                                                f"该企业法定代表人为 {rec['legal_rep']}"
                                            ),
                                        ))
                                        totals["legal_added"] += 1
                                        stage_hits.append("法人")
                        # Stage B: profile
                        stat = enrich_stats(s, e.id)
                        if stat.get("bid_count", 0) > 0:
                            totals["stat_built"] += 1
                            stage_hits.append("画像")
                        # Stage C: gongshang
                        if e.uscc and e.registered_capital_wan is None:
                            try:
                                data = provider.enrich(e.uscc or "")
                            except NotImplementedError:
                                data = None
                            if data and data.get("registered_capital_wan") is not None:
                                e.registered_capital_wan = float(data["registered_capital_wan"])
                                if data.get("business_scope"):
                                    e.business_scope = data["business_scope"]
                                apply_icp(e)
                                totals["gongshang_updated"] += 1
                                stage_hits.append("工商")
                        if stage_hits:
                            apply_icp(e)
                        s.commit()
                    totals["scanned"] += 1
                    if stage_hits:
                        _log(task_id, f"  [{i + 1}/{len(targets)}] {ent_ref.name[:22]}：{' + '.join(stage_hits)}")
                except Exception as exc:  # noqa: BLE001 — 单企业失败不中断流水线
                    totals["failed"] += 1
                    logger.warning("补全失败 %s: %s", ent_ref.name[:22], exc)
                pct = 5 + int((i + 1) / max(len(targets), 1) * 90)
                _checkpoint(task_id, progress=pct,
                            state_json=json.dumps(totals, ensure_ascii=False))
            return totals  # 生命周期（尾部钩子/三分支收尾）由 task_runtime.lifecycle 接管
        except TaskCancelled:
            raise
        except Exception:
            raise

    from .task_runtime import spawn

    spawn(task_id, _worker, label="enrich-pipeline")
    return {"task_id": task_id}


@router.post("/tasks/outreach-dispatch")
def start_outreach_dispatch(
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """触达调度任务（SPEC-J J4）：收件同步 + 预算内发送，SSE 可观测。"""
    task_id = uuid.uuid4().hex[:12]
    session.add(TaskCheckpoint(task_id=task_id, kind="outreach", status="running", progress=0))
    session.commit()

    def _worker() -> dict:
        from ..outreach.dispatcher import run_outreach_dispatch

        with SessionLocal() as s:
            totals = run_outreach_dispatch(s)
        _log(task_id, f"触达调度完成：发送 {totals['sent']}，回信 {totals['replies']}，"
                      f"失败 {totals['failed']}，预算耗尽挂起 {totals['budget_exhausted']}")
        return totals

    from .task_runtime import spawn

    spawn(task_id, _worker, label="outreach-dispatch")
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
