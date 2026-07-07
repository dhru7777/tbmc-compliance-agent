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
        url = url.replace("postgres://", "postgresql://", 1)
    # Mac: localhost resolves to ::1 and can hit system Postgres instead of Docker on 127.0.0.1
    if "@localhost:" in url:
        url = url.replace("@localhost:", "@127.0.0.1:", 1)
    if "@localhost/" in url:
        url = url.replace("@localhost/", "@127.0.0.1/", 1)
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
            connect_args={"connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5"))},
        )
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def init_db() -> bool:
    """Create tables if DATABASE_URL is configured. Returns True if ready."""
    global _engine, _SessionLocal
    try:
        engine = get_engine()
        if engine is None:
            return False
        Base.metadata.create_all(bind=engine)
        _migrate_verification_columns(engine)
        return ping_db()
    except Exception as exc:
        print(
            f"WARNING: Postgres init failed ({exc}) — "
            "verification will run but records will not persist. "
            "Fix DATABASE_URL or run: cd backend && docker compose up -d"
        )
        _engine = None
        _SessionLocal = None
        return False


def ping_db() -> bool:
    engine = get_engine()
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _migrate_verification_columns(engine: Engine) -> None:
    """Add JSONB credential columns to existing deployments (create_all is non-destructive)."""
    statements = [
        "ALTER TABLE kyb_verifications ADD COLUMN IF NOT EXISTS layered_credentials JSONB",
        "ALTER TABLE kyb_verifications ADD COLUMN IF NOT EXISTS kya_proof JSONB",
    ]
    try:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
    except Exception as exc:
        print(f"WARNING: credential column migration skipped: {exc}")


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
