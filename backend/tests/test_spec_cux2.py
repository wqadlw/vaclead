"""SPEC-CUX2 回归：联系人列表分页正确化 + 批量标记。

核心钉死：旧版 `.limit(2000)` 采样 + Python 过滤导致"最新 2000 条之外的
可拨联系人永远翻不到"的数据完整性 bug——用 250 条可拨目标（先入库、id 小）
+ 2000 条无号填充（后入库、id 大）把目标挤出旧版 id desc 采样窗口：
旧实现采样窗内 0 条目标 → total=0；新实现 SQL 过滤先行 → 250 条翻页全可达。
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-cux2-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Contact,
    Enterprise,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Contact, AuditLog]

_PAGE = 50  # 与前端 PAGE_SIZE 对齐的翻页页大小


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            row = s.query(m).with_entities(m.id).order_by(m.id.desc()).first()
            snap[m.__tablename__] = (row or (0,))[0]
    yield
    with SessionLocal() as s:
        for m in _ISOLATED_MODELS:
            s.query(m).filter(m.id > snap[m.__tablename__]).delete()
        s.commit()


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
def seeded():
    """模块级种子（插入顺序即 id 顺序，目标在前、填充在后）：

    - 250 条可拨目标（CUX2目标厂，全 pending）——旧版 2000 采样窗外主体；
    - 2000 条无号填充（CUX2填充厂）——把目标挤出旧版采样窗口；
    - 过滤四态种子（CUX2过滤试验厂 + 已删企业）；
    - 排序四键种子（CUX2排序甲/乙/丙，test_spec_cux 四态平移）；
    - 批量标记种子（CUX2批量厂）。
    """
    with SessionLocal() as s:
        # —— 分页主体：目标 250 条（先插入 → id 小，会被后插入的填充挤出旧采样）——
        target_ent = Enterprise(name="CUX2目标厂", source="poi", intent_score=42)
        s.add(target_ent)
        s.flush()
        targets = [
            Contact(
                enterprise_id=target_ent.id,
                name=f"CUX2目标{i:03d}",
                role="buyer_contact",
                phones_json=f'["1395555{i:04d}"]',
            )
            for i in range(1, 251)
        ]
        s.add_all(targets)
        s.flush()
        target_ids = [c.id for c in targets]
        # —— 填充 2000 条（后插入 → id 大，恰好占满旧版 2000 采样窗）——
        filler_ent = Enterprise(name="CUX2填充厂", source="poi", intent_score=1)
        s.add(filler_ent)
        s.flush()
        s.add_all([
            Contact(
                enterprise_id=filler_ent.id,
                name=f"CUX2填充{i:04d}",
                role="legal_rep",
            )
            for i in range(2000)
        ])
        # —— 过滤四态种子：联系人名不带厂名前缀，双列 LIKE 可分别断言 ——
        fent = Enterprise(name="CUX2过滤试验厂", source="poi", intent_score=60)
        gone = Enterprise(name="CUX2过滤已删厂", source="poi", deleted_at="2026-11-01T00:00:00")
        s.add_all([fent, gone])
        s.flush()
        f_pend = Contact(
            enterprise_id=fent.id, name="CUX2滤王工", role="buyer_contact",
            phones_json='["13966660001"]',
        )
        f_conn = Contact(
            enterprise_id=fent.id, name="CUX2滤钱工", role="buyer_contact",
            phones_json='["13966660002"]', call_result="connected",
            called_at="2026-11-02T09:00:00",
        )
        f_inter = Contact(
            enterprise_id=fent.id, name="CUX2滤孙工", role="buyer_contact",
            phones_json='["13966660003"]', call_result="interested",
            called_at="2026-11-03T09:00:00",
        )
        f_nophone = Contact(enterprise_id=fent.id, name="CUX2滤无名氏", role="legal_rep")
        f_del = Contact(
            enterprise_id=gone.id, name="CUX2滤已删", role="legal_rep",
            phones_json='["13966660004"]',
        )
        # —— 排序四键种子（语义平移自 test_spec_cux：四态互不相同）——
        # A：高意图已拨（11-01）；B：最高意图但无号码；C：中意图最近拨（11-05）
        eA = Enterprise(name="CUX2排序甲公司", source="poi", intent_score=90)
        eB = Enterprise(name="CUX2排序乙公司", source="poi", intent_score=95)
        eC = Enterprise(name="CUX2排序丙公司", source="poi", intent_score=70)
        s.add_all([eA, eB, eC])
        s.flush()
        oA = Contact(
            enterprise_id=eA.id, name="CUX2排序甲王工", role="buyer_contact",
            phones_json='["13977770001"]', call_result="connected",
            called_at="2026-11-01T09:00:00",
        )
        oB = Contact(enterprise_id=eB.id, name="CUX2排序乙李总", role="legal_rep")
        oC = Contact(
            enterprise_id=eC.id, name="CUX2排序丙张经理", role="buyer_contact",
            phones_json='["13977770003"]', call_result="no_answer",
            called_at="2026-11-05T09:00:00",
        )
        # —— 批量标记种子 ——
        bent = Enterprise(name="CUX2批量厂", source="poi", intent_score=20)
        s.add(bent)
        s.flush()
        b1 = Contact(
            enterprise_id=bent.id, name="CUX2批量赵一", role="buyer_contact",
            phones_json='["13988880001"]',
        )
        b2 = Contact(
            enterprise_id=bent.id, name="CUX2批量钱二", role="buyer_contact",
            phones_json='["13988880002"]',
        )
        b3 = Contact(
            enterprise_id=bent.id, name="CUX2批量孙三", role="buyer_contact",
            phones_json='["13988880003"]',
        )
        s.add_all([f_pend, f_conn, f_inter, f_nophone, f_del, oA, oB, oC, b1, b2, b3])
        s.commit()
        return {
            "target_ids": target_ids,
            "filter": {
                "pend": f_pend.id, "conn": f_conn.id, "inter": f_inter.id,
                "nophone": f_nophone.id, "del": f_del.id,
            },
            "order": {"A": oA.id, "B": oB.id, "C": oC.id},
            "batch": [b1.id, b2.id, b3.id],
        }


def _get(client, admin_headers, params: str) -> dict:
    r = client.get(f"/api/contacts?{params}", headers=admin_headers)
    assert r.status_code == 200
    return r.json()


def test_cux2_pagination_reaches_all_250(client, admin_headers, seeded):
    """核心回归：250 条目标（被 2000 无号填充挤出旧 2000 采样窗）翻页全可达。

    旧实现：采样窗内 0 条目标 → has_phone=1 时 total=0；
    新实现：SQL 过滤先行 → total 与 SQL COUNT 一致，offset 翻页 250 条全可达。
    """
    # 独立 SQL COUNT（与新实现同一过滤谓词）：目标厂可拨联系人总数
    with SessionLocal() as s:
        sql_count = (
            s.query(func.count())
            .select_from(Contact)
            .join(Enterprise, Enterprise.id == Contact.enterprise_id)
            .filter(
                Enterprise.name == "CUX2目标厂",
                Contact.phones_json.is_not(None),
                Contact.phones_json.notin_(("", "[]")),
            )
            .scalar()
        )
        assert sql_count == 250  # 种子自检：250 条目标全可拨

    # total 与 SQL COUNT 一致
    d = _get(client, admin_headers, f"q=CUX2目标厂&limit={_PAGE}&offset=0")
    assert d["total"] == 250 == sql_count
    assert len(d["items"]) == _PAGE

    # page=2（offset=50）内容精确：默认序（同意图/全未拨）= id 降序切片
    expect_all = sorted(seeded["target_ids"], reverse=True)
    page2 = _get(client, admin_headers, f"q=CUX2目标厂&limit={_PAGE}&offset=50")
    assert [i["id"] for i in page2["items"]] == expect_all[50:100]

    # offset 翻页：5 页恰好覆盖 250，无重复无遗漏，末页之后为空
    seen: list[int] = []
    for off in range(0, 250, _PAGE):
        d = _get(client, admin_headers, f"q=CUX2目标厂&limit={_PAGE}&offset={off}")
        assert d["total"] == 250
        assert len(d["items"]) == _PAGE
        seen.extend(i["id"] for i in d["items"])
    assert seen == expect_all  # 全量可达且每页次序衔接（id desc）
    assert _get(client, admin_headers, f"q=CUX2目标厂&limit={_PAGE}&offset=250")["items"] == []

    # 不带 q 的完整翻页（旧实现此场景 total=0）：翻到拉满，250 目标全在列
    collected: list[int] = []
    total = None
    for _ in range(30):  # 翻页上限护栏
        d = _get(client, admin_headers, f"has_phone=1&limit={_PAGE}&offset={len(collected)}")
        total = d["total"]
        collected.extend(i["id"] for i in d["items"])
        if len(collected) >= total or d["items"] == []:
            break
    assert total is not None and total >= 250
    assert len(collected) == len(set(collected)) == total  # 无重复、翻页拉满
    assert set(seeded["target_ids"]) <= set(collected)  # 250 条目标一条不少


def test_cux2_filters_sql(client, admin_headers, seeded):
    """过滤 SQL 化回归：call_status 三态 / has_phone / q 双列 LIKE（平移断言）。"""
    f = seeded["filter"]

    # untouched：可拨 且 未拨（无号/已拨都不进；has_phone=0 也一样——口径带可拨前件）
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "call_status=untouched&q=CUX2过滤试验厂&limit=200")["items"]
    } == {"CUX2滤王工"}
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "call_status=untouched&has_phone=0&q=CUX2过滤试验厂&limit=200")["items"]
    } == {"CUX2滤王工"}

    # touched：可拨 且 已拨（pending 不算 touched）
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "call_status=touched&q=CUX2过滤试验厂&limit=200")["items"]
    } == {"CUX2滤钱工", "CUX2滤孙工"}

    # 其他值 → call_result 精确等值
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "call_status=connected&q=CUX2过滤试验厂&limit=200")["items"]
    } == {"CUX2滤钱工"}
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "call_status=interested&q=CUX2过滤试验厂&limit=200")["items"]
    } == {"CUX2滤孙工"}

    # has_phone=0：纳入无号联系人；已删企业联系人在任何筛选下都不出现
    names0 = {
        i["name"] for i in _get(client, admin_headers,
                                "has_phone=0&q=CUX2过滤试验厂&limit=200")["items"]
    }
    assert names0 == {"CUX2滤王工", "CUX2滤钱工", "CUX2滤孙工", "CUX2滤无名氏"}
    assert "CUX2滤已删" not in {
        i["name"] for i in _get(client, admin_headers, "has_phone=0&limit=200")["items"]
    }

    # q 双列 LIKE：企业名命中（联系人名不含厂名前缀）→ 全部 4 条
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "has_phone=0&q=CUX2过滤试验厂&limit=200")["items"]
    } == names0
    # q 双列 LIKE：联系人名命中 → 单条
    assert {
        i["name"] for i in _get(client, admin_headers,
                                "has_phone=0&q=滤王工&limit=200")["items"]
    } == {"CUX2滤王工"}

    # 内部字段由 SQL 结果回填：可拨未拨 / 已拨标记（has_phone=0 纳入无号行）
    d = _get(client, admin_headers, "has_phone=0&q=CUX2过滤试验厂&limit=200")
    flags = {i["name"]: (i["_dialable"], i["_untouched"]) for i in d["items"]}
    assert flags["CUX2滤王工"] == (True, True)
    assert flags["CUX2滤钱工"] == (True, False)
    assert flags["CUX2滤无名氏"] == (False, False)


def test_cux2_order_modes(client, admin_headers, seeded):
    """排序 SQL 化回归：四态互不相同（test_spec_cux 同种子平移断言）。"""
    ids = set(seeded["order"].values())

    def mine(params: str) -> list[int]:
        d = _get(client, admin_headers, f"has_phone=0&limit=200&q=CUX2排序&{params}")
        return [i["id"] for i in d["items"] if i["id"] in ids]

    o = seeded["order"]
    # 默认智能推荐序（order 缺省）：可拨 → 意图分 → 未拨 → id
    # B 意图分 95 最高但无号码 → 可拨优先把它垫尾
    assert mine("") == [o["A"], o["C"], o["B"]]
    # 显式空串与缺省一致；非法值回退默认（白名单外不覆写）
    assert mine("order=") == [o["A"], o["C"], o["B"]]
    assert mine("order=bogus") == [o["A"], o["C"], o["B"]]
    # newest：id 降序（最近入库先打）
    assert mine("order=newest") == [o["C"], o["B"], o["A"]]
    # intent：企业意图分降序（B 95 分排头，与默认序分化）
    assert mine("order=intent") == [o["B"], o["A"], o["C"]]
    # recent_called：已拨在前按拨打时间倒序（C 11-05 > A 11-01），未拨 B 垫尾
    assert mine("order=recent_called") == [o["C"], o["A"], o["B"]]


def test_cux2_batch_call_result(client, admin_headers, seeded):
    """批量标记：多 ids 更新 + called_at 写入 + 仅传入 ids + 非法 result 422。"""
    b1, b2, b3 = seeded["batch"]

    # 多 ids 更新：changed=3，call_result 与 called_at（utcnow）都落库
    r = client.post(
        "/api/contacts/batch-call-result",
        json={"ids": [b1, b2, b3], "result": "interested"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"changed": 3}
    with SessionLocal() as s:
        rows = {c.id: c for c in s.query(Contact).filter(Contact.id.in_([b1, b2, b3])).all()}
        for cid in (b1, b2, b3):
            assert rows[cid].call_result == "interested"
            assert rows[cid].called_at and "T" in rows[cid].called_at  # utcnow ISO 写入

    # 仅更新传入 ids：不存在的 id 不计入 changed
    r = client.post(
        "/api/contacts/batch-call-result",
        json={"ids": [b1, 99999999], "result": "no_answer"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"changed": 1}

    # 非法 result：422（七态白名单外）
    r = client.post(
        "/api/contacts/batch-call-result",
        json={"ids": [b1], "result": "bogus"},
        headers=admin_headers,
    )
    assert r.status_code == 422

    # ids 缺失 / 空数组 / 类型非法：一律 422
    for bad in ({}, {"result": "interested"}, {"ids": [], "result": "interested"},
                {"ids": ["b1"], "result": "interested"}):
        r = client.post("/api/contacts/batch-call-result", json=bad, headers=admin_headers)
        assert r.status_code == 422

    # 未登录：401
    r = client.post(
        "/api/contacts/batch-call-result",
        json={"ids": [b1], "result": "interested"},
    )
    assert r.status_code == 401
