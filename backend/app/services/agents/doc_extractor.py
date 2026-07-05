"""Agent 1 — extract structured KYB claims from uploaded documents."""

from __future__ import annotations

import asyncio
from io import BytesIO

from app.services import api_cache
from app.services.agents import llm_client
from app.services.agents.trace import AgentTrace
from app.services.agents.trace_labels import short_words
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


def _extract_one_sync(
    label: str,
    text_content: str,
    usage_session: UsageSession | None = None,
) -> dict:
    text_hash = api_cache.content_hash(text_content[:8000])
    label_key = label.strip().lower()
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
  "document_type": "sos_filing|articles|license|government_id|other|null",
  "entity_name": null,
  "ein": null,
  "person_name": null,
  "address": null,
  "formation_date": null,
  "key_facts": []
}}
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
) -> dict:
    return await asyncio.to_thread(_extract_one_sync, label, text_content, usage_session)


async def extract_uploads(
    uploads: list[tuple[str, str, bytes]],
    trace: AgentTrace,
    usage_session: UsageSession | None = None,
) -> list[dict]:
    """Parse each upload: text extract → LLM structured claims."""
    if not uploads:
        await trace.emit(
            "observe",
            "doc_extractor",
            "No documents uploaded.",
            label="no files uploaded",
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
            label="read upload",
            document_count=n,
            document_index=index,
            filename=filename,
        )

        text = extract_text_from_upload(filename, content)
        extraction = await extract_document(
            label, text or f"[unreadable: {filename}]", usage_session
        )
        extraction["filename"] = filename
        extraction["text_length"] = len(text)
        results.append(extraction)

        entity = (extraction.get("extracted") or {}).get("entity_name")
        observe_label = (
            short_words(str(entity), 4)
            if entity and entity != "—"
            else short_words("fields extracted", 4)
        )
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
