"""规模补全编排（W12：治"玩具感"= 数据规模+完整度）。

Phase 1 零 API：全量 ICP 重算 + 画像
Phase 2 ggzy API：按中标明细 areaCode 补齐企业区域（限速 1.5s）
Phase 3 ccgp 清扫：34 强词 × 3 页（限速 3-6s，连续 6 词空=限流即停）
Phase 4 联系人回填：ccgp_buyer 详情页（≤30 页，走本机 curl 子进程）
收尾：intent 重算 + 画像 + meili + 通知扫播，打印最终统计。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, r"E:\AAAA\A-xiaosou\vaclead\backend")

import os

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)

from app.db import SessionLocal
from app.models import Contact, Enterprise, Signal

BASE = "http://127.0.0.1:8000"


def curl_json(args: list[str]) -> dict:
    out = subprocess.run(
        ["curl", "-s", "--noproxy", "*", "-m", "600"] + args,
        capture_output=True, text=True, encoding="utf-8", timeout=620,
    )
    if not out.stdout.strip():
        raise RuntimeError(f"空响应: {out.stderr[:200]}")
    return json.loads(out.stdout)


def phase1_local() -> None:
    print("== Phase 1：零 API 补全 ==", flush=True)
    from app.api.imports import apply_icp, recompute_intent

    with SessionLocal() as s:
        ents = s.query(Enterprise).all()
        for e in ents:
            apply_icp(e)
        s.commit()
        for e in ents:
            recompute_intent(s, e.id)
        s.commit()
    print(f"  ICP+intent 重算 {len(ents)} 家", flush=True)
    from app.jobs import refresh_all_stats

    with SessionLocal() as s:
        stat = refresh_all_stats(s)
    print(f"  画像刷新 {stat}", flush=True)


def phase2_region() -> None:
    """ggzy 中标明细 areaCode → 供应商与采购人城市回填。"""
    print("== Phase 2：ggzy 区域回填 ==", flush=True)
    from app.collectors.ggzy import list_bids, normalize_bid_record

    with SessionLocal() as s:
        ents = (
            s.query(Enterprise)
            .filter(Enterprise.source == "ggzy", Enterprise.uscc.isnot(None))
            .all()
        )
    updated = 0
    for i, ref in enumerate(ents):
        try:
            result = list_bids(ref.uscc or "", page=1, size=20)
            areas: list[str] = []
            buyer_area: dict[str, str] = {}
            for raw in result.get("records") or []:
                nb = normalize_bid_record(raw)
                if nb is None:
                    continue
                if nb.get("area") and len(nb["area"]) >= 2:
                    areas.append(nb["area"])
                    if nb.get("tender_org"):
                        buyer_area.setdefault(nb["tender_org"], nb["area"])
            if not areas:
                continue
            with SessionLocal() as s:
                e = s.get(Enterprise, ref.id)
                if e is not None and not e.city:
                    e.city = areas[0]
                    updated += 1
                for bname, area in list(buyer_area.items())[:5]:
                    b = s.query(Enterprise).filter(Enterprise.name == bname).first()
                    if b is not None and not b.city:
                        b.city = area
                        updated += 1
                s.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"  区域回填失败 {ref.name[:16]}: {exc}", flush=True)
        if (i + 1) % 25 == 0:
            print(f"  区域进度 {i + 1}/{len(ents)} 更新 {updated}", flush=True)
    print(f"  区域回填完成：{updated} 处", flush=True)


def phase3_ccgp() -> None:
    print("== Phase 3：ccgp 34 词清扫 ==", flush=True)
    from app.collectors.ccgp import search_ccgp_all
    from app.vertical import device_strong_terms

    terms = device_strong_terms()
    total_signals = 0
    zero_streak = 0
    for idx, kw in enumerate(terms):
        items = list(search_ccgp_all(kw, days=1095, max_pages=3))
        if not items:
            zero_streak += 1
            print(f"  [{idx + 1}/{len(terms)}] {kw}：0 条", flush=True)
            if zero_streak >= 6:
                print("  连续 6 词空结果——疑似限流，ccgp 阶段提前结束", flush=True)
                return
            continue
        zero_streak = 0
        new = persist_ccgp(items)
        total_signals += new
        print(f"  [{idx + 1}/{len(terms)}] {kw}：{len(items)} 公告 → {new} 新信号", flush=True)
    print(f"  ccgp 阶段合计新增信号 {total_signals}", flush=True)


def persist_ccgp(items: list[dict]) -> int:
    from app.api.imports import extract_enterprise_name
    from app.normalize import resolve_enterprise

    created = 0
    with SessionLocal() as s:
        for item in items:
            buyer = item.get("buyer") or extract_enterprise_name(
                item["title"], item["summary"]
            )
            if not buyer or len(buyer) < 4:
                continue
            ent, _ = resolve_enterprise(s, buyer, source="ccgp_buyer")
            exists = (
                s.query(Signal)
                .filter(
                    Signal.enterprise_id == ent.id,
                    Signal.url == item["url"],
                    Signal.title == item["title"],
                )
                .first()
            )
            if exists is None:
                s.add(Signal(
                    enterprise_id=ent.id,
                    type="tender",
                    title=item["title"],
                    url=item["url"],
                    pub_date=item["pub_date"],
                    matched_terms="[]",
                    hit_part="title",
                    raw_excerpt=item["summary"][:200],
                    evidence_claim=(
                        f"政采网{item.get('notice_type') or '公告'}："
                        f"该单位就「{item['title'][:40]}」发布公告"
                    ),
                    base_score=50.0,
                ))
                created += 1
        s.commit()
    return created


def phase4_contacts() -> None:
    print("== Phase 4：联系人回填（30 页）==", flush=True)
    token = curl_json([
        "-X", "POST", BASE + "/api/auth/login",
        "-H", "Content-Type: application/json",
        "-d", '{"username":"admin","password":"admin123"}',
    ])["token"]
    out = curl_json([
        "-X", "POST", BASE + "/api/tasks/notice-contacts",
        "-H", "Authorization: Bearer " + token,
        "-H", "Content-Type: application/json",
        "-d", '{"limit": 30}',
    ])
    print(f"  联系人回填：{out}", flush=True)


def finale() -> None:
    print("== 收尾 ==", flush=True)
    phase1_local()
    try:
        from app import search as meili
        from app.db import SessionLocal as SL

        with SL() as s:
            meili.sync_all(s)
        print("  meili 同步完成", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  meili 同步失败: {exc}", flush=True)
    from app.jobs import generate_notifications

    with SessionLocal() as s:
        n = generate_notifications(s)
    print(f"  通知扫播：{n}", flush=True)
    with SessionLocal() as s:
        ents = s.query(Enterprise).count()
        buyers = s.query(Enterprise).filter(
            Enterprise.source.in_(("ggzy_buyer", "ccgp_buyer"))
        ).count()
        sigs = s.query(Signal).count()
        contacts = s.query(Contact).count()
        with_region = s.query(Enterprise).filter(Enterprise.city.isnot(None)).count()
        with_score = s.query(Enterprise).filter(Enterprise.icp_score > 0).count()
    print(
        f"\n=== 最终统计：企业 {ents}（买家 {buyers}）｜信号 {sigs}｜"
        f"联系人 {contacts}｜有区域 {with_region}｜有评分 {with_score} ===",
        flush=True,
    )


if __name__ == "__main__":
    t0 = time.time()
    phase1_local()
    phase2_region()
    phase3_ccgp()
    phase4_contacts()
    finale()
    print(f"总耗时 {time.time() - t0:.0f}s", flush=True)
