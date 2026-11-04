"""geoutil 网格聚合纯函数单测（SPEC-AK AK2 模块）。"""
from app.geoutil import grid_cluster


def test_points_mode_below_threshold():
    rows = [(31.30, 120.58, True), (31.31, 120.60, False)]
    out = grid_cluster(rows, threshold=10)
    assert out["mode"] == "points" and out["total"] == 2
    assert out["items"][0]["has_phone"] is True


def test_cluster_mode_groups_and_counts():
    # 三个点聚在同一格（0.08° 网格），一个点在远处
    rows = [
        (31.300, 120.580, True),
        (31.302, 120.582, True),
        (31.298, 120.579, False),
        (32.500, 121.000, True),
    ]
    out = grid_cluster(rows, threshold=2)
    assert out["mode"] == "clusters" and out["total"] == 4
    near = [c for c in out["items"] if c["count"] == 3]
    assert len(near) == 1
    assert near[0]["phone"] == 2, "格内电话计数"
    # 格心是均值
    assert abs(near[0]["lat"] - 31.3) < 0.001
    far = [c for c in out["items"] if c["count"] == 1]
    assert far[0]["phone"] == 1
