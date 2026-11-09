"""SPEC-ORDER 回归：企业列表表头服务端真排序（order/dir 白名单 + 全库排序）。

要点：
- order=capital desc 全库首行=注册资本最大（与 icp 序刻意错开，证明排序真的换了键）
- order=capital asc；order=created；非法 order/dir 回退默认 icp desc
- order+dir 与 URL 组合；page_size 强制分页时排序跨页连续（全库序，非页内序）
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-order-"), "t.db")
)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def seeded(client, admin_headers):
    """5 家企业：资本序与 icp 序刻意错开，created_at 显式给定——确定性排序验证。

    资本（万）：A=100 < B=500 < C=1000 < D=3000 < E=5000
    icp 得分：   A=90  > B=80  > C=70  > D=60  > E=50（与资本序完全相反）
    created_at：A < B < C < D < E（与资本序同向）
    """
    from app.db import SessionLocal
    from app.models import Enterprise

    with SessionLocal() as s:
        rows = [
            ("排序测试甲公司", 100.0, 90.0, "2024-01-01T00:00:00+00:00"),
            ("排序测试乙公司", 500.0, 80.0, "2024-02-01T00:00:00+00:00"),
            ("排序测试丙公司", 1000.0, 70.0, "2024-03-01T00:00:00+00:00"),
            ("排序测试丁公司", 3000.0, 60.0, "2024-04-01T00:00:00+00:00"),
            ("排序测试戊公司", 5000.0, 50.0, "2024-05-01T00:00:00+00:00"),
        ]
        names = []
        for name, capital, icp, created in rows:
            s.add(
                Enterprise(
                    name=name,
                    province="江苏省",
                    source="manual",
                    registered_capital_wan=capital,
                    icp_score=icp,
                    created_at=created,
                )
            )
            names.append(name)
        s.commit()
    yield {"names": names}


def _list(client, headers, **params) -> dict:
    r = client.get("/api/enterprises", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _gather(client, headers, **params) -> list[dict]:
    """翻页收全库中"排序测试"前缀的 seed 子序列。

    全量跑时所有用例共享同一测试库（conftest setdefault 语义：首个导入模块
    定库），seed 五家不一定在第 1 页——单页断言会随机假绿/假红，必须跨页
    拼接后只看自己的子序列（相对序确定性不受其他用例数据影响）。
    """
    items: list[dict] = []
    page = 1
    while page <= 50:  # page_size=100，防呆上限
        d = _list(client, headers, page_size=100, page=page, **params)
        items.extend(i for i in d["items"] if i["name"].startswith("排序测试"))
        if page * 100 >= d["total"]:
            break
        page += 1
    return items


def test_default_order_unchanged(client, admin_headers, seeded):
    """SPEC-ORDER：order 空 → 默认 icp desc, id desc（与旧行为逐字节一致）。

    只断言过滤子序列的相对序：全量跑时库里有其他用例的企业（icp 可能更高），
    但 seed 内 icp 序甲(90)>乙(80)>丙(70)>丁(60)>戊(50) 必须原样保持。
    """
    items = _gather(client, admin_headers)
    names = [i["name"] for i in items]
    assert names == [
        "排序测试甲公司", "排序测试乙公司", "排序测试丙公司", "排序测试丁公司", "排序测试戊公司",
    ]


def test_order_capital_desc_first_is_largest(client, admin_headers, seeded):
    """SPEC-ORDER：order=capital&dir=desc 全库序下 seed 首行=注册资本最大
    （戊 5000 万，其 icp=50 最低——默认序下排最后，证明排序键真的切换了）。"""
    items = _gather(client, admin_headers, order="capital", dir="desc")
    names = [i["name"] for i in items]
    assert names[0] == "排序测试戊公司"
    capitals = [i["registered_capital_wan"] for i in items]
    assert capitals == [5000.0, 3000.0, 1000.0, 500.0, 100.0]


def test_order_capital_asc(client, admin_headers, seeded):
    items = _gather(client, admin_headers, order="capital", dir="asc")
    names = [i["name"] for i in items]
    assert names[0] == "排序测试甲公司"
    capitals = [i["registered_capital_wan"] for i in items]
    assert capitals == [100.0, 500.0, 1000.0, 3000.0, 5000.0]


def test_order_created_desc(client, admin_headers, seeded):
    """SPEC-ORDER：order=created&dir=desc → 最新创建的戊（2024-05）排最前。"""
    items = _gather(client, admin_headers, order="created", dir="desc")
    names = [i["name"] for i in items]
    assert names[0] == "排序测试戊公司"
    # created 是第 5 键：数据按创建时间倒序
    assert names == [
        "排序测试戊公司", "排序测试丁公司", "排序测试丙公司", "排序测试乙公司", "排序测试甲公司",
    ]


def test_order_intent(client, admin_headers, seeded):
    """SPEC-ORDER：order=intent——seed 全为 0（默认），走 intent 键应回退到
    稳定尾键 id desc（同值行按 id 降序，分页不重不漏）。"""
    items = _gather(client, admin_headers, order="intent", dir="desc")
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)


def test_illegal_order_falls_back_to_default(client, admin_headers, seeded):
    """SPEC-ORDER：非法 order（乱值/注入尝试）回退默认 icp desc——与不传参逐字节一致。"""
    base = _list(client, admin_headers, page_size=20)
    for bad in ("hack", "1=1", "name; DROP TABLE", "registered_capital_wan"):
        d = _list(client, admin_headers, order=bad, dir="desc", page_size=20)
        assert [(i["id"], i["name"]) for i in d["items"]] == [
            (i["id"], i["name"]) for i in base["items"]
        ], f"非法 order={bad!r} 未回退默认序"
    # 非法 dir 同理按 desc 处理（capital desc 序下 seed 首行=戊）
    items = _gather(client, admin_headers, order="capital", dir="sideways")
    assert [i["name"] for i in items][0] == "排序测试戊公司"


def test_order_url_combo_and_page(client, admin_headers, seeded):
    """SPEC-ORDER：order+dir 与 URL 组合（筛选+分页）；page_size=2 强制分页时
    排序跨页连续——全库序而非页内序（回归本 SPEC 的动机 bug）。

    断言取"跨页拼接后的过滤子序列"而非单页内容：全量跑时库里有其他用例的
    企业（NULL 资本会排在 asc 最前），只有跨页全收才能确定性验证相对序。
    """
    gathered: list[tuple[str, float | None]] = []
    total = -1
    for p in range(1, 101):  # 上限防呆：page_size=2 全库也就几十页
        d = _list(client, admin_headers, order="capital", dir="asc",
                  province="江苏省", page_size=2, page=p)
        total = d["total"]
        gathered.extend(
            (i["name"], i["registered_capital_wan"])
            for i in d["items"] if i["name"].startswith("排序测试")
        )
        if p * 2 >= total:
            break
    names = [n for n, _ in gathered]
    # capital asc 全库序：甲(100)→乙(500)→丙(1000)→丁(3000)→戊(5000)，
    # 跨页连续且完整（5 家一个不少——漏了就是页内排序/分页错位的回归）
    assert names == [
        "排序测试甲公司", "排序测试乙公司", "排序测试丙公司", "排序测试丁公司", "排序测试戊公司",
    ]
    capitals = [c for _, c in gathered]
    assert capitals == sorted(capitals, key=lambda x: (x is None, x))
    # 翻页不影响 total（首尾两次请求一致）
    d_last = _list(client, admin_headers, order="capital", dir="asc",
                   province="江苏省", page_size=2, page=1)
    assert d_last["total"] == total
