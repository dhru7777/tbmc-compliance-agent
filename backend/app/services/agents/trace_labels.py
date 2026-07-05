"""Ultra-short UI labels (2–4 words) for agent trace ReAct steps."""

from __future__ import annotations

import re


def short_words(text: str, max_words: int = 4) -> str:
    """First N words, lowercased, for compact trace display."""
    if not text or not str(text).strip():
        return "…"
    cleaned = re.sub(r"[«»\"'`,.;:!?\[\](){}]", " ", str(text))
    words = [w for w in cleaned.split() if w][:max_words]
    return " ".join(words).lower() if words else "…"


def act_label_for_action(action: str) -> str:
    return {
        "skip_search": "skip web search",
        "public_search": "run registry search",
        "need_internal": "need internal fields",
        "finish": "finish research",
    }.get(action, short_words(action.replace("_", " "), 4))


def observe_label_for_action(action: str, *, search_performed: bool = False) -> str:
    if action == "skip_search":
        return "docs cover public"
    if action == "public_search":
        return "registry data found" if search_performed else "search attempted"
    if action == "need_internal":
        return "recheck internal docs"
    if action == "finish":
        return "research phase done"
    return "step complete"
