"""SQLAlchemy models for KYB verification persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class KybVerification(Base):
    """
    One row per completed KYB verification run.
    enterprise_id = verification UUID returned to clients after submit.
    """

    __tablename__ = "kyb_verifications"

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    legal_name: Mapped[str | None] = mapped_column(String(512), index=True)
    state: Mapped[str | None] = mapped_column(String(8))
    ein: Mapped[str | None] = mapped_column(String(32))
    operating_address: Mapped[str | None] = mapped_column(Text)
    business_purpose: Mapped[str | None] = mapped_column(Text)

    document_names: Mapped[list] = mapped_column(JSONB, default=list)
    document_count: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    flags_count: Mapped[int] = mapped_column(Integer, default=0)
    blocks_count: Mapped[int] = mapped_column(Integer, default=0)
    reject_reason: Mapped[str | None] = mapped_column(Text)

    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)

    search_performed: Mapped[bool] = mapped_column(default=False)
    scorecard: Mapped[dict | None] = mapped_column(JSONB)
    public_facts: Mapped[dict | None] = mapped_column(JSONB)
    layered_credentials: Mapped[dict | None] = mapped_column(JSONB)
    kya_proof: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "enterprise_id": str(self.enterprise_id),
            "session_id": self.session_id,
            "legal_name": self.legal_name,
            "state": self.state,
            "ein": self.ein,
            "operating_address": self.operating_address,
            "business_purpose": self.business_purpose,
            "document_names": self.document_names,
            "document_count": self.document_count,
            "status": self.status,
            "flags_count": self.flags_count,
            "blocks_count": self.blocks_count,
            "reject_reason": self.reject_reason,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "total_cost_usd": self.total_cost_usd,
            "cost_breakdown": self.cost_breakdown,
            "search_performed": self.search_performed,
            "scorecard": self.scorecard,
            "public_facts": self.public_facts,
            "layered_credentials": self.layered_credentials,
            "kya_proof": self.kya_proof,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
