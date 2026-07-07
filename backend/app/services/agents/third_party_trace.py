"""Third-party KYB trace steps (Middesk business, Persona identity)."""

from __future__ import annotations

from typing import Any

from app.services import kyb_rules
from app.services.agents.trace import AgentTrace
from app.services.agents.trace_labels import short_words
from app.services.llm_usage import UsageSession

MIDDESK_CHECKLIST_NUMS = (1, 2, 3, 4, 5, 6)
PERSONA_CHECKLIST_NUMS = (7, 8, 9, 10)

_ITEM_SHORT = {
    1: "legal business name",
    2: "formation documents",
    3: "good standing status",
    4: "OFAC sanctions screen",
    5: "business address",
    6: "business purpose",
    7: "EIN tax identifier",
    8: "beneficial ownership",
    9: "control person identities",
    10: "government issued identification",
}


def _check_failed(result: str) -> bool:
    return result in ("FLAG", "BLOCK")


def _check_label(provider: str, topic: str, result: str) -> str:
    if result == "PASS":
        return short_words(f"{provider} {topic} passed successfully", 6)
    if result == "SKIP":
        return short_words(f"{provider} {topic} check skipped", 6)
    return short_words(f"{provider} {topic} check failed", 6)


def _batch_summary_label(provider: str, scope: str, results: list[str]) -> str:
    if any(_check_failed(r) for r in results):
        return short_words(f"{provider} {scope} checks failed")
    if all(r == "SKIP" for r in results):
        return short_words(f"{provider} {scope} checks skipped")
    return short_words(f"{provider} {scope} checks passed successfully")


def middesk_observe_label(item_num: int, result: str) -> str:
    topic = _ITEM_SHORT.get(item_num, "business KYB field")
    return _check_label("Middesk", topic, result)


def persona_observe_label(item_num: int, result: str) -> str:
    topic = _ITEM_SHORT.get(item_num, "private identity field")
    return _check_label("Persona", topic, result)


async def build_scorecard_with_third_party_trace(
    trace: AgentTrace,
    session: dict,
    usage: UsageSession | None = None,
) -> dict[str, Any]:
    """
    Run deterministic scorecard and emit readable Middesk + Persona agent steps.
    Simulates production vendor flow — no live vendor API keys configured.
    """
    await trace.emit(
        "act",
        "middesk",
        "POST /v1/businesses/verify — Middesk business KYB (simulated).",
        label="Calling Middesk business identity API",
    )
    if usage:
        usage.add_skip(
            "Middesk business KYB verify",
            agent="middesk",
            note="deterministic rules — live Middesk API not configured",
        )

    scorecard = kyb_rules.build_scorecard(session)
    items_by_num = {int(i["num"]): i for i in scorecard.get("items") or []}

    middesk_results: list[str] = []
    for num in MIDDESK_CHECKLIST_NUMS:
        item = items_by_num.get(num)
        if not item:
            continue
        result = item.get("result", "FLAG")
        middesk_results.append(result)
        detail = item.get("detail") or item.get("item", "")
        await trace.emit(
            "observe",
            "middesk",
            f"Middesk — {item.get('item')}: {detail}",
            label=middesk_observe_label(num, result),
            checklist_num=num,
            checklist_result=result,
            checklist_detail=detail,
            checklist_recommendation=item.get("recommendation") or "",
            vendor="middesk",
            trace_visible=False,
        )

    if middesk_results:
        summary = _batch_summary_label("Middesk", "business KYB", middesk_results)
        await trace.emit(
            "observe",
            "middesk",
            summary,
            label=summary,
            vendor="middesk",
            vendor_batch_passed=not any(_check_failed(r) for r in middesk_results),
        )

    await trace.emit(
        "act",
        "persona",
        "POST /v1/inquiries — Persona identity verification (simulated).",
        label="Calling Persona identity verification API",
    )
    if usage:
        usage.add_skip(
            "Persona identity verify",
            agent="persona",
            note="deterministic rules — live Persona API not configured",
        )

    persona_results: list[str] = []
    for num in PERSONA_CHECKLIST_NUMS:
        item = items_by_num.get(num)
        if not item:
            continue
        result = item.get("result", "FLAG")
        persona_results.append(result)
        detail = item.get("detail") or item.get("item", "")
        await trace.emit(
            "observe",
            "persona",
            f"Persona — {item.get('item')}: {detail}",
            label=persona_observe_label(num, result),
            checklist_num=num,
            checklist_result=result,
            checklist_detail=detail,
            checklist_recommendation=item.get("recommendation") or "",
            vendor="persona",
            trace_visible=False,
        )

    if persona_results:
        summary = _batch_summary_label("Persona", "identity", persona_results)
        await trace.emit(
            "observe",
            "persona",
            summary,
            label=summary,
            vendor="persona",
            vendor_batch_passed=not any(_check_failed(r) for r in persona_results),
        )

    return scorecard
