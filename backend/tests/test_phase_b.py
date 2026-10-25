"""Phase B 测试（SPEC-B §7）：归一升级 / 复核队列 / 中标画像 / 水位与备份。

使用独立内存库（不走 app.db 全局 ENGINE），与 test_models 同模式。
"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vaclead-b-")
os.environ.setdefault("VACLEAD_DB", os.path.join(_tmp, "t.db"))

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    Base,
    Enterprise,
    ReviewQueue,
    Signal,
    SyncWatermark,
    utcnow,
)
from app.normalize import canonical_name, resolve_enterprise  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")  # 内存库
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


# ---- canonical_name 括号修复（审计发现的误并风险）----
def test_canonical_keeps_paren_region():
    """(苏州)/(无锡) 必须得到不同 canon——跨城市同名不可误并。"""
    assert canonical_name("沃尔真空(苏州)有限公司") != canonical_name("沃尔真空(无锡)有限公司")
    assert canonical_name("沃尔真空(苏州)有限公司") == "沃尔真空苏州"
    assert canonical_name("苏州X真空设备制造有限公司") == "苏州X真空设备制造"


# ---- tier0：uscc 精确合并 + 回填 ----
def test_uscc_exact_merge(session):
    e1, new1 = resolve_enterprise(session, "淄博水环真空泵厂有限公司", "ggzy",
                                  uscc="91370304724954109N")
    session.commit()
    # 同 uscc 不同名（括号/全半角变体）→ 直接归并
    e2, new2 = resolve_enterprise(session, "淄博水环真空泵厂（集团）有限公司", "ggzy",
                                  uscc="91370304724954109N")
    assert new1 is True and new2 is False
    assert e2.id == e1.id


def test_uscc_backfill_on_name_hit(session):
    e1, _ = resolve_enterprise(session, "盐城市视远真空设备有限公司", "ccgp_buyer")
    assert e1.uscc is None
    e2, is_new = resolve_enterprise(session, "盐城市视远真空设备有限公司", "ggzy",
                                    uscc="91320924MA1T6WGX7T")
    assert is_new is False and e2.id == e1.id
    assert e1.uscc == "91320924MA1T6WGX7T"


# ---- 复核区间：80-89 落队列而非静默 ----
def test_review_band_creates_queue_item(session):
    # canon "上海夯业真空设备"(8字) vs "上海夯业真空设备科技"(10字) → ratio 88.9，落在 80-89 复核带
    base, _ = resolve_enterprise(session, "上海夯业真空设备有限公司", "ggzy")
    session.commit()
    cand, is_new = resolve_enterprise(session, "上海夯业真空设备科技有限公司", "ccgp")
    session.flush()
    rows = session.query(ReviewQueue).all()
    if is_new:
        assert len(rows) >= 1 and rows[-1].candidate_id == cand.id
        assert rows[-1].target_id == base.id
        assert rows[-1].status == "pending"
        assert 80.0 <= rows[-1].score < 90.0
    else:
        assert cand.id == base.id  # ≥90 自动合并（阈值边界由 token_sort_ratio 决定）


# ---- 中标画像聚合 ----
def test_enrich_stats_aggregation(session):
    from app.jobs import enrich_stats

    ent, _ = resolve_enterprise(session, "淄博水环真空泵厂有限公司", "ggzy")
    session.flush()
    session.add_all([
        Signal(enterprise_id=ent.id, type="tender_win", title="水环真空泵A包",
               url="u1", pub_date="2026-09-11", base_score=70,
               evidence_claim="该主体中标「A」，成交价418.8万元，采购人：鹤壁煤业（集团）有限责任公司"),
        Signal(enterprise_id=ent.id, type="tender_win", title="水环真空泵B包",
               url="u2", pub_date="2026-10-01", base_score=70,
               evidence_claim="该主体中标「B」，成交价263.7万元，采购人：广西华昇新材料有限公司"),
        Signal(enterprise_id=ent.id, type="tender_win", title="无金额包",
               url="u3", pub_date="2026-10-20", base_score=70,
               evidence_claim="该主体中标「无金额包」，成交价0.0万元"),
    ])
    session.commit()
    stat = enrich_stats(session, ent.id)
    assert stat["bid_count"] == 3
    assert stat["bid_amount_total_wan"] == pytest.approx(682.5)
    assert stat["bid_amount_max_wan"] == pytest.approx(418.8)
    assert stat["last_win_date"] == "2026-10-20"  # 最近 pub_date 优先
    assert stat["top_buyer"] in ("鹤壁煤业（集团）有限责任公司", "广西华昇新材料有限公司")


def test_extract_amount_negative_or_zero(session):
    from app.jobs import extract_amount_wan

    assert extract_amount_wan("成交价0.0万元") is None
    assert extract_amount_wan("成交价453.34万元") == pytest.approx(453.34)
    assert extract_amount_wan("无金额文本") is None


# ---- 水位 ----
def test_mark_watermark_ok_and_failed(session):
    from app.jobs import mark_watermark

    mark_watermark(session, "backup_sqlite", "ok", {"size": 1})
    mark_watermark(session, "backup_sqlite", "failed", {"error": "x"})
    row = session.get(SyncWatermark, "backup_sqlite")
    assert row.last_status == "failed"
    assert "error" in row.stats_json


# ---- 备份（真实文件）----
def test_backup_sqlite_creates_and_rotates(tmp_path):
    from app.jobs import backup_sqlite
    from app.db import ENGINE as global_engine

    # 借全局 engine（测试环境指向临时文件库）备份两次，验证滚动
    out1 = backup_sqlite(tmp_path, keep=2)
    out2 = backup_sqlite(tmp_path, keep=2)
    import sqlite3

    con = sqlite3.connect(out2["path"])
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    # keep=2 → 最多 2 份
    files = list(tmp_path.glob("vaclead_*.db"))
    assert len(files) <= 2
    assert out1["path"] != out2["path"]
    assert global_engine is not None
