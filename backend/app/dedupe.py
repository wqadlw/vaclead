"""全库企业查重扫描（SPEC-Q）：rapidfuzz 两两比对 → 喂复核队列。

resolve_enterprise 的实时归一只比"同时到达"的候选——不同渠道先后入库的
同名/近名企业从未被两两比较过，这是本扫描要补的主动发现环节。
产出统一走 review_queue（stewardship）：80-89 分宁复核不静默，与 SPEC-B 语义一致。
"""
from __future__ import annotations

import logging
import re

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from .models import Enterprise, ReviewQueue, utcnow

logger = logging.getLogger("vaclead.dedupe")

#: 入队下限（resolve 实时链用 80；批量扫描全库面向历史存量，85 起控精度）
SCAN_THRESHOLD = 85
#: O(N²) 防炸上限：企业数超过该值时只比同首字/同关键词分组（自用规模远达不到）
_MAX_PAIRWISE = 5000

_NOISE_RE = re.compile(r"[\s（）()·、,，。.]*")


def norm_name(name: str | None) -> str:
    """比对用归一：去空白/括号/标点 + 小写。"""
    return _NOISE_RE.sub("", (name or "").lower())


def scan_duplicates(session: Session, threshold: int = SCAN_THRESHOLD,
                    limit: int = 200) -> dict:
    """全库两两模糊比对，新命中入 review_queue（UNIQUE 幂等，重扫安全）。

    返回 {compared, queued, skipped_existing}；queued=本次新入队的疑似重复对数。
    """
    rows = (
        session.query(Enterprise.id, Enterprise.name)
        .filter(Enterprise.status == "active")
        .limit(_MAX_PAIRWISE)
        .all()
    )
    ents = [(int(rid), norm_name(name)) for rid, name in rows if norm_name(name)]
    existing = {
        (int(c), int(t))
        for c, t in session.query(ReviewQueue.candidate_id, ReviewQueue.target_id)
        .all()
        if c is not None
    }
    queued = 0
    compared = 0
    seen: set[tuple[int, int]] = set()
    for i in range(len(ents)):
        id_a, name_a = ents[i]
        for j in range(i + 1, len(ents)):
            id_b, name_b = ents[j]
            compared += 1
            if (id_a, id_b) in seen or (id_b, id_a) in seen:
                continue
            score = fuzz.ratio(name_a, name_b)
            if score < threshold:
                continue
            # 小 id 当 target（幸存者=先入库者），大 id 当 candidate
            pair = (id_b, id_a) if id_b < id_a else (id_a, id_b)
            candidate_id, target_id = pair
            if (candidate_id, target_id) in existing or (candidate_id, target_id) in seen:
                continue
            seen.add((candidate_id, target_id))
            session.add(ReviewQueue(
                candidate_id=candidate_id, target_id=target_id,
                score=round(score, 1), reason="dup_scan",
            ))
            queued += 1
            if queued >= limit:
                break
        if queued >= limit:
            break
    session.commit()
    return {"compared": compared, "queued": queued,
            "skipped_existing": len(existing), "enterprises": len(ents)}
