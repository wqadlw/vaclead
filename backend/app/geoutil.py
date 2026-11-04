"""地图网格聚合（SPEC-AK AK2）：纯函数，无 SQL——安全扫描零摩擦。

行取回 + 内存网格分组（0.08°≈8km）。24k 行毫秒级。
"""
from __future__ import annotations

from collections import defaultdict


def grid_cluster(
    rows: list[tuple[float, float, bool]],
    step: float = 0.08,
    threshold: int = 1500,
) -> dict:
    """[(lat, lng, has_phone)] → {mode, items}。

    rows 超过 threshold 时聚合成 0.08° 网格（返回格心/格计数/格内电话数），
    否则原样返回打点。
    """
    if len(rows) <= threshold:
        return {
            "mode": "points",
            "total": len(rows),
            "items": [
                {"lat": lat, "lng": lng, "has_phone": hp} for lat, lng, hp in rows
            ],
        }
    clusters: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    phones: dict[tuple[int, int], int] = defaultdict(int)
    for lat, lng, hp in rows:
        key = (round(lat / step), round(lng / step))
        acc = clusters[key]
        acc[0] += lat
        acc[1] += lng
        acc[2] += 1
        if hp:
            phones[key] += 1
    return {
        "mode": "clusters",
        "total": len(rows),
        "items": [
            {
                "lat": round(acc[0] / acc[2], 5),
                "lng": round(acc[1] / acc[2], 5),
                "count": acc[2],
                "phone": phones[key],
            }
            for key, acc in clusters.items()
        ],
    }
