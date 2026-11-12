"""MySQL persistence: the engine and the users / runs / run_rows tables.

The connection string comes from the DATABASE_URL environment variable (Dokku's MySQL plugin sets it as
"mysql://user:password@host:port/name"); "mysql://" is rewritten to "mysql+pymysql://" so SQLAlchemy uses PyMySQL.
Tables are created if missing. Every query in the app goes through SQLAlchemy expressions, so values are always sent
as bound parameters, never pasted into SQL text. Tests pass "sqlite://" for an in-memory database.
"""
from __future__ import annotations

import os

from sqlalchemy import (DECIMAL, Column, Date, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
                        create_engine, event, func, text)
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("display_name", String(100)),
    Column("is_demo", Integer().with_variant(mysql.TINYINT(1), "mysql"), nullable=False, server_default=text("0")),
    Column("created_at", DateTime, server_default=func.current_timestamp()),
)

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(150)),
    Column("as_of", Date),
    Column("n_rows", Integer),
    Column("created_at", DateTime, server_default=func.current_timestamp()),
)

run_rows = Table(
    "run_rows", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("row_id", Integer),
    Column("address", String(200)),
    Column("zip", String(10)),
    Column("asking_price", DECIMAL(12, 2)),
    Column("status", String(30)),
    Column("reason", String(40)),
    Column("matched_address", String(200)),
    Column("arv", DECIMAL(12, 2)),
    Column("comp_count", Integer),
    Column("spread", DECIMAL(12, 2)),
    Column("reno_likely_with_contingency", DECIMAL(12, 2)),
    Column("net_profit", DECIMAL(12, 2)),
    Column("roi_pct", DECIMAL(8, 3)),
    Column("rank_in_run", Integer),
    Column("comps_json", Text().with_variant(mysql.LONGTEXT(), "mysql")),
)


def database_url(raw: str | None = None) -> str | None:
    """DATABASE_URL (or `raw`) with "mysql://" rewritten for PyMySQL; None when it is not set."""
    raw = os.environ.get("DATABASE_URL") if raw is None else raw
    if not raw:
        return None
    return "mysql+pymysql://" + raw[len("mysql://"):] if raw.startswith("mysql://") else raw


def make_engine(url: str) -> Engine:
    """Engine for `url`, with the tables created if missing. hide_parameters keeps bound values (such as password
    hashes) out of error messages and logs."""
    if url.startswith("sqlite"):
        engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False}, hide_parameters=True)

        @event.listens_for(engine, "connect")
        def _foreign_keys(dbapi_con, _):          # SQLite enforces ON DELETE CASCADE only with this switched on
            dbapi_con.execute("PRAGMA foreign_keys = ON")
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_recycle=280, hide_parameters=True)
    metadata.create_all(engine)                   # CREATE TABLE only for tables that do not exist yet
    return engine
