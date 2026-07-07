"""Shared Anthropic client helpers for agent modules."""

from __future__ import annotations

import json
import os
import re
from typing import Any

SEARCH_INPUT_BUDGET = int(os.getenv("KYB_SEARCH_INPUT_BUDGET", "1500"))
SEARCH_OUTPUT_MAX = int(os.getenv("KYB_SEARCH_OUTPUT_MAX", "300"))
PLANNER_OUTPUT_MAX = int(os.getenv("KYB_PLANNER_OUTPUT_MAX", "600"))
DOC_EXTRACT_OUTPUT_MAX = int(os.getenv("KYB_DOC_EXTRACT_OUTPUT_MAX", "512"))
DEFAULT_MODEL = os.getenv("KYB_LLM_MODEL", "claude-sonnet-4-6")


def doc_api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def research_api_key() -> str:
    return (
        os.getenv("ANTHROPIC_API_KEY_RESEARCH", "").strip()
        or os.getenv("ANTHROPIC_API_KEY_2", "").strip()
        or doc_api_key()
    )


def _parse_json_from_text(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        raw = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise json.JSONDecodeError("No JSON object found", text, 0)
        raw = text[start : end + 1]
    return json.loads(raw.strip())


def call_json(
    *,
    api_key: str,
    prompt: str,
    max_tokens: int,
    model: str = DEFAULT_MODEL,
    operation: str = "llm_call",
    agent: str = "",
    usage_session: Any | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("Anthropic API key is not configured")

    import anthropic

    from app.services.llm_usage import usage_from_message

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if usage_session is not None:
        record = usage_from_message(message, operation=operation, model=model)
        record.agent = agent
        usage_session.add(record)
    text = "\n".join(block.text for block in message.content if block.type == "text")
    return _parse_json_from_text(text)


def truncate_for_budget(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
