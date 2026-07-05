"""Database engine and session factory."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def is_db_enabled() -> bool:
    return bool(database_url())


def _normalize_url(url: str) -> str:
    # Railway/Heroku sometimes provide postgres:// — SQLAlchemy 2 prefers postgresql://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_engine() -> Engine | None:
    global _engine, _SessionLocal
    url = database_url()
    if not url:
        return None
    if _engine is None:
        _engine = create_engine(
            _normalize_url(url),
            pool_pre_ping=True,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        )
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def init_db() -> bool:
    """Create tables if DATABASE_URL is configured. Returns True if ready."""
    engine = get_engine()
    if engine is None:
        return False
    Base.metadata.create_all(bind=engine)
    return ping_db()


def ping_db() -> bool:
    engine = get_engine()
    if engine is None:
        return False
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


@contextmanager
def get_db() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        get_engine()
    if _SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
