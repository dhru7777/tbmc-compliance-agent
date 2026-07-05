"""PostgreSQL persistence for KYB verification records."""

from app.db.database import get_db, init_db, is_db_enabled
from app.db.models import KybVerification

__all__ = ["KybVerification", "get_db", "init_db", "is_db_enabled"]
