"""Persist completed KYB verifications to PostgreSQL."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from app.db.database import get_db, is_db_enabled
from app.db.models import KybVerification

logger = logging.getLogger(__name__)

KYB_EXPIRY_DAYS = int(os.getenv("KYB_EXPIRY_DAYS", "180"))


def _build_reject_reason(scorecard: dict) -> str | None:
    items = scorecard.get("items") or []
    parts: list[str] = []
    for item in items:
        result = item.get("result")
        if result == "BLOCK":
            parts.append(f"BLOCK — {item.get('item')}: {item.get('detail', '')}")
        elif result == "FLAG":
            note = item.get("recommendation") or item.get("detail") or ""
            parts.append(f"REVIEW — {item.get('item')}: {note}")
    return "\n".join(parts) if parts else None


def _document_names(uploads: list[tuple[str, str, bytes]], session: dict) -> list[str]:
    if uploads:
        return [label for label, _, _ in uploads]
    docs = session.get("documents") or []
    return [d.get("label") or d.get("filename") or "document" for d in docs]


def save_verification_record(
    *,
    session_id: str,
    session: dict,
    scorecard: dict,
    uploads: list[tuple[str, str, bytes]],
    verify_result: dict,
    layered_credentials: dict | None = None,
    kya_proof: dict | None = None,
) -> dict | None:
    """
    Insert verification row. Returns serialized record or None if DB disabled/failed.
    """
    if not is_db_enabled():
        logger.info("DATABASE_URL not set — verification not persisted to Postgres")
        return None

    user = session.get("user_claims") or {}
    cost = verify_result.get("cost_analysis") or {}
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=KYB_EXPIRY_DAYS)
    enterprise_id = uuid.uuid4()
    doc_names = _document_names(uploads, session)

    record = KybVerification(
        enterprise_id=enterprise_id,
        session_id=session_id,
        legal_name=user.get("legal_name") or None,
        state=user.get("state") or None,
        ein=user.get("ein") or None,
        operating_address=user.get("operating_address") or None,
        business_purpose=user.get("business_purpose") or None,
        document_names=doc_names,
        document_count=len(doc_names),
        status=scorecard.get("kyb_status", "unknown"),
        flags_count=int(scorecard.get("flags_count", 0)),
        blocks_count=int(scorecard.get("blocks_count", 0)),
        reject_reason=_build_reject_reason(scorecard),
        verified_at=now,
        expires_at=expires,
        total_cost_usd=float(cost.get("total_cost_usd") or 0.0),
        cost_breakdown=cost,
        search_performed=bool(verify_result.get("search_performed")),
        scorecard=scorecard,
        public_facts=session.get("public_facts"),
        layered_credentials=layered_credentials,
        kya_proof=kya_proof,
    )

    try:
        with get_db() as db:
            db.add(record)
            db.flush()
            return record.to_dict()
    except Exception as exc:
        logger.exception("Failed to save KYB verification to Postgres: %s", exc)
        return None


def update_verification_credentials(
    *,
    enterprise_id: str,
    layered_credentials: dict | None = None,
    kya_proof: dict | None = None,
) -> bool:
    """Patch credential JSONB after enterprise_id is assigned to signed bundles."""
    if not is_db_enabled():
        return False
    try:
        eid = uuid.UUID(enterprise_id)
    except ValueError:
        return False
    try:
        with get_db() as db:
            row = db.get(KybVerification, eid)
            if not row:
                return False
            if layered_credentials is not None:
                row.layered_credentials = layered_credentials
            if kya_proof is not None:
                row.kya_proof = kya_proof
            db.flush()
            return True
    except Exception as exc:
        logger.exception("Failed to update verification credentials %s: %s", enterprise_id, exc)
        return False


def get_verification_by_id(enterprise_id: str) -> dict | None:
    if not is_db_enabled():
        return None
    try:
        eid = uuid.UUID(enterprise_id)
    except ValueError:
        return None
    try:
        with get_db() as db:
            row = db.get(KybVerification, eid)
            return row.to_dict() if row else None
    except Exception as exc:
        logger.exception("Failed to load verification %s: %s", enterprise_id, exc)
        return None


def list_verifications(*, limit: int = 50) -> list[dict]:
    if not is_db_enabled():
        return []
    from sqlalchemy import select

    try:
        with get_db() as db:
            rows = db.scalars(
                select(KybVerification).order_by(KybVerification.verified_at.desc()).limit(limit)
            ).all()
            return [r.to_dict() for r in rows]
    except Exception as exc:
        logger.exception("Failed to list verifications: %s", exc)
        return []
