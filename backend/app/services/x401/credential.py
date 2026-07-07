"""Assemble and sign x401 compliance credentials from Stage 1 + Stage 2 outputs."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.services.x401.signing import sign_canonical_payload, signing_key_id, verify_signature

KYB_EXPIRY_DAYS = int(os.getenv("KYB_EXPIRY_DAYS", "180"))
CREDIT_LIMIT_FRACTION = float(os.getenv("KYB_CREDIT_LIMIT_FRACTION", "0.20"))
# Used when the applicant leaves monthly volume blank (avoid $1 → $0.14 credit limits).
DEFAULT_MONTHLY_VOLUME_LOW = float(os.getenv("KYB_DEFAULT_MONTHLY_VOLUME_LOW", "100000"))
DEFAULT_MONTHLY_VOLUME_HIGH = float(os.getenv("KYB_DEFAULT_MONTHLY_VOLUME_HIGH", "250000"))
ISSUER_NAME = "Better Money Company Clearinghouse Compliance Agent"

# Scorecard item numbers → criteria_checked field(s)
_CRITERIA_MAP: dict[str, list[int]] = {
    "entity_verified": [1, 2],
    "good_standing_active": [3],
    "sanctions_clear": [4],
    "address_match": [5],
    "business_purpose_verified": [6],
    "ein_confirmed": [7],
    "beneficial_ownership_disclosed": [8],
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _item_result(scorecard: dict, num: int) -> str:
    for item in scorecard.get("items") or []:
        if item.get("num") == num:
            return item.get("result", "SKIP")
    return "SKIP"


def _criteria_checked(scorecard: dict) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for field, nums in _CRITERIA_MAP.items():
        results = [_item_result(scorecard, n) for n in nums]
        out[field] = all(r == "PASS" for r in results)
    return out


def _entity_file_number(session: dict) -> str:
    public = session.get("public_facts") or {}
    for key in ("file_number", "entity_file_number", "sos_file_number"):
        if public.get(key):
            return str(public[key])
    for ext in session.get("doc_extractions") or []:
        extracted = ext.get("extracted") or {}
        for key in ("file_number", "entity_file_number", "sos_file_number"):
            if extracted.get(key):
                return str(extracted[key])
        for fact in extracted.get("key_facts") or []:
            text = str(fact)
            match = re.search(r"(?:file|document)\s*(?:#|number)?\s*:?\s*([\d-]+)", text, re.I)
            if match:
                return match.group(1)
    return ""


def compute_confidence_score(session: dict, scorecard: dict) -> float:
    """0.0–1.0 confidence from public search + checklist skips."""
    public = session.get("public_facts") or {}
    base = public.get("confidence")
    if base is None:
        base = 1.0 if scorecard.get("kyb_status") == "passed" else 0.5
    base = float(base)
    skips = sum(1 for i in scorecard.get("items") or [] if i.get("result") == "SKIP")
    score = min(1.0, max(0.0, base - skips * 0.03))
    return round(score, 2)


def compute_credit_limit_usd(volume_low: float, confidence_score: float) -> float:
    base = CREDIT_LIMIT_FRACTION * volume_low
    return round(base * confidence_score, 2)


def canonical_payload_bytes(payload: dict) -> bytes:
    """Canonical JSON for signing — excludes signature and signing_key_id."""
    signable = {k: v for k, v in payload.items() if k not in ("signature", "signing_key_id")}
    return json.dumps(signable, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_credential(credential: dict, public_key=None) -> bool:
    """Standalone verification — simulates what a third party would run."""
    if not credential.get("signature"):
        return False
    signable = {k: v for k, v in credential.items() if k not in ("signature", "signing_key_id")}
    canonical = json.dumps(signable, sort_keys=True, separators=(",", ":"))
    return verify_signature(canonical, credential["signature"], public_key)


def issue_compliance_credential(
    *,
    session_id: str,
    session: dict,
    scorecard: dict,
    enterprise_id: str | None = None,
) -> dict | None:
    """
    Issue signed x401 compliance credential when Stage 2 passed.
    Returns None if status is not passed.
    """
    if scorecard.get("kyb_status") != "passed":
        return None

    user = session.get("user_claims") or {}
    public = session.get("public_facts") or {}
    raw_low = user.get("monthly_volume_low_usd")
    raw_high = user.get("monthly_volume_high_usd")
    volume_declared = bool(raw_low or raw_high)
    try:
        volume_low = float(raw_low) if raw_low not in (None, "") else 0.0
    except (TypeError, ValueError):
        volume_low = 0.0
    try:
        volume_high = float(raw_high) if raw_high not in (None, "") else 0.0
    except (TypeError, ValueError):
        volume_high = 0.0
    if volume_low <= 0:
        volume_low = DEFAULT_MONTHLY_VOLUME_LOW
    if volume_high <= 0:
        volume_high = DEFAULT_MONTHLY_VOLUME_HIGH if not volume_declared else volume_low
    if volume_high < volume_low:
        volume_high = volume_low

    confidence = scorecard.get("confidence_score")
    if confidence is None:
        confidence = compute_confidence_score(session, scorecard)

    now = _now_utc()
    expiry = now + timedelta(days=KYB_EXPIRY_DAYS)
    credit_limit = compute_credit_limit_usd(volume_low, confidence)

    payload: dict = {
        "credential_type": "x401_compliance_credential",
        "credential_id": str(enterprise_id or uuid.uuid4()),
        "session_id": session_id,
        "issued_to": {
            "legal_name": user.get("legal_name") or public.get("legal_name") or "",
            "entity_file_number": _entity_file_number(session),
            "ein": user.get("ein") or "",
        },
        "issued_by": ISSUER_NAME,
        "issuance_date": now.isoformat(),
        "expiry_date": expiry.isoformat(),
        "compliance_status": "passed",
        "confidence_score": confidence,
        "declared_monthly_volume_usd": {
            "low": volume_low,
            "high": volume_high,
            "declared_by_applicant": volume_declared,
        },
        "criteria_checked": _criteria_checked(scorecard),
        "allowed_scope": {
            "credit_limit_usd": credit_limit,
            "approved_asset_classes": ["USDC"],
            "approved_counterparty_type": "clearinghouse_network_member",
        },
        "protocol_note": "x401-simulated — credential issuance only; HTTP PROOF-* presentation not implemented",
    }

    canonical = json.dumps(
        {k: v for k, v in payload.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["signature"] = sign_canonical_payload(canonical)
    payload["signing_key_id"] = signing_key_id()
    return payload
