"""数据库引擎与会话 — SQLite WAL + FTS5 全文索引（契约见 docs/SPEC.md §1）。

所有 SQL 均为静态字面量（DDL/PRAGMA），不含任何用户输入拼接。
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from .models import Base, PoolSetting, User

_DB_PATH = os.environ.get(
    "VACLEAD_DB", os.path.join(os.path.dirname(__file__), "..", "..", "data", "vaclead.db")
)
_DB_PATH = os.path.abspath(_DB_PATH)
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
#: 数据目录（备份等落盘产物统一放这里，SPEC-B §6）
from pathlib import Path as _Path  # noqa: E402

DATA_DIR = _Path(os.path.dirname(_DB_PATH))

ENGINE = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)

SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


@event.listens_for(SessionLocal, "do_orm_execute")
def _soft_delete_filter(execute_state) -> None:
    """SPEC-AM AM1：软删除全局过滤——所有 ORM select 自动排除已删企业。

    回收站/彻底删等管理查询用 statement.execution_options(include_deleted=True)
    显式豁免。join 到 Enterprise 的查询（bulletin/推荐等）同样被过滤
    （include_aliases=True）。
    """
    if not execute_state.is_select:
        return
    if execute_state.execution_options.get("include_deleted", False):
        return
    from .models import Enterprise

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            Enterprise,
            lambda cls: cls.deleted_at.is_(None),
            include_aliases=True,
        )
    )


@event.listens_for(ENGINE, "connect")
def _set_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _seed_defaults(session: Session) -> None:
    import bcrypt

    if session.query(User).count() == 0:
        for username, pwd, role, display in [
            ("admin", "admin123", "admin", "管理员"),
            ("sales", "sales123", "sales", "销售"),
        ]:
            session.add(
                User(
                    username=username,
                    password_hash=bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode(),
                    role=role,
                    display_name=display,
                )
            )
    if session.query(PoolSetting).count() == 0:
        session.add(PoolSetting(name="默认公海", recycle_days=30, receive_limit=0, remind_days=3))


#: 旧库补列清单（幂等 ALTER，PRAGMA table_info 检查后 ADD COLUMN，OPERATIONS §8）：
#: key=表名，value=[(列名, DDL 片段)]——只放"已有表追加的列"，新表走 create_all
_EVOLUTION_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "user": [
        ("email", "TEXT"),
        ("active", "INTEGER NOT NULL DEFAULT 1"),
        ("team_id", "INTEGER"),
    ],
    "lead": [
        ("lifecycle_stage", "TEXT NOT NULL DEFAULT 'lead'"),
        ("health_score", "INTEGER NOT NULL DEFAULT 50"),
    ],
    "campaign": [
        ("track_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "contact": [
        ("phones_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("call_result", "TEXT NOT NULL DEFAULT 'pending'"),
        ("called_at", "TEXT"),
    ],
    "enterprise": [
        ("deleted_at", "TEXT"),  # SPEC-AM AM1 软删除
        ("customer_role", "TEXT"),  # SPEC-AS AS2 客户角色
        ("prov_json", "TEXT NOT NULL DEFAULT '{}'"),  # SPEC-PROV 字段级溯源
        # SPEC-MULTI S2 工商深解析（chinacheck 快照）
        ("biz_status", "TEXT"),
        ("biz_status_at", "TEXT"),
        ("staff_size", "TEXT"),
        ("paid_in_capital_wan", "REAL"),
        ("former_names", "TEXT NOT NULL DEFAULT '[]'"),
    ],
    "product": [
        ("application_industries", "TEXT NOT NULL DEFAULT '[]'"),
    ],
    "signal": [
        ("amount_wan", "REAL"),  # SPEC-AMT 成交金额（万元）持久列——排序/惰性回填
    ],
    "watch": [
        ("snapshot_text", "TEXT"),  # SPEC-RADAR v2：正文全文（diff）
        ("prev_snapshot_text", "TEXT"),  # 变更前全文（diff 视图）
    ],
}


def _ensure_columns() -> None:
    """给已存在的表补缺失列（SPEC-N：User 组织化三列；旧真库无需手工 ALTER）。"""
    with ENGINE.connect() as conn:
        for table, cols in _EVOLUTION_COLUMNS.items():
            if not conn.dialect.has_table(conn, table):
                continue
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for col, ddl in cols:
                if col not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # SPEC-AMT：signal.amount_wan 自愈式回填——惰性回填排不出序（首次点
        # 金额排序时全表 NULL=没排）。列已在但存量 NULL 时补（幂等：无 NULL 不跑）。
        if "signal" in _EVOLUTION_COLUMNS and conn.dialect.has_table(conn, "signal"):
            pending = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM signal WHERE amount_wan IS NULL "
                "AND raw_excerpt LIKE '%成交价%'"
            ).fetchone()[0]
            if pending:
                _backfill_signal_amount(conn)
        conn.commit()


def _backfill_signal_amount(conn) -> int:
    """存量 signal 批量回填 amount_wan（SPEC-AMT：正则抽取 raw_excerpt，仅本次新列时跑）。"""
    import re

    from .models import AMOUNT_RE

    rows = conn.exec_driver_sql("SELECT id, raw_excerpt FROM signal").fetchall()
    n = 0
    for rid, excerpt in rows:
        m = AMOUNT_RE.search(excerpt or "")
        if m:
            conn.exec_driver_sql(
                "UPDATE signal SET amount_wan = ? WHERE id = ?",
                (float(m.group(1)), rid),
            )
            n += 1
    return n


def init_db(seed: bool = True) -> None:
    """建表 + FTS5 虚表与同步触发器（全部静态 DDL）+ 种子用户。"""
    Base.metadata.create_all(ENGINE)
    _ensure_columns()
    with ENGINE.connect() as conn:
        # SPEC-AK AK3：地理范围查询索引（26k 行全表 SCAN → 索引点查，EXPLAIN 实证）
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enterprise_geo ON enterprise (lat, lng)"
        ))
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS enterprise_fts USING fts5("
                "enterprise_id UNINDEXED, name, business_scope, address, industry_name)"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS enterprise_fts_ai AFTER INSERT ON enterprise "
                "BEGIN INSERT INTO enterprise_fts(enterprise_id, name, business_scope, "
                "address, industry_name) VALUES (new.id, new.name, "
                "COALESCE(new.business_scope,''), COALESCE(new.address,''), "
                "COALESCE(new.industry_name,'')); END"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS enterprise_fts_ad AFTER DELETE ON enterprise "
                "BEGIN DELETE FROM enterprise_fts WHERE enterprise_id = old.id; END"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS enterprise_fts_au AFTER UPDATE ON enterprise "
                "BEGIN DELETE FROM enterprise_fts WHERE enterprise_id = old.id; "
                "INSERT INTO enterprise_fts(enterprise_id, name, business_scope, address, "
                "industry_name) VALUES (new.id, new.name, "
                "COALESCE(new.business_scope,''), COALESCE(new.address,''), "
                "COALESCE(new.industry_name,'')); END"
            )
        )
        conn.commit()
    if seed:
        with SessionLocal() as session:
            _seed_defaults(session)
            session.commit()


def get_session() -> Iterator[Session]:
    """FastAPI 依赖。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
