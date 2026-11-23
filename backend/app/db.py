"""Engine, session factory and schema creation.

SQLite via SQLAlchemy: no daemon or port to fail on a reviewer's machine, and
swapping DATABASE_URL is the only change needed to move to Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.models import Base


def _build_engine(url: str = DATABASE_URL) -> Engine:
    is_sqlite = url.startswith("sqlite")

    engine = create_engine(
        url,
        # Uvicorn serves from a threadpool and SQLite rejects cross-thread
        # connection use by default. Safe because SQLAlchemy hands out one
        # connection per checkout. Gated on the scheme since psycopg would
        # reject this argument outright.
        connect_args={"check_same_thread": False} if is_sqlite else {},
        future=True,
    )

    if not is_sqlite:
        return engine

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        # SQLite enforces foreign keys per connection and only on request;
        # without this, deleting a delivery orphans its facts and check results.
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL keeps reads serving while an ingestion run writes.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


engine = _build_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create missing tables. Safe to call repeatedly."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on exception.

    The pipeline wraps one delivery per scope, which is what makes a delivery
    either fully replaced or untouched, never half-written.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Routes are read-only, so no commit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
