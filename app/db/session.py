"""SQLAlchemy engine wiring.

Two engines, both lazy:
- read_engine via the read-only Postgres user (used for every LLM-generated query).
- admin_engine for one-time schema introspection at startup.

Engines are created on first access so importing this module does not
require a live database; tests can inspect the code without Postgres.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings


def _make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


_read_engine: Engine | None = None
_admin_engine: Engine | None = None


def _get_read_engine() -> Engine:
    global _read_engine
    if _read_engine is None:
        _read_engine = _make_engine(get_settings().database_url)
    return _read_engine


def _get_admin_engine() -> Engine:
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = _make_engine(get_settings().admin_database_url)
    return _admin_engine


def __getattr__(name: str):
    if name == "read_engine":
        return _get_read_engine()
    if name == "admin_engine":
        return _get_admin_engine()
    raise AttributeError(name)


@contextmanager
def readonly_connection(timeout_s: int | None = None) -> Iterator:
    """Yield a connection in a read-only transaction that always rolls back.

    Combined with the SELECT-only DB user, this is belt-and-braces: even
    a SELECT with side effects cannot commit anything.
    """
    settings = get_settings()
    with _get_read_engine().connect() as conn:
        ms = (timeout_s if timeout_s is not None else settings.query_timeout_s) * 1000
        conn.execute(text(f"SET LOCAL statement_timeout = {ms}"))
        conn.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield conn
        finally:
            conn.rollback()
