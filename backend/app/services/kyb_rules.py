"""Deterministic KYB cross-check rules. Middesk can be added later as corroboration."""

import os
import re
from difflib import SequenceMatcher

OFAC_DENY_NAMES = [
    "specially designated national",
    "sdn list example corp",
]

KYB_CONFIDENCE_FLOOR = float(os.getenv("KYB_CONFIDENCE_FLOOR", "0.7"))

EIN_PATTERN = re.compile(r"^\d{2}-\d{7}$")


def as_text(value) -> str:
    """Coerce LLM-extracted values (sometimes lists) into a single string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [as_text(v) for v in value]
        return ", ".join(p for p in parts if p)
    return str(value).strip()


def fuzzy_match(a: str, b: str) -> float:
    a, b = as_text(a), as_text(b)
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def normalize_address(addr) -> str:
    text = as_text(addr)
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower())


def extract_state_from_address(addr: str) -> str | None:
    upper = addr.upper()
    # Prefer "City, ST 12345" — avoids matching "St" in street names
    match = re.search(r",\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", upper)
    if match:
        return match.group(1)
    match = re.search(r",\s*([A-Z]{2})\b\s*$", upper.strip())
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Z]{2})\b", upper)
    return match.group(1) if match else None


def check_legal_name(user_name: str, public_name: str) -> dict:
    user_name = as_text(user_name)
    public_name = as_text(public_name)
    if not user_name and not public_name:
        return _with_recommendation({"result": "FLAG", "detail": "Legal name not provided"}, 1)
    if not user_name:
        return {"result": "SKIP", "detail": "Inferred from documents — no form entry to compare"}
    if not public_name:
        return {"result": "SKIP", "detail": "No public record to compare"}
    score = fuzzy_match(user_name, public_name)
    if score >= 0.85:
        return {"result": "PASS", "detail": f"Name match ({score:.0%})"}
    if score >= 0.6:
        return {"result": "FLAG", "detail": f"Partial name match ({score:.0%})"}
    return {"result": "BLOCK", "detail": f"Name mismatch ({score:.0%})"}


def _standing_is_negated(text: str) -> bool:
    lower = text.lower()
    negation = (
        "not attached",
        "not included",
        "not provided",
        "missing",
        "not in good standing",
        "pending",
    )
    return any(n in lower for n in negation)


def _standing_is_active(text: str) -> bool:
    lower = text.lower()
    if _standing_is_negated(lower):
        return False
    active_tokens = ("good standing", "active", "in existence", "current")
    return any(t in lower for t in active_tokens)


def check_good_standing(status: str | None, extractions: list[dict] | None = None) -> dict:
    extractions = extractions or []
    if not status:
        for ext in extractions:
            extracted = ext.get("extracted") or {}
            for fact in extracted.get("key_facts") or []:
                if _standing_is_active(str(fact)):
                    status = "active"
                    break
            if status:
                break
            blob = str(extracted).lower()
            if _standing_is_active(blob) and ("status" in blob or "standing" in blob):
                status = "active"
                break
    if not status:
        return {"result": "FLAG", "detail": "Status not found in public search"}
    active_tokens = ("active", "good standing", "in existence", "current")
    bad_tokens = ("dissolved", "inactive", "suspended", "revoked", "cancelled")
    lower = status.lower()
    if any(t in lower for t in bad_tokens):
        return {"result": "BLOCK", "detail": f"Entity status: {status}"}
    if any(t in lower for t in active_tokens):
        return {"result": "PASS", "detail": f"Entity status: {status}"}
    return {"result": "FLAG", "detail": f"Unclear status: {status}"}


def _address_from_extractions(extractions: list[dict] | None) -> str:
    for ext in extractions or []:
        addr = as_text((ext.get("extracted") or {}).get("address"))
        if addr:
            return addr
    return ""


def _purpose_from_extractions(extractions: list[dict] | None) -> str:
    for ext in extractions or []:
        extracted = ext.get("extracted") or {}
        for fact in extracted.get("key_facts") or []:
            text = str(fact)
            if "business purpose:" in text.lower():
                return text.split(":", 1)[1].strip()
    return ""


def check_address(user_address, public_address, extractions: list[dict] | None = None) -> dict:
    user_address = as_text(user_address)
    public_address = as_text(public_address) or None
    source = "public filing"
    if not user_address:
        return {"result": "SKIP", "detail": "Add your operating address to compare"}
    if not public_address:
        doc_addr = _address_from_extractions(extractions)
        if doc_addr:
            public_address = doc_addr
            source = "uploaded formation document"
        else:
            return {
                "result": "SKIP",
                "detail": "Address on form — registry cross-check unavailable (no public record loaded)",
            }

    user_norm = normalize_address(user_address)
    public_norm = normalize_address(public_address)
    if user_norm == public_norm or user_norm in public_norm or public_norm in user_norm:
        return {"result": "PASS", "detail": f"Matches address on {source}"}

    user_state = extract_state_from_address(user_address)
    public_state = extract_state_from_address(public_address)
    # HQ / operating address often differs from registered agent — same state is normal
    if user_state and public_state and user_state == public_state:
        return {
            "result": "PASS",
            "detail": f"Different street from {source}, both in {user_state} — typical for corporations",
        }
    if user_state and public_state and user_state != public_state:
        return {
            "result": "FLAG",
            "detail": f"Your address is in {user_state}; address on {source} is in {public_state}",
        }
    return {"result": "FLAG", "detail": f"Could not confirm your address aligns with the {source}"}


def check_purpose(user_purpose: str, public_purpose: str | None, extractions: list[dict] | None = None) -> dict:
    if not user_purpose:
        return {"result": "SKIP", "detail": "Add your business purpose to compare"}
    if not public_purpose:
        doc_purpose = _purpose_from_extractions(extractions)
        if doc_purpose:
            public_purpose = doc_purpose
        else:
            return {
                "result": "SKIP",
                "detail": "Purpose on form — registry cross-check unavailable (no public record loaded)",
            }

    user_words = set(re.findall(r"[a-z]{4,}", user_purpose.lower()))
    public_words = set(re.findall(r"[a-z]{4,}", public_purpose.lower()))
    overlap = user_words & public_words
    risky_user = {"crypto", "cryptocurrency", "exchange", "stablecoin", "gambling"}

    if user_words & risky_user and not (user_words & public_words):
        return {
            "result": "FLAG",
            "detail": "Stated purpose may not match public filing category",
        }
    if len(overlap) >= 1:
        return {"result": "PASS", "detail": "Purpose overlaps with public filing description"}
    return {"result": "FLAG", "detail": "Stated purpose differs from public NAICS/filing description"}


def check_ofac(legal_name: str) -> dict:
    lower = legal_name.lower()
    for denied in OFAC_DENY_NAMES:
        if denied in lower:
            return {"result": "BLOCK", "detail": "Potential OFAC SDN match"}
    return {"result": "PASS", "detail": "No OFAC match on deny-list check"}


FLAG_DOCUMENT_HINTS = {
    1: "Confirm legal name matches your Secretary of State filing.",
    2: "Upload Articles of Incorporation / Organization.",
    3: "Upload a Certificate of Good Standing from the SOS.",
    4: "Resolve OFAC hit or confirm entity name with compliance.",
    5: "Enter operating address or upload a document showing business address.",
    6: "Enter business purpose or upload a Business Purpose Statement.",
    7: "Enter EIN above or upload an IRS EIN confirmation letter (CP 575).",
    8: "Add owners in the form or upload a Beneficial Ownership Certification.",
    9: "Add control persons in the form or upload an Operating Agreement excerpt.",
    10: "Upload Government-issued ID (passport or driver's license) — include 'ID' in the filename.",
}


def _with_recommendation(result: dict, num: int) -> dict:
    if result.get("result") == "FLAG" and "recommendation" not in result:
        result = {**result, "recommendation": FLAG_DOCUMENT_HINTS.get(num, "Upload supporting documentation.")}
    return result


def _owners_from_extractions(extractions: list[dict]) -> list[dict]:
    owners: list[dict] = []
    seen: set[str] = set()
    for ext in extractions:
        label = (ext.get("label") or "").lower()
        extracted = ext.get("extracted") or {}
        if not any(k in label for k in ("beneficial", "ownership", "cdd", "boi")):
            continue
        for fact in extracted.get("key_facts") or []:
            text = str(fact)
            match = re.search(r"([^:]{2,}?):\s*(\d+(?:\.\d+)?)\s*%\s*ownership", text, re.I)
            if match:
                name = match.group(1).strip()
                key = name.lower()
                if key not in seen:
                    seen.add(key)
                    owners.append({"name": name, "ownership_pct": float(match.group(2))})
        names = extracted.get("person_name")
        if names:
            name_list = names if isinstance(names, (list, tuple)) else [names]
            for name in name_list:
                n = as_text(name)
                if n and n.lower() not in seen:
                    seen.add(n.lower())
                    owners.append({"name": n, "ownership_pct": 25})
    return owners


def _control_from_extractions(extractions: list[dict]) -> list[dict]:
    persons: list[dict] = []
    seen: set[str] = set()
    for ext in extractions:
        label = (ext.get("label") or "").lower()
        extracted = ext.get("extracted") or {}
        relevant = any(
            k in label
            for k in ("operating", "agreement", "control", "management", "officer", "beneficial", "ownership")
        )
        if not relevant:
            continue
        for fact in extracted.get("key_facts") or []:
            text = str(fact)
            lower = text.lower()
            if not any(k in lower for k in ("ceo", "managing member", "president", "control person")):
                continue
            match = re.search(r"^([^:]{2,}?):\s*(\d+(?:\.\d+)?)\s*%\s*ownership", text, re.I)
            if match:
                name = match.group(1).strip()
                if name.lower() not in seen:
                    seen.add(name.lower())
                    title = "CEO" if "ceo" in lower else "Managing Member"
                    persons.append({"name": name, "title": title})
                continue
            role_match = re.search(
                r"(?:managing member\s*/?\s*ceo|ceo|managing member|president)[:\s]+([A-Z][A-Za-z.\s]+?)(?:\s+with|\s*$|,)",
                text,
                re.I,
            )
            if role_match:
                name = role_match.group(1).strip()
                if name.lower() not in seen:
                    seen.add(name.lower())
                    persons.append({"name": name, "title": "Managing Member"})
        name_val = extracted.get("person_name")
        name_candidates = name_val if isinstance(name_val, (list, tuple)) else [name_val]
        for raw_name in name_candidates:
            name = as_text(raw_name)
            if not name:
                continue
            name_lower = name.lower()
            for fact in extracted.get("key_facts") or []:
                text = str(fact)
                lower = text.lower()
                if name_lower not in lower:
                    continue
                if any(k in lower for k in ("ceo", "managing member", "president", "control person", "authorized")):
                    if name_lower not in seen:
                        seen.add(name_lower)
                        persons.append({"name": name, "title": "Managing Member"})
                    break
    return persons


def _ein_from_extractions(extractions: list[dict]) -> str:
    for ext in extractions:
        extracted = ext.get("extracted") or {}
        label = (ext.get("label") or "").lower()
        if extracted.get("ein") or "ein" in label:
            ein = as_text(extracted.get("ein"))
            if ein:
                return ein
    return ""


def _has_id_document(documents: list[dict], extractions: list[dict]) -> bool:
    id_tokens = ("government id", "gov id", "passport", "driver", "drivers", " license", " state id", "04 id")
    labels = " ".join(d.get("label", "").lower() for d in documents)
    for ext in extractions:
        labels += " " + (ext.get("label") or "").lower()
        extracted = ext.get("extracted") or {}
        if extracted.get("document_type") == "government_id":
            return True
    return any(t in labels for t in id_tokens)


def check_ein(ein: str, extractions: list[dict] | None = None) -> dict:
    extractions = extractions or []
    form_ein = ein
    ein = form_ein or _ein_from_extractions(extractions)
    if not ein:
        return _with_recommendation({"result": "FLAG", "detail": "EIN not provided"}, 7)
    if EIN_PATTERN.match(ein.strip()):
        source = "form" if form_ein else "document"
        return {"result": "PASS", "detail": f"EIN format valid ({source} attestation)"}
    return _with_recommendation(
        {"result": "FLAG", "detail": "EIN format invalid (expected XX-XXXXXXX)"},
        7,
    )


def check_ownership(owners: list[dict], extractions: list[dict] | None = None) -> dict:
    extractions = extractions or []
    merged = list(owners) if owners else _owners_from_extractions(extractions)
    if not merged:
        return _with_recommendation({"result": "FLAG", "detail": "No beneficial owners declared"}, 8)
    major = [o for o in merged if float(o.get("ownership_pct", 0)) >= 25]
    if major:
        source = "form" if owners else "uploaded certification"
        return {"result": "PASS", "detail": f"{len(major)} owner(s) at 25%+ via {source}"}
    return _with_recommendation({"result": "FLAG", "detail": "No 25%+ beneficial owner declared"}, 8)


def check_control_persons(persons: list[dict], extractions: list[dict] | None = None) -> dict:
    extractions = extractions or []
    merged = list(persons) if persons else _control_from_extractions(extractions)
    if not merged:
        return _with_recommendation({"result": "FLAG", "detail": "No control person declared"}, 9)
    source = "form" if persons else "uploaded agreement"
    return {"result": "PASS", "detail": f"{len(merged)} control person(s) via {source}"}


def check_documents(documents: list[dict], extractions: list[dict] | None = None) -> dict:
    extractions = extractions or []
    if not documents:
        return _with_recommendation({"result": "FLAG", "detail": "No supporting documents uploaded"}, 10)
    if _has_id_document(documents, extractions):
        return {"result": "PASS", "detail": "Government ID document uploaded"}
    return _with_recommendation({"result": "FLAG", "detail": "No government ID detected"}, 10)


KYB_CHECKLIST = [
    {
        "num": 1,
        "item": "Legal business name",
        "availability": "Public",
        "useful_for": "Confirms identity",
        "source": "Secretary of State",
    },
    {
        "num": 2,
        "item": "Formation documents",
        "availability": "Public",
        "useful_for": "Proves legal existence",
        "source": "Secretary of State",
    },
    {
        "num": 3,
        "item": "Proof of good standing",
        "availability": "Public",
        "useful_for": "Confirms active status",
        "source": "Secretary of State",
    },
    {
        "num": 4,
        "item": "OFAC sanctions screening",
        "availability": "Public",
        "useful_for": "Blocks sanctioned entities",
        "source": "OFAC SDN List",
    },
    {
        "num": 5,
        "item": "Business address",
        "availability": "Partial",
        "useful_for": "Confirms real location",
        "source": "Secretary of State",
    },
    {
        "num": 6,
        "item": "Business purpose",
        "availability": "Partial",
        "useful_for": "Assesses risk profile",
        "source": "State filing (NAICS)",
    },
    {
        "num": 7,
        "item": "EIN (Tax ID)",
        "availability": "Private",
        "useful_for": "Confirms tax identity",
        "source": "Client provided",
    },
    {
        "num": 8,
        "item": "Beneficial ownership",
        "availability": "Private",
        "useful_for": "Identifies real owners",
        "source": "Self-attested",
    },
    {
        "num": 9,
        "item": "Control person(s)",
        "availability": "Private",
        "useful_for": "Identifies decision-makers",
        "source": "Self-attested",
    },
    {
        "num": 10,
        "item": "Government-issued ID",
        "availability": "Private",
        "useful_for": "Verifies real humans",
        "source": "Client submitted",
    },
]


def get_checklist_template() -> list[dict]:
    return [{**row, "result": "PENDING", "detail": ""} for row in KYB_CHECKLIST]


def check_formation_documents(public: dict, documents: list[dict]) -> dict:
    if public.get("formation_verified"):
        return {"result": "PASS", "detail": public.get("formation_detail", "Formation verified via public record")}
    labels = " ".join(d.get("label", "").lower() for d in documents)
    if any(k in labels for k in ("articles", "incorporation", "sos", "formation", "certificate", "org")):
        return {"result": "PASS", "detail": "Formation document uploaded"}
    if documents:
        return _with_recommendation(
            {"result": "FLAG", "detail": "Documents uploaded — no formation filing detected by label"},
            2,
        )
    return _with_recommendation(
        {"result": "FLAG", "detail": "Awaiting formation document or public confirmation"},
        2,
    )


def middesk_corroborate(_legal_name: str, _state: str) -> dict:
    """Placeholder for future Middesk API corroboration."""
    return {
        "available": False,
        "message": "Middesk corroboration not configured — using LLM public search + deterministic rules only",
    }


def _effective_public_facts(session: dict) -> dict:
    """Merge trial registry fixture when UI sent trial_company_id but search did not run."""
    from app.services.demo_companies import trial_public_facts

    public = dict(session.get("public_facts") or {})
    trial_id = session.get("trial_company_id")
    if trial_id and not public.get("trial_company_id"):
        public.update(trial_public_facts(trial_id))
    return public


def build_scorecard(session: dict) -> dict:
    public = _effective_public_facts(session)
    user = session.get("user_claims") or {}
    docs = session.get("documents") or []
    extractions = session.get("doc_extractions") or []

    raw_checks = [
        (1, check_legal_name(user.get("legal_name", ""), public.get("legal_name", ""))),
        (2, check_formation_documents(public, docs)),
        (3, check_good_standing(public.get("status"), extractions)),
        (4, check_ofac(user.get("legal_name", ""))),
        (5, check_address(user.get("operating_address", ""), public.get("registered_agent_address"), extractions)),
        (6, check_purpose(user.get("business_purpose", ""), public.get("naics_or_purpose"), extractions)),
        (7, check_ein(user.get("ein", ""), extractions)),
        (8, check_ownership(user.get("beneficial_owners", []), extractions)),
        (9, check_control_persons(user.get("control_persons", []), extractions)),
        (10, check_documents(docs, extractions)),
    ]
    checks = [
        _with_recommendation(c, n) if c.get("result") == "FLAG" and "recommendation" not in c else c
        for n, c in raw_checks
    ]

    items = [{**meta, **check} for meta, check in zip(KYB_CHECKLIST, checks)]

    blocks = [i for i in items if i["result"] == "BLOCK"]
    flags = [i for i in items if i["result"] == "FLAG"]  # SKIP items are informational only

    if blocks:
        kyb_status = "blocked"
    elif flags:
        kyb_status = "flagged"
    else:
        kyb_status = "passed"

    confidence_score = _compute_confidence_score(session, items, kyb_status)
    if kyb_status == "passed" and confidence_score < KYB_CONFIDENCE_FLOOR:
        kyb_status = "flagged"
        flags = list(flags) + [
            {
                "num": 0,
                "item": "Confidence floor",
                "result": "FLAG",
                "detail": f"Confidence {confidence_score:.2f} below minimum {KYB_CONFIDENCE_FLOOR:.2f}",
                "recommendation": "Improve public record match or supply missing attestations, then resubmit.",
            }
        ]

    vc = {
        "entity": user.get("legal_name") or public.get("legal_name"),
        "ein": user.get("ein") or None,
        "kyb_status": kyb_status,
        "verified_by": "TBMC-KYB-Agent",
        "public_sources": public.get("source_urls", []),
        "issued_at": session.get("updated_at"),
        "signature": "<mock-signature>",
    }

    return {
        "kyb_status": kyb_status,
        "items": items,
        "flags_count": len(flags),
        "blocks_count": len(blocks),
        "confidence_score": confidence_score,
        "vc": vc,
    }


def _compute_confidence_score(session: dict, items: list[dict], kyb_status: str) -> float:
    public = session.get("public_facts") or {}
    base = public.get("confidence")
    if base is None:
        base = 1.0 if kyb_status == "passed" else 0.5
    base = float(base)
    skips = sum(1 for i in items if i.get("result") == "SKIP")
    return round(min(1.0, max(0.0, base - skips * 0.03)), 2)
