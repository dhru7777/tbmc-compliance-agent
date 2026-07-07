"""
Layered verification credentials (C1–C4) issued after KYB pipeline pass.

Document taxonomy:
  - verification_domain: kyc (individual identity) | kyb (business entity)
  - visibility: public | private

Credential tiers:
  C1 — KYC verification credential (identity / control-person documents)
  C2 — KYB verification credential (business entity documents)
  C3 — Combined KYC + KYB attestation credential
  C4 — Master credential binding C1, C2, and C3 signatures (proof of verification)
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from app.services.x401.credential import verify_credential
from app.services.x401.signing import sign_canonical_payload, signing_key_id

VerificationDomain = Literal["kyc", "kyb"]
DocumentVisibility = Literal["public", "private"]
CredentialTier = Literal["C1", "C2", "C3", "C4"]

KYB_EXPIRY_DAYS = int(__import__("os").getenv("KYB_EXPIRY_DAYS", "180"))
ISSUER_NAME = "Better Money Company Clearinghouse Compliance Agent"

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"
LAYERED_CREDENTIAL_FILENAME = "layered_verification_credentials.json"

# KYC checklist items (control person + government ID)
KYC_CHECKLIST_NUMS = (9, 10)
# KYB checklist items (entity through beneficial ownership)
KYB_CHECKLIST_NUMS = tuple(range(1, 9))


@dataclass(frozen=True)
class CanonicalDocument:
    id: str
    label: str
    verification_domain: VerificationDomain
    visibility: DocumentVisibility
    document_types: tuple[str, ...]
    label_keywords: tuple[str, ...]


CANONICAL_DOCUMENTS: tuple[CanonicalDocument, ...] = (
    CanonicalDocument(
        id="articles_of_incorporation",
        label="Articles of Incorporation / Certificate of Formation",
        verification_domain="kyb",
        visibility="public",
        document_types=("articles", "sos_filing"),
        label_keywords=("articles", "incorporation", "formation", "certificate of formation"),
    ),
    CanonicalDocument(
        id="certificate_of_good_standing",
        label="Certificate of Good Standing",
        verification_domain="kyb",
        visibility="public",
        document_types=("good_standing", "sos_filing"),
        label_keywords=("good standing", "standing certificate"),
    ),
    CanonicalDocument(
        id="ein_confirmation_letter",
        label="EIN Confirmation Letter (CP 575)",
        verification_domain="kyb",
        visibility="private",
        document_types=("ein_letter",),
        label_keywords=("ein", "cp 575", "tax id", "employer identification"),
    ),
    CanonicalDocument(
        id="proof_of_business_address",
        label="Proof of Business Address",
        verification_domain="kyb",
        visibility="private",
        document_types=("address_proof",),
        label_keywords=("proof of address", "business address", "utility bill", "lease"),
    ),
    CanonicalDocument(
        id="operating_agreement",
        label="Operating Agreement Excerpt",
        verification_domain="kyb",
        visibility="private",
        document_types=("operating_agreement", "articles"),
        label_keywords=("operating agreement", "management and control"),
    ),
    CanonicalDocument(
        id="beneficial_ownership_certification",
        label="Beneficial Ownership Certification",
        verification_domain="kyb",
        visibility="private",
        document_types=("beneficial_ownership",),
        label_keywords=("beneficial ownership", "boi", "ownership certification"),
    ),
    CanonicalDocument(
        id="business_purpose_statement",
        label="Business Purpose Statement",
        verification_domain="kyb",
        visibility="public",
        document_types=("business_purpose",),
        label_keywords=("business purpose", "purpose statement", "naics"),
    ),
    CanonicalDocument(
        id="business_license",
        label="Business License / Registered Agent Letter",
        verification_domain="kyb",
        visibility="public",
        document_types=("license", "other"),
        label_keywords=("business license", "registered agent", "annual report"),
    ),
    CanonicalDocument(
        id="government_issued_id",
        label="Government-issued ID",
        verification_domain="kyc",
        visibility="private",
        document_types=("government_id",),
        label_keywords=("government id", "driver license", "passport", "government-issued"),
    ),
    CanonicalDocument(
        id="identity_verification_result",
        label="Identity Verification Result",
        verification_domain="kyc",
        visibility="private",
        document_types=("government_id",),
        label_keywords=("identity verification", "id verification", "persona", "kyc"),
    ),
)


def list_document_catalog() -> list[dict[str, Any]]:
    """Return the canonical 10-document catalog with KYC/KYB and public/private tags."""
    return [
        {
            "id": doc.id,
            "label": doc.label,
            "verification_domain": doc.verification_domain,
            "visibility": doc.visibility,
            "document_types": list(doc.document_types),
        }
        for doc in CANONICAL_DOCUMENTS
    ]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _match_canonical_document(
    *,
    label: str = "",
    filename: str = "",
    document_type: str | None = None,
) -> CanonicalDocument | None:
    label_norm = _normalize(label)
    file_norm = _normalize(filename)
    doc_type = _normalize(document_type or "")

    best: CanonicalDocument | None = None
    best_score = 0

    for entry in CANONICAL_DOCUMENTS:
        score = 0
        if doc_type and doc_type in entry.document_types:
            score += 4
        for kw in entry.label_keywords:
            if kw in label_norm or kw in file_norm:
                score += 3
        for token in entry.id.split("_"):
            if len(token) > 3 and (token in label_norm or token in file_norm):
                score += 1
        if score > best_score:
            best_score = score
            best = entry

    return best if best_score > 0 else None


def categorize_uploaded_documents(session: dict) -> dict[str, Any]:
    """
    Classify every uploaded document into KYC/KYB and public/private buckets.
    Unmatched uploads are listed under uncategorized.
    """
    extractions = session.get("doc_extractions") or []
    docs_meta = session.get("documents") or []

    kyc_public: list[dict] = []
    kyc_private: list[dict] = []
    kyb_public: list[dict] = []
    kyb_private: list[dict] = []
    uncategorized: list[dict] = []

    seen: set[str] = set()

    def add_bucket(entry: CanonicalDocument, meta: dict) -> None:
        key = f"{entry.id}:{meta.get('filename') or meta.get('label')}"
        if key in seen:
            return
        seen.add(key)
        row = {
            "document_id": entry.id,
            "label": meta.get("label") or entry.label,
            "filename": meta.get("filename") or "",
            "verification_domain": entry.verification_domain,
            "visibility": entry.visibility,
            "document_type": meta.get("document_type"),
        }
        if entry.verification_domain == "kyc":
            (kyc_public if entry.visibility == "public" else kyc_private).append(row)
        else:
            (kyb_public if entry.visibility == "public" else kyb_private).append(row)

    for ext in extractions:
        label = ext.get("label") or ext.get("filename") or ""
        filename = ext.get("filename") or label
        extracted = ext.get("extracted") or {}
        doc_type = extracted.get("document_type")
        entry = _match_canonical_document(label=label, filename=filename, document_type=doc_type)
        meta = {"label": label, "filename": filename, "document_type": doc_type}
        if entry:
            add_bucket(entry, meta)
        else:
            uncategorized.append(meta)

    for doc in docs_meta:
        label = doc.get("label") or doc.get("filename") or ""
        filename = doc.get("filename") or label
        if any(u.get("filename") == filename or u.get("label") == label for u in uncategorized):
            continue
        if any(
            filename == row.get("filename") or label == row.get("label")
            for bucket in (kyc_public, kyc_private, kyb_public, kyb_private)
            for row in bucket
        ):
            continue
        entry = _match_canonical_document(label=label, filename=filename)
        meta = {"label": label, "filename": filename, "document_type": None}
        if entry:
            add_bucket(entry, meta)
        else:
            uncategorized.append(meta)

    return {
        "kyc": {"public": kyc_public, "private": kyc_private},
        "kyb": {"public": kyb_public, "private": kyb_private},
        "uncategorized": uncategorized,
        "summary": {
            "kyc_count": len(kyc_public) + len(kyc_private),
            "kyb_count": len(kyb_public) + len(kyb_private),
            "public_count": len(kyc_public) + len(kyb_public),
            "private_count": len(kyc_private) + len(kyb_private),
            "total_categorized": len(kyc_public) + len(kyc_private) + len(kyb_public) + len(kyb_private),
        },
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _checklist_passed(scorecard: dict, nums: tuple[int, ...]) -> bool:
    items = {i.get("num"): i.get("result") for i in scorecard.get("items") or []}
    return all(items.get(n) == "PASS" for n in nums)


def _checklist_snapshot(scorecard: dict, nums: tuple[int, ...]) -> list[dict]:
    wanted = set(nums)
    out = []
    for item in scorecard.get("items") or []:
        if item.get("num") in wanted:
            out.append(
                {
                    "num": item.get("num"),
                    "item": item.get("item"),
                    "result": item.get("result"),
                    "availability": item.get("availability"),
                }
            )
    return out


def _sign_payload(payload: dict[str, Any]) -> dict[str, Any]:
    signable = {k: v for k, v in payload.items() if k not in ("signature", "signing_key_id")}
    canonical = json.dumps(signable, sort_keys=True, separators=(",", ":"))
    payload["signature"] = sign_canonical_payload(canonical)
    payload["signing_key_id"] = signing_key_id()
    return payload


def _documents_for_domain(categorized: dict, domain: VerificationDomain) -> list[dict]:
    bucket = categorized.get(domain) or {}
    return list(bucket.get("public") or []) + list(bucket.get("private") or [])


def _subject_from_session(session: dict) -> dict[str, Any]:
    user = session.get("user_claims") or {}
    control = user.get("control_persons") or []
    owners = user.get("beneficial_owners") or []
    return {
        "legal_name": user.get("legal_name") or "",
        "ein": user.get("ein") or "",
        "state": user.get("state") or "",
        "beneficial_owners": owners,
        "control_persons": control,
        "primary_subject": (control[0].get("name") if control else "") or (owners[0].get("name") if owners else ""),
    }


def issue_layered_credentials(
    *,
    session_id: str,
    session: dict,
    scorecard: dict,
    enterprise_id: str | None = None,
) -> dict | None:
    """
    Issue C1 (KYC), C2 (KYB), C3 (combined), and C4 (master) signed credentials.
    Returns None when verification did not pass.
    """
    if scorecard.get("kyb_status") != "passed":
        return None

    categorized = categorize_uploaded_documents(session)
    kyc_docs = _documents_for_domain(categorized, "kyc")
    kyb_docs = _documents_for_domain(categorized, "kyb")
    subject = _subject_from_session(session)
    now = _now_utc()
    expiry = (now + timedelta(days=KYB_EXPIRY_DAYS)).isoformat()
    root_id = str(enterprise_id or uuid.uuid4())

    c1_id = str(uuid.uuid4())
    c2_id = str(uuid.uuid4())
    c3_id = str(uuid.uuid4())
    c4_id = root_id

    c1 = _sign_payload(
        {
            "credential_tier": "C1",
            "credential_type": "kyc_verification_credential",
            "credential_id": c1_id,
            "session_id": session_id,
            "enterprise_id": root_id,
            "issued_by": ISSUER_NAME,
            "issuance_date": now.isoformat(),
            "expiry_date": expiry,
            "verification_domain": "kyc",
            "proof_purpose": "proof_of_individual_identity_and_control",
            "issued_to": {
                "primary_subject": subject["primary_subject"],
                "control_persons": subject["control_persons"],
            },
            "documents_verified": kyc_docs,
            "document_visibility": {
                "public": [d for d in kyc_docs if d.get("visibility") == "public"],
                "private": [d for d in kyc_docs if d.get("visibility") == "private"],
            },
            "checklist_items": _checklist_snapshot(scorecard, KYC_CHECKLIST_NUMS),
            "checklist_passed": _checklist_passed(scorecard, KYC_CHECKLIST_NUMS),
            "confidence_score": scorecard.get("confidence_score"),
        }
    )

    c2 = _sign_payload(
        {
            "credential_tier": "C2",
            "credential_type": "kyb_verification_credential",
            "credential_id": c2_id,
            "session_id": session_id,
            "enterprise_id": root_id,
            "issued_by": ISSUER_NAME,
            "issuance_date": now.isoformat(),
            "expiry_date": expiry,
            "verification_domain": "kyb",
            "proof_purpose": "proof_of_business_entity_verification",
            "issued_to": {
                "legal_name": subject["legal_name"],
                "ein": subject["ein"],
                "state": subject["state"],
                "beneficial_owners": subject["beneficial_owners"],
            },
            "documents_verified": kyb_docs,
            "document_visibility": {
                "public": [d for d in kyb_docs if d.get("visibility") == "public"],
                "private": [d for d in kyb_docs if d.get("visibility") == "private"],
            },
            "checklist_items": _checklist_snapshot(scorecard, KYB_CHECKLIST_NUMS),
            "checklist_passed": _checklist_passed(scorecard, KYB_CHECKLIST_NUMS),
            "confidence_score": scorecard.get("confidence_score"),
        }
    )

    c3 = _sign_payload(
        {
            "credential_tier": "C3",
            "credential_type": "kyc_kyb_combined_credential",
            "credential_id": c3_id,
            "session_id": session_id,
            "enterprise_id": root_id,
            "issued_by": ISSUER_NAME,
            "issuance_date": now.isoformat(),
            "expiry_date": expiry,
            "proof_purpose": "proof_of_combined_kyc_and_kyb_verification",
            "kyc_credential_id": c1_id,
            "kyb_credential_id": c2_id,
            "kyc_signature": c1["signature"],
            "kyb_signature": c2["signature"],
            "issued_to": {
                "legal_name": subject["legal_name"],
                "ein": subject["ein"],
                "primary_subject": subject["primary_subject"],
                "beneficial_owners": subject["beneficial_owners"],
                "control_persons": subject["control_persons"],
            },
            "documents_verified": {
                "kyc": kyc_docs,
                "kyb": kyb_docs,
            },
            "document_categorization": categorized["summary"],
            "checklist_passed": {
                "kyc": _checklist_passed(scorecard, KYC_CHECKLIST_NUMS),
                "kyb": _checklist_passed(scorecard, KYB_CHECKLIST_NUMS),
            },
            "confidence_score": scorecard.get("confidence_score"),
        }
    )

    c4 = _sign_payload(
        {
            "credential_tier": "C4",
            "credential_type": "master_verification_credential",
            "credential_id": c4_id,
            "session_id": session_id,
            "enterprise_id": root_id,
            "issued_by": ISSUER_NAME,
            "issuance_date": now.isoformat(),
            "expiry_date": expiry,
            "proof_purpose": "proof_of_ownership_and_verification",
            "constituent_credentials": {
                "C1": {
                    "credential_id": c1_id,
                    "credential_type": c1["credential_type"],
                    "signature": c1["signature"],
                },
                "C2": {
                    "credential_id": c2_id,
                    "credential_type": c2["credential_type"],
                    "signature": c2["signature"],
                },
                "C3": {
                    "credential_id": c3_id,
                    "credential_type": c3["credential_type"],
                    "signature": c3["signature"],
                },
            },
            "issued_to": {
                "legal_name": subject["legal_name"],
                "ein": subject["ein"],
                "primary_subject": subject["primary_subject"],
            },
            "document_categorization": categorized,
            "verification_status": scorecard.get("kyb_status"),
            "confidence_score": scorecard.get("confidence_score"),
        }
    )

    bundle = {
        "session_id": session_id,
        "enterprise_id": root_id,
        "issued_at": now.isoformat(),
        "expiry_date": expiry,
        "document_catalog_count": len(CANONICAL_DOCUMENTS),
        "document_categorization": categorized,
        "credentials": {
            "C1": c1,
            "C2": c2,
            "C3": c3,
            "C4": c4,
        },
    }
    return bundle


def verify_layered_credentials(bundle: dict) -> dict[str, bool]:
    """Verify all four credential signatures in a layered bundle."""
    creds = bundle.get("credentials") or {}
    results: dict[str, bool] = {}
    for tier in ("C1", "C2", "C3", "C4"):
        cred = creds.get(tier)
        results[tier] = bool(cred and verify_credential(cred))
    results["all_valid"] = all(results.get(t) for t in ("C1", "C2", "C3", "C4"))
    return results


def save_layered_credentials(session_id: str, bundle: dict) -> str:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LAYERED_CREDENTIAL_FILENAME
    path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return str(path)


def load_layered_credentials(session_id: str) -> dict | None:
    path = RECORDS_DIR / session_id / LAYERED_CREDENTIAL_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
