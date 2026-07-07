"""Clear English labels (5–6 words) for agent trace ReAct steps."""

from __future__ import annotations

import re

TRACE_LABEL_MAX_WORDS = 6


def short_words(text: str, max_words: int = TRACE_LABEL_MAX_WORDS) -> str:
    """First N words for compact but readable trace display."""
    if not text or not str(text).strip():
        return "…"
    cleaned = re.sub(r"[«»\"'`,.;:!?\[\](){}—–-]", " ", str(text))
    words = [w for w in cleaned.split() if w][:max_words]
    if not words:
        return "…"
    label = " ".join(words)
    return label[0].upper() + label[1:] if label else "…"


def act_label_for_action(action: str) -> str:
    return {
        "skip_search": "Skipping web search documents sufficient",
        "public_search": "Running public registry verification search",
        "need_internal": "Waiting for more internal form fields",
        "finish": "Finishing research planning phase now",
    }.get(action, short_words(action.replace("_", " ")))


def observe_label_for_action(action: str, *, search_performed: bool = False) -> str:
    if action == "skip_search":
        return "Uploaded documents satisfy public verification needs"
    if action == "public_search":
        return "Public registry search completed successfully" if search_performed else "Public registry search was attempted"
    if action == "need_internal":
        return "Rechecked internal documents after planner feedback"
    if action == "finish":
        return "Research planning phase completed successfully"
    return "Agent step completed successfully"


def public_search_act_label(legal_name: str, state: str) -> str:
    state = (state or "").strip().upper()[:2]
    name = short_words(legal_name, 3)
    if state:
        return f"Searching {state} public registry for {name}"
    return f"Searching public registry for {name}"


def public_search_observe_label(*, publicly_verified: bool | None, summary: str = "") -> str:
    if publicly_verified is True:
        return "Public registry check passed successfully"
    if publicly_verified is False:
        return "Public registry check failed no match"
    lower = (summary or "").lower()
    if lower.startswith("yes"):
        return "Public registry check passed successfully"
    if lower.startswith("no"):
        return "Public registry check failed no match"
    return short_words(summary) or "Public registry check completed"


def doc_extract_act_label(filename: str = "") -> str:
    if filename:
        return f"Reading uploaded file {short_words(filename, 4)}"
    return "Reading uploaded supporting document file"


def doc_extract_observe_label(entity: str | None) -> str:
    if entity and str(entity).strip() and str(entity).strip() != "—":
        return f"Document extraction completed successfully"
    return "Document extraction completed with warnings"


def scorecard_result_label(*, kyb_status: str, flags_count: int, blocks_count: int) -> str:
    if kyb_status == "passed":
        return "KYB verification completed successfully"
    if blocks_count:
        return "KYB verification failed admission blocked"
    if flags_count:
        return "KYB verification flagged for review"
    return f"KYB verification finished as {kyb_status}"
