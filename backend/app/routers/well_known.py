"""Well-known endpoints for clearinghouse public keys."""

from fastapi import APIRouter

from app.services.x401_service import get_public_key_info

router = APIRouter()


@router.get("/tbmc-signing-key.json")
def tbmc_signing_key():
    """Public Ed25519 key for third-party credential verification."""
    return get_public_key_info()
