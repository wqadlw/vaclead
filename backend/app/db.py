"""数据库引擎与会话 — SQLite WAL + FTS5 全文索引（契约见 docs/SPEC.md §1）。

所有 SQL 均为静态字面量（DDL/PRAGMA），不含任何用户输入拼接。
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, PoolSetting, User

_DB_PATH = os.environ.get(
    "VACLEAD_DB", os.path.join(os.path.dirname(__file__), "..", "..", "data", "vaclead.db")
)
_DB_PATH = os.path.abspath(_DB_PATH)
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

ENGINE = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)

SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


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


def init_db(seed: bool = True) -> None:
    """建表 + FTS5 虚表与同步触发器（全部静态 DDL）+ 种子用户。"""
    Base.metadata.create_all(ENGINE)
    with ENGINE.connect() as conn:
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
