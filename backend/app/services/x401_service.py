"""x401 stage: issue a signed KYB credential from verification RESULTS only (not raw docs)."""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone


def _signing_key() -> bytes:
    return os.getenv("KYB_SIGNING_KEY", "tbmc-demo-signing-key-change-in-prod").encode()


def _selective_disclosure(user_claims: dict, public_facts: dict | None, scorecard: dict) -> dict:
    """Public + attested fields for the VC — no raw document content."""
    public = public_facts or {}
    sc = scorecard
    return {
        "entity": user_claims.get("legal_name") or public.get("legal_name"),
        "ein": user_claims.get("ein") or None,
        "incorporation_state": user_claims.get("state") or public.get("incorporation_state"),
        "kyb_status": sc.get("kyb_status"),
        "scorecard_summary": {
            "passed": sc.get("kyb_status") == "passed",
            "flags_count": sc.get("flags_count", 0),
            "blocks_count": sc.get("blocks_count", 0),
        },
        "public_attestations": {
            "entity_status": public.get("status"),
            "entity_type": public.get("entity_type"),
            "formation_verified": public.get("formation_verified"),
            "public_sources": (public.get("source_urls") or [])[:5],
        },
        "verified_by": "TBMC-KYB-Agent",
        "protocol": "x401-simulated",
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


def issue_kyb_credential(
    session_id: str,
    user_claims: dict,
    verify_result: dict,
) -> dict:
    """
    x401 stage: build selective-disclosure VC + mock cryptographic signature.
    Only the credential payload is intended for persistent storage.
    """
    public_facts = verify_result.get("ai", {}).get("public_presence", {}).get("public_facts")
    scorecard = verify_result["deterministic"]["scorecard"]

    payload = _selective_disclosure(user_claims, public_facts, scorecard)
    payload["session_id"] = session_id
    payload["document_count"] = verify_result.get("ai", {}).get("documents", {}).get("count", 0)

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sig = hmac.new(_signing_key(), canonical.encode(), hashlib.sha256).hexdigest()
    payload["signature"] = f"x401-mock:{sig[:32]}"

    return payload
