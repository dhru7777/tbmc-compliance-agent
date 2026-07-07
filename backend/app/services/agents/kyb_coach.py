"""Agentic KYB coach — short suggestive prompts only; answers user questions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.agents import llm_client
from app.services.llm_usage import UsageSession

OBJECTIVE_PASSED = "achieved"
OBJECTIVE_IN_PROGRESS = "in_progress"
OBJECTIVE_BLOCKED = "blocked"

# Proactive coach events — skip chat noise on extract/session
_SILENT_EVENTS = frozenset({"session_start", "documents_extracted", "documents_added"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_missing_label(doc: dict) -> str:
    return str(doc.get("label") or doc.get("document_type") or "document").strip()


def _fallback_turn(
    *,
    event: str,
    session: dict,
    verify_result: dict | None = None,
    user_message: str | None = None,
) -> dict | None:
    if event in _SILENT_EVENTS:
        return None

    sc = ((verify_result or {}).get("deterministic") or {}).get("scorecard") if verify_result else None
    sc = sc or session.get("last_scorecard") or {}

    if sc.get("kyb_status") == "passed":
        return {
            "role": "assistant",
            "message": "Verification passed. Generate your certificate on the scorecard.",
            "objective_status": OBJECTIVE_PASSED,
            "suggested_actions": [{"action": "view_scorecard", "label": "View scorecard"}],
        }

    if event == "user_message" and user_message:
        return {
            "role": "assistant",
            "message": "Upload any missing documents, then run verification again.",
            "objective_status": OBJECTIVE_IN_PROGRESS,
            "suggested_actions": [],
        }

    gaps = (verify_result or {}).get("document_gaps") or session.get("document_gaps") or {}
    missing = gaps.get("missing_documents") or []
    if missing or (verify_result or {}).get("pipeline_status") == "needs_documents":
        label = _short_missing_label(missing[0]) if missing else "required document"
        extra = f" (+{len(missing) - 1} more)" if len(missing) > 1 else ""
        actions = []
        if missing:
            actions.append({"action": "upload_document", "label": f"Upload {_short_missing_label(missing[0])}"})
        actions.append({"action": "run_verification", "label": "Run verification"})
        return {
            "role": "assistant",
            "message": f"Upload {label}{extra}, then run verification.",
            "objective_status": OBJECTIVE_IN_PROGRESS,
            "suggested_actions": actions,
        }

    flags = [i for i in (sc.get("items") or []) if i.get("result") == "FLAG"]
    if flags and event == "verify_complete":
        return {
            "role": "assistant",
            "message": f"{len(flags)} item(s) need review — fix and run verification again.",
            "objective_status": OBJECTIVE_IN_PROGRESS,
            "suggested_actions": [{"action": "run_verification", "label": "Run verification"}],
        }

    return None


def generate_coach_turn(
    *,
    session: dict,
    event: str = "user_message",
    user_message: str | None = None,
    verify_result: dict | None = None,
    usage_session: UsageSession | None = None,
) -> dict[str, Any] | None:
    """Return a coach turn, or None when there is nothing useful to say."""
    if event in _SILENT_EVENTS:
        return None

    sc = ((verify_result or {}).get("deterministic") or {}).get("scorecard") if verify_result else None
    sc = sc or session.get("last_scorecard") or {}
    if sc.get("kyb_status") == "passed":
        turn = _fallback_turn(event=event, session=session, verify_result=verify_result, user_message=user_message)
        if turn:
            turn["at"] = _now_iso()
            turn["event"] = event
        return turn

    api_key = llm_client.research_api_key()
    if not api_key or (event != "user_message" and not user_message):
        turn = _fallback_turn(event=event, session=session, verify_result=verify_result, user_message=user_message)
        if turn:
            turn["at"] = _now_iso()
            turn["event"] = event
        return turn

    gaps = (verify_result or {}).get("document_gaps") if verify_result else session.get("document_gaps")
    missing = (gaps or {}).get("missing_documents") or []
    pipeline = (verify_result or {}).get("pipeline_status") if verify_result else session.get("pipeline_status")

    if event != "user_message" and not missing and pipeline != "needs_documents" and event != "verify_complete":
        return None

    prompt = f"""KYB coach — be minimal.

EVENT: {event}
USER: {user_message or ""}
MISSING DOCS: {", ".join(_short_missing_label(m) for m in missing[:4]) or "none"}
KYB STATUS: {sc.get("kyb_status", "pending")}

RULES:
- Proactive (missing docs): ONE short sentence. Example: "Upload Beneficial Ownership Certification, then run verification."
- User question: answer in 1-2 short sentences only.
- No greetings, no "progress saved", no regulatory boilerplate, no listing what's already on file.
- suggested_actions: upload_document and/or run_verification only when helpful.

JSON:
{{"message": "...", "objective_status": "in_progress|achieved|blocked", "suggested_actions": [{{"action": "upload_document|run_verification|view_scorecard", "label": "..."}}]}}"""

    try:
        data = llm_client.call_json(
            api_key=api_key,
            prompt=prompt,
            max_tokens=200,
            operation="KYB coach",
            agent="kyb_coach",
            usage_session=usage_session,
        )
        msg = str(data.get("message", "")).strip()
        if not msg:
            return _fallback_turn(event=event, session=session, verify_result=verify_result, user_message=user_message)
        turn = {
            "role": "assistant",
            "message": msg[:280],
            "objective_status": data.get("objective_status", OBJECTIVE_IN_PROGRESS),
            "suggested_actions": (data.get("suggested_actions") or [])[:3],
            "at": _now_iso(),
            "event": event,
        }
        return turn
    except Exception:
        if usage_session:
            usage_session.add_skip("KYB coach", agent="kyb_coach", note="LLM failed")
        return _fallback_turn(event=event, session=session, verify_result=verify_result, user_message=user_message)


def append_chat(session: dict, turn: dict | None) -> None:
    if not turn:
        return
    messages = list(session.get("chat_messages") or [])
    event = turn.get("event", "")
    if turn.get("role") == "assistant" and event in ("verify_needs_documents", "verify_complete"):
        messages = [m for m in messages if m.get("role") == "user"]
    messages.append(turn)
    session["chat_messages"] = messages[-30:]
    session["objective_status"] = turn.get("objective_status", OBJECTIVE_IN_PROGRESS)
    session["coach_last_message"] = turn.get("message", "")
