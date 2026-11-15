"""SPEC-COLLECT 回归：信息采集门户——GET /api/tasks/recent 人话摘要。

隔离铁律：VACLEAD_DB setdefault 头两行 + VACLEAD_DISABLE_JOBS（conftest）。
"""
from __future__ import annotations

import os
import tempfile

pytest_tmp = tempfile.mkdtemp(prefix="vaclead-collect-")
os.environ.setdefault("VACLEAD_DB", os.path.join(pytest_tmp, "t.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import TaskCheckpoint  # noqa: E402

init_db(seed=False)

_ISOLATED_MODELS = [TaskCheckpoint]


@pytest.fixture(scope="module", autouse=True)
def _isolate_rows():
    """模块级快照清理：进前记数，出后清本模块触达的行。"""
    snap: dict[str, int] = {}
    with SessionLocal() as s:
        snap["checkpoint"] = s.query(TaskCheckpoint).count()
    yield
    with SessionLocal() as s:
        s.query(TaskCheckpoint).delete()
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


def test_tasks_recent_shape_and_summary(client, admin_headers):
    """recent：倒序 cap 20 + 四态人话摘要（done 数值映射/skipped/running/error）。"""
    from app.db import SessionLocal
    from app.models import TaskCheckpoint
    from app.api.tasks import utcnow

    with SessionLocal() as s:
        s.add_all([
            TaskCheckpoint(
                task_id="col-done", kind="ggzy", status="done", progress=100,
                state_json='{"entities": 12, "new_entities": 3, "bids": 5, "signals": 5}',
                updated_at=utcnow(),
            ),
            TaskCheckpoint(
                task_id="col-run", kind="poi", status="running", progress=40,
                state_json="{}", updated_at=utcnow(),
            ),
            TaskCheckpoint(
                task_id="col-skip", kind="sdgp", status="done", progress=100,
                state_json='{"skipped_disabled": true}', updated_at=utcnow(),
            ),
            TaskCheckpoint(
                task_id="col-err", kind="ccgp", status="error", progress=0,
                state_json='{"error": "拦截页连续出现"}', updated_at=utcnow(),
            ),
        ])
        s.commit()

    r = client.get("/api/tasks/recent", headers=admin_headers)
    assert r.status_code == 200, r.text
    items = r.json()["tasks"]
    by_id = {t["task_id"]: t for t in items}
    # 倒序：最新在前（col-err 最后插入 updated_at 同秒也按插入序近似——用存在性断言为主）
    for tid in ("col-done", "col-run", "col-skip", "col-err"):
        assert tid in by_id, f"{tid} 应在 recent 列表"
    done = by_id["col-done"]
    assert done["status"] == "done" and done["progress"] == 100
    assert "主体 12" in done["summary"] and "新面孔 3" in done["summary"]
    assert "中标 5" in done["summary"], "数值>0 前三键中文映射"
    assert by_id["col-skip"]["summary"] == "来源已停用，跳过"
    assert "进行中 40%" in by_id["col-run"]["summary"]
    assert by_id["col-err"]["summary"].startswith("失败：") and "拦截页" in by_id["col-err"]["summary"]

    # 未登录 401
    assert client.get("/api/tasks/recent").status_code == 401


def test_tasks_recent_empty(client, admin_headers):
    """空库（清空后）返回空数组不炸；cap 20 上限生效。"""
    from app.db import SessionLocal
    from app.models import TaskCheckpoint
    from app.api.tasks import utcnow

    r = client.get("/api/tasks/recent", headers=admin_headers)
    assert r.status_code == 200
    assert isinstance(r.json()["tasks"], list)

    # cap 20：插 25 条只回 20
    with SessionLocal() as s:
        for i in range(25):
            s.add(TaskCheckpoint(
                task_id=f"cap-{i}", kind="ggzy", status="done", progress=100,
                state_json="{}", updated_at=utcnow(),
            ))
        s.commit()
    r2 = client.get("/api/tasks/recent", headers=admin_headers)
    assert len(r2.json()["tasks"]) <= 20

    with SessionLocal() as s:
        s.query(TaskCheckpoint).delete()
        s.commit()
