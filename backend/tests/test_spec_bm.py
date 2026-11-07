"""SPEC-BM 回归：老户雷达轮——在管客户新信号零配置推送负责人。"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "VACLEAD_DB", os.path.join(tempfile.mkdtemp(prefix="vaclead-spec-bm-"), "t.db")
)
os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")

import pytest  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.jobs import owner_signal_sweep  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,  # noqa: E402
    Enterprise,
    Lead,
    Notification,
    Signal,
    SyncWatermark,
    User,
)

init_db(seed=False)

_ISOLATED_MODELS = [Enterprise, Lead, Signal, Notification, User, AuditLog]


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
        # 水位表主键是 job_name（非 id）：只清本模块水位，防污染其他模块断言
        s.query(SyncWatermark).filter(
            SyncWatermark.job_name == "owner_signal_sweep"
        ).delete()
        s.commit()


def _run_sweep() -> dict:
    """生产同款路径：_run_job_with_watermark 执行 + 落水位。"""
    from app.main import _run_job_with_watermark

    _run_job_with_watermark("owner_signal_sweep", owner_signal_sweep)
    with SessionLocal() as s:
        row = s.get(SyncWatermark, "owner_signal_sweep")
        return {"status": row.last_status, **json.loads(row.stats_json or "{}")}


def _count_owner_signal(user_id: int | None = None) -> int:
    with SessionLocal() as s:
        q = s.query(Notification).filter(Notification.ref_type == "owner_signal")
        if user_id is not None:
            q = q.filter(Notification.user_id == user_id)
        return q.count()


def test_bm1_baseline_no_flood():
    """首跑无水位 → 基线模式：不推任何通知，只记游标。"""
    with SessionLocal() as s:
        s.query(SyncWatermark).filter(
            SyncWatermark.job_name == "owner_signal_sweep"
        ).delete()
        s.commit()
    before = _count_owner_signal()
    stats = _run_sweep()
    assert stats["status"] == "ok"
    assert stats["baseline"] is True
    assert stats["notified"] == 0
    assert _count_owner_signal() == before


def test_bm1_push_by_lead_status():
    """在管客户新信号 → 按线索状态分层推送负责人；公海/已删不推。"""
    with SessionLocal() as s:
        u1 = User(username="bm_owner1", password_hash="x", role="sales")
        u2 = User(username="bm_owner2", password_hash="x", role="sales")
        won_e = Enterprise(name="BM老户机械有限公司", source="poi")
        intent_e = Enterprise(name="BM意向精密设备有限公司", source="poi")
        follow_e = Enterprise(name="BM跟进电气有限公司", source="poi")
        public_e = Enterprise(name="BM公海 untouched 有限公司", source="poi")
        deleted_e = Enterprise(
            name="BM已删半导体有限公司", source="poi",
            deleted_at="2026-01-01T00:00:00",
        )
        s.add_all([u1, u2, won_e, intent_e, follow_e, public_e, deleted_e])
        s.flush()
        s.add_all([
            Lead(enterprise_id=won_e.id, status="won", owner_id=u1.id, tenant_id=1),
            Lead(enterprise_id=intent_e.id, status="intent", owner_id=u1.id, tenant_id=1),
            Lead(enterprise_id=follow_e.id, status="following", owner_id=u2.id, tenant_id=1),
            # 公海：status=new 且无 owner → 不推
            Lead(enterprise_id=public_e.id, status="new", owner_id=None, tenant_id=1),
            # 已删企业的在管线索 → 不推
            Lead(enterprise_id=deleted_e.id, status="won", owner_id=u1.id, tenant_id=1),
        ])
        long_evidence = "该公告要求采购真空镀膜机两套，" + "配置细节补充" * 20
        s.add_all([
            Signal(enterprise_id=won_e.id, type="tender", title="BM老户中标公示",
                   url="https://example.com/bm-won", evidence_claim=long_evidence),
            Signal(enterprise_id=intent_e.id, type="tender", title="BM意向采购公告",
                   url="https://example.com/bm-intent"),
            Signal(enterprise_id=follow_e.id, type="tender", title="BM跟进询价公告",
                   url="https://example.com/bm-follow"),
            Signal(enterprise_id=public_e.id, type="tender", title="BM公海新信号",
                   url="https://example.com/bm-public"),
            Signal(enterprise_id=deleted_e.id, type="tender", title="BM已删企业信号",
                   url="https://example.com/bm-deleted"),
        ])
        s.commit()
        u1_id, u2_id = u1.id, u2.id

    stats = _run_sweep()
    assert stats["status"] == "ok"
    assert stats["notified"] == 3  # 公海/已删不推

    with SessionLocal() as s:
        # won → 老户新动向 + 证据句截断
        n_won = (
            s.query(Notification)
            .filter(Notification.ref_type == "owner_signal",
                    Notification.user_id == u1_id,
                    Notification.title.like("老户新动向%"))
            .one()
        )
        assert "BM老户机械" in n_won.title
        assert "BM老户中标公示" in n_won.body
        assert "…" in n_won.body and len(n_won.body) < 200
        # intent → 意向客户新信号
        n_intent = (
            s.query(Notification)
            .filter(Notification.ref_type == "owner_signal",
                    Notification.user_id == u1_id,
                    Notification.title.like("意向客户新信号%"))
            .one()
        )
        assert "BM意向精密" in n_intent.title
        # following → 跟进客户新信号（推给 u2）
        n_follow = (
            s.query(Notification)
            .filter(Notification.ref_type == "owner_signal",
                    Notification.user_id == u2_id)
            .one()
        )
        assert n_follow.title.startswith("跟进客户新信号")
        # 公海/已删企业全库无推送
        assert (
            s.query(Notification)
            .filter(Notification.ref_type == "owner_signal",
                    Notification.title.like("%BM公海%"))
            .count() == 0
        )
        assert (
            s.query(Notification)
            .filter(Notification.ref_type == "owner_signal",
                    Notification.title.like("%BM已删%"))
            .count() == 0
        )


def test_bm1_idempotent_and_cursor_recovery():
    """重扫幂等；失败轮游标丢失 → 全量重扫但不产生重复通知。"""
    total = _count_owner_signal()

    # 1) 正常重扫：游标未推进 → 0 新通知
    stats = _run_sweep()
    assert stats["notified"] == 0
    assert _count_owner_signal() == total

    # 2) 新信号到达 → 恰好一条新通知
    with SessionLocal() as s:
        e = s.query(Enterprise).filter(Enterprise.name == "BM老户机械有限公司").one()
        s.add(Signal(enterprise_id=e.id, type="tender", title="BM老户第二批采购",
                     url="https://example.com/bm-won-2"))
        s.commit()

    stats = _run_sweep()
    assert stats["notified"] == 1
    assert _count_owner_signal() == total + 1

    # 3) 模拟失败轮：stats 被错误覆盖丢游标 → 全量重扫仍无重复
    with SessionLocal() as s:
        row = s.get(SyncWatermark, "owner_signal_sweep")
        row.stats_json = json.dumps({"error": "boom"})
        s.commit()
    stats = _run_sweep()
    assert stats["status"] == "ok"
    assert _count_owner_signal() == total + 1  # 去重兜底，无重复轰炸
