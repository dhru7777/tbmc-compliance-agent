"""Agent — LLM-driven document gap review (uses research API key)."""

from __future__ import annotations

from app.services.agents import gaps, llm_client
from app.services.agents.trace_labels import short_words
from app.services.llm_usage import UsageSession

_GAP_TO_DOC_HINTS: dict[str, str] = {
    "legal_name": "Articles of Incorporation / Certificate of Formation",
    "incorporation_state": "Articles of Incorporation (shows state of formation)",
    "formation_verified": "Articles of Incorporation or SOS formation certificate",
    "entity_status": "Certificate of Good Standing",
    "ein": "IRS EIN Confirmation Letter (CP 575)",
    "beneficial_owners": "Beneficial Ownership Certification",
    "operating_address": "Proof of Business Address",
    "business_purpose": "Business Purpose Statement",
    "control_persons": "Operating Agreement excerpt or identity document naming control person",
    "government_id": "Government-issued ID or identity verification result",
}


def _deterministic_advice(
    gap_list: list[dict],
    documents: list[dict],
    extractions: list[dict],
) -> dict:
    """Fallback when research API key is missing."""
    doc_labels = " ".join(
        f"{d.get('label', '')} {d.get('filename', '')}".lower() for d in documents
    )
    missing: list[dict] = []
    for gap in gap_list:
        field = gap.get("field", "")
        hint = _GAP_TO_DOC_HINTS.get(field, "Supporting KYB document")
        tokens = hint.lower().split()
        if any(t in doc_labels for t in tokens[:3]):
            continue
        missing.append(
            {
                "document_type": field,
                "label": hint,
                "reason": str(gap.get("reason", "Required"))[:48],
                "priority": "required" if field in ("ein", "beneficial_owners", "legal_name") else "recommended",
            }
        )
    if not missing:
        return {
            "action": "continue",
            "user_message": "",
            "missing_documents": [],
            "think_label": "Submitted documents appear sufficient",
        }
    return {
        "action": "request_documents",
        "user_message": "",
        "missing_documents": missing,
        "think_label": "Additional documents required",
    }


def advise_document_gaps(
    *,
    claims: dict,
    gap_list: list[dict],
    documents: list[dict],
    extractions: list[dict],
    usage_session: UsageSession | None = None,
) -> dict:
    """
    Research-agent decision: continue to scorecard or pause for more uploads.
    Returns action, user_message, missing_documents[], think_label, act_label.
    """
    api_key = llm_client.research_api_key()
    if not api_key:
        if usage_session:
            usage_session.add_skip(
                "Document gap advisor",
                agent="document_gap_advisor",
                note="deterministic rules — no research API key",
            )
        return _deterministic_advice(gap_list, documents, extractions)

    uploaded = [
        {
            "label": d.get("label"),
            "filename": d.get("filename"),
            "document_type": (e.get("extracted") or {}).get("document_type"),
            "entity_name": (e.get("extracted") or {}).get("entity_name"),
        }
        for d, e in zip(documents, extractions)
    ]
    if len(uploaded) < len(documents):
        uploaded = [{"label": d.get("label"), "filename": d.get("filename")} for d in documents]

    gap_text = "\n".join(f"- {g['field']}: {g['reason']}" for g in gap_list) or "None detected"
    doc_text = "\n".join(
        f"- {u.get('label') or u.get('filename')}: type={u.get('document_type')}" for u in uploaded
    ) or "None"

    prompt = f"""You are the KYB document gap advisor. Review what the applicant submitted and decide whether verification can continue or more documents are needed.

INTERNAL CLAIMS (from form + extractions):
{claims}

DETERMINISTIC GAP HINTS (may be incomplete — use your judgment):
{gap_text}

UPLOADED DOCUMENTS:
{doc_text}

KYB DOCUMENT CATEGORIES (typical):
- Articles of Incorporation / Certificate of Formation
- Certificate of Good Standing
- EIN Confirmation Letter (CP 575)
- Proof of Business Address
- Operating Agreement excerpt
- Beneficial Ownership Certification
- Business Purpose Statement
- Government-issued ID / Identity verification

RULES:
- action "continue" if submitted documents reasonably cover gaps (e.g. combined formation doc with EIN and ownership).
- action "request_documents" if critical private fields (EIN, beneficial owners, control person, legal name) cannot be verified from current uploads.
- Do NOT require duplicate document types already clearly present.
- user_message: leave empty (not shown in UI).
- missing_documents: list only what is still needed; label = short name only; reason = max 8 words.
- think_label / act_label: 5-6 word English phrases.

Return JSON only:
{{
  "action": "continue|request_documents",
  "user_message": "string or empty if continue",
  "missing_documents": [{{"document_type": "ein", "label": "EIN Confirmation Letter", "reason": "...", "priority": "required"}}],
  "think_label": "...",
  "act_label": "..."
}}"""

    try:
        decision = llm_client.call_json(
            api_key=api_key,
            prompt=prompt,
            max_tokens=llm_client.PLANNER_OUTPUT_MAX,
            operation="Document gap advisor",
            agent="document_gap_advisor",
            usage_session=usage_session,
        )
        action = decision.get("action", "continue")
        if action not in ("continue", "request_documents"):
            action = "continue"
        missing = decision.get("missing_documents") or []
        if not isinstance(missing, list):
            missing = []
        if action == "request_documents" and not missing:
            action = "continue"
        decision["action"] = action
        decision["missing_documents"] = missing
        decision.setdefault("think_label", short_words(decision.get("reason", "Document review complete"), 6))
        decision.setdefault("act_label", "Request additional KYB documents" if action == "request_documents" else "Proceed to verification scorecard")
        return decision
    except Exception:
        if usage_session:
            usage_session.add_skip(
                "Document gap advisor",
                agent="document_gap_advisor",
                note="LLM failed — deterministic fallback",
            )
        return _deterministic_advice(gap_list, documents, extractions)
