"""Agent 1 — extract structured KYB claims from uploaded documents."""

from __future__ import annotations

import asyncio
from io import BytesIO

from app.services import api_cache
from app.services.demo_companies import (
    is_mock_package_document,
    is_trial_document_text,
    parse_mock_package_fields,
    parse_trial_document_fields,
    trial_document_fields_from_company,
)
from app.services.agents import llm_client
from app.services.agents.trace import AgentTrace
from app.services.agents.trace_labels import doc_extract_act_label, doc_extract_observe_label
from app.services.llm_usage import UsageSession


def extract_text_from_upload(filename: str, content: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""
    if lower.endswith((".txt", ".md")):
        return content.decode("utf-8", errors="ignore")
    return ""


def _is_stale_placeholder_extraction(prior: dict) -> bool:
    extracted = prior.get("extracted") or {}
    facts = " ".join(str(f) for f in (extracted.get("key_facts") or [])).upper()
    if "NOT SUBMITTED" in facts or "DOCUMENT SLOT" in facts:
        return True
    if "NOT SUBMITTED" in str(extracted).upper() and not extracted.get("ein"):
        return True
    return False


def _should_reextract_file(prior: dict | None, filename: str, content: bytes, content_hash: str) -> bool:
    if not prior:
        return True
    if _is_stale_placeholder_extraction(prior):
        return True
    text = extract_text_from_upload(filename, content)
    if is_mock_package_document(text) and not prior.get("mock_package"):
        return True
    same_bytes = prior.get("content_hash") == content_hash
    has_parse = bool(prior.get("extracted")) or prior.get("text_length", 0) > 0
    return not (same_bytes and has_parse)


def _extract_one_sync(
    label: str,
    text_content: str,
    usage_session: UsageSession | None = None,
    trial_company_id: str | None = None,
) -> dict:
    text_hash = api_cache.content_hash(text_content[:8000])
    label_key = label.strip().lower()

    if is_mock_package_document(text_content):
        if usage_session:
            usage_session.add_skip(
                f"Document extraction ({label})",
                agent="doc_extractor",
                note="mock package — deterministic parse",
            )
        return {
            "label": label,
            "extracted": parse_mock_package_fields(text_content),
            "note": "Mock package — deterministic extraction",
            "mock_package": True,
        }

    if is_trial_document_text(text_content):
        if usage_session:
            usage_session.add_skip(
                f"Document extraction ({label})",
                agent="doc_extractor",
                note="trial package — deterministic parse",
            )
        extracted = parse_trial_document_fields(text_content)
        return {
            "label": label,
            "extracted": extracted,
            "note": "Trial document — deterministic extraction (no LLM cache)",
            "trial_document": True,
        }

    if trial_company_id and not text_content.strip():
        if usage_session:
            usage_session.add_skip(
                f"Document extraction ({label})",
                agent="doc_extractor",
                note="trial package — unreadable file fallback",
            )
        return {
            "label": label,
            "extracted": trial_document_fields_from_company(trial_company_id),
            "note": "Trial document — deterministic extraction (no LLM cache)",
            "trial_document": True,
        }

    cached = api_cache.get("doc_extract", label_key, text_hash)
    if cached is not None:
        if usage_session:
            usage_session.add_cache(
                f"Document extraction ({label})",
                agent="doc_extractor",
            )
        return api_cache.mark_cached(cached)

    api_key = llm_client.doc_api_key()
    if not api_key or not text_content.strip():
        if usage_session:
            usage_session.add_skip(
                f"Document extraction ({label})",
                agent="doc_extractor",
                note="no text or API key",
            )
        result = {"label": label, "extracted": {}, "note": "No text extracted or API key missing"}
        api_cache.set("doc_extract", result, label_key, text_hash)
        return result

    prompt = f"""Extract structured KYB claims from this document labeled "{label}".
Return JSON only:
{{
  "document_type": "sos_filing|articles|good_standing|ein_letter|beneficial_ownership|operating_agreement|government_id|address_proof|business_purpose|other|null",
  "entity_name": null,
  "ein": null,
  "incorporation_state": null,
  "person_name": null,
  "address": null,
  "formation_date": null,
  "beneficial_owners": [{{"name": "string", "ownership_pct": 100}}],
  "control_persons": [{{"name": "string", "title": "CEO|Managing Member|President"}}],
  "key_facts": []
}}
Rules:
- entity_name: legal business name on the filing
- ein: federal tax ID in XX-XXXXXXX format when present
- incorporation_state: 2-letter US state code
- beneficial_owners / control_persons: populate from ownership, officer, or ID sections
- key_facts: short strings such as "Status: Active - in good standing", "Jane Doe: 100% ownership", "Control Person: Jane Doe, CEO"
Document text:
{text_content[:8000]}"""

    try:
        extracted = llm_client.call_json(
            api_key=api_key,
            prompt=prompt,
            max_tokens=llm_client.DOC_EXTRACT_OUTPUT_MAX,
            operation=f"Document extraction ({label})",
            agent="doc_extractor",
            usage_session=usage_session,
        )
        result = {"label": label, "extracted": extracted}
        api_cache.set("doc_extract", result, label_key, text_hash)
        return result
    except Exception as exc:
        return {"label": label, "extracted": {}, "note": str(exc)}


async def extract_document(
    label: str,
    text_content: str,
    usage_session: UsageSession | None = None,
    trial_company_id: str | None = None,
) -> dict:
    return await asyncio.to_thread(
        _extract_one_sync, label, text_content, usage_session, trial_company_id
    )


async def extract_uploads(
    uploads: list[tuple[str, str, bytes]],
    trace: AgentTrace,
    usage_session: UsageSession | None = None,
    trial_company_id: str | None = None,
) -> list[dict]:
    """Parse each upload: text extract → LLM structured claims."""
    if not uploads:
        await trace.emit(
            "observe",
            "doc_extractor",
            "No documents uploaded.",
            label="No documents were uploaded for review",
            document_count=0,
        )
        return []

    n = len(uploads)
    results: list[dict] = []

    for index, (label, filename, content) in enumerate(uploads, start=1):
        await trace.emit(
            "act",
            "doc_extractor",
            f"Reading {label or filename}…",
            label=doc_extract_act_label(filename),
            document_count=n,
            document_index=index,
            filename=filename,
        )

        text = extract_text_from_upload(filename, content)
        extraction = await extract_document(
            label, text or f"[unreadable: {filename}]", usage_session, trial_company_id
        )
        extraction["filename"] = filename
        extraction["text_length"] = len(text)
        extraction["content_hash"] = api_cache.content_hash_bytes(content)
        results.append(extraction)

        entity = (extraction.get("extracted") or {}).get("entity_name")
        observe_label = doc_extract_observe_label(entity)
        await trace.emit(
            "observe",
            "doc_extractor",
            f"Parsed {filename} — entity: {entity or 'unknown'}",
            label=observe_label,
            document_count=n,
            document_index=index,
            filename=filename,
            entity=entity,
        )

    return results


async def extract_uploads_merged(
    uploads: list[tuple[str, str, bytes]],
    existing_extractions: list[dict],
    trace: AgentTrace,
    usage_session: UsageSession | None = None,
    trial_company_id: str | None = None,
) -> list[dict]:
    """Extract only new/changed files; reuse prior extractions when bytes unchanged."""
    cached = {e.get("filename"): e for e in existing_extractions if e.get("filename")}
    new_uploads: list[tuple[str, str, bytes]] = []
    for label, filename, content in uploads:
        prior = cached.get(filename)
        content_hash = api_cache.content_hash_bytes(content)
        if not _should_reextract_file(prior, filename, content, content_hash):
            continue
        new_uploads.append((label, filename, content))

    if new_uploads:
        await trace.emit(
            "observe",
            "doc_extractor",
            f"Extracting {len(new_uploads)} new document(s); reusing {len(uploads) - len(new_uploads)} prior parse(s).",
            label=f"Parsing {len(new_uploads)} new uploaded files",
            document_count=len(uploads),
        )
        fresh = await extract_uploads(new_uploads, trace, usage_session, trial_company_id)
        for item in fresh:
            cached[item["filename"]] = item
    elif uploads:
        await trace.emit(
            "observe",
            "doc_extractor",
            f"Reusing {len(uploads)} prior document extraction(s).",
            label="Reusing prior document extractions",
            document_count=len(uploads),
        )

    merged: list[dict] = []
    for _label, filename, _content in uploads:
        if filename in cached:
            merged.append(cached[filename])
    return merged
