"""Deterministic gap analysis — what KYB still needs vs user + doc extractions."""

from __future__ import annotations

from app.services import kyb_rules

as_text = kyb_rules.as_text

# Fields that may be filled via public registry / web search (never EIN, owners, etc.)
PUBLIC_GAP_FIELDS = {
    "legal_name_public_match",
    "entity_status",
    "formation_verified",
    "registered_agent_address",
    "incorporation_state",
    "entity_type",
}


def _has_sos_doc(documents: list[dict], extractions: list[dict]) -> bool:
    labels = " ".join(d.get("label", "").lower() for d in documents)
    if any(k in labels for k in ("sos", "secretary", "formation", "articles", "certificate", "incorporation")):
        return True
    for ext in extractions:
        dtype = as_text(ext.get("extracted", {}).get("document_type")).lower()
        if dtype in ("sos_filing", "articles", "license"):
            return True
    return False


def _status_in_docs(extractions: list[dict]) -> bool:
    for ext in extractions:
        extracted = ext.get("extracted") or {}
        for fact in extracted.get("key_facts") or []:
            lower = str(fact).lower()
            if any(t in lower for t in ("good standing", "active", "in existence")):
                return True
        text = str(extracted).lower()
        if "good standing" in text or '"status"' in text:
            return True
    return False


def build_claims_summary(user: dict, extractions: list[dict], documents: list[dict]) -> dict:
    """Internal merged view for planner — includes private fields for decision only."""
    legal_name = as_text(user.get("legal_name"))
    state = as_text(user.get("state")).upper()
    ein = as_text(user.get("ein"))
    address = as_text(user.get("operating_address"))
    purpose = as_text(user.get("business_purpose"))
    owners = user.get("beneficial_owners") or []
    control = user.get("control_persons") or []

    doc_names: list[str] = []
    for ext in extractions:
        name = as_text(ext.get("extracted", {}).get("entity_name") or ext.get("extracted", {}).get("legal_name"))
        if name:
            doc_names.append(name)

    return {
        "legal_name": legal_name or None,
        "state": state or None,
        "ein_provided": bool(ein),
        "address_provided": bool(address),
        "purpose_provided": bool(purpose),
        "owners_count": len(owners),
        "control_persons_count": len(control),
        "document_count": len(documents),
        "has_sos_or_formation_doc": _has_sos_doc(documents, extractions),
        "active_status_in_documents": _status_in_docs(extractions),
        "names_from_documents": doc_names[:3],
    }


def analyze_gaps(user: dict, extractions: list[dict], documents: list[dict]) -> list[dict]:
    """Return gaps with public_searchable flag."""
    summary = build_claims_summary(user, extractions, documents)
    gaps: list[dict] = []

    if not summary["legal_name"]:
        gaps.append(
            {
                "field": "legal_name",
                "public_searchable": False,
                "reason": "Legal name missing from form and documents",
            }
        )

    if summary["legal_name"] and not summary["state"]:
        gaps.append(
            {
                "field": "incorporation_state",
                "public_searchable": True,
                "reason": "State of incorporation not provided — needed to disambiguate registry search",
            }
        )

    if summary["legal_name"] and summary["state"] and not summary["has_sos_or_formation_doc"]:
        gaps.append(
            {
                "field": "formation_verified",
                "public_searchable": True,
                "reason": "No formation/SOS document detected — public registry may confirm existence",
            }
        )

    if summary["legal_name"] and not summary["active_status_in_documents"]:
        gaps.append(
            {
                "field": "entity_status",
                "public_searchable": True,
                "reason": "Active/good-standing status not found in submitted documents",
            }
        )

    if not summary["ein_provided"]:
        gaps.append(
            {
                "field": "ein",
                "public_searchable": False,
                "reason": "EIN not provided — not reliably searchable on public web",
            }
        )

    if summary["owners_count"] == 0:
        gaps.append(
            {
                "field": "beneficial_owners",
                "public_searchable": False,
                "reason": "Beneficial owners not listed — requires private documentation",
            }
        )

    return gaps


def public_gaps_remain(gaps: list[dict]) -> list[dict]:
    return [g for g in gaps if g.get("public_searchable")]


def can_skip_public_search(gaps: list[dict]) -> tuple[bool, str]:
    """Skip when no public-searchable gaps remain."""
    public = public_gaps_remain(gaps)
    if not public:
        non_public = [g for g in gaps if not g.get("public_searchable")]
        if non_public:
            return True, "Submitted documents and form data cover public verification needs; remaining gaps are private."
        return True, "All known verification fields satisfied from user input and documents."
    return False, ""


def public_search_query(user: dict) -> dict[str, str]:
    """Only fields safe to send to web search."""
    return {
        "legal_name": as_text(user.get("legal_name")),
        "state": as_text(user.get("state")).upper(),
    }


def enrich_claims_from_documents(user: dict, extractions: list[dict]) -> None:
    """Fill empty form fields from doc extractions (in-place)."""
    merged = kyb_rules.merge_user_claims_from_extractions(user, extractions)
    for key, val in merged.items():
        if key in ("beneficial_owners", "control_persons"):
            if val and not user.get(key):
                user[key] = val
        elif val and not user.get(key):
            user[key] = val
    if not user.get("state"):
        st = kyb_rules._state_from_extractions(extractions)
        if not st and user.get("operating_address"):
            st = kyb_rules.extract_state_from_address(str(user.get("operating_address")))
        if st:
            user["state"] = st
