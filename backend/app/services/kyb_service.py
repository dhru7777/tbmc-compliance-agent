"""KYB session orchestration: upload → VERIFY (AI + deterministic) → x401 → credential store."""

import json
import uuid
from datetime import datetime, timezone

from app.services import credential_store, kyb_rules, llm_search, md_recorder, session_store, verify_service, x401_service

_sessions: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_session(session_id: str) -> dict:
    if session_id in _sessions:
        return _sessions[session_id]
    stored = session_store.load_session(session_id)
    if stored:
        _sessions[session_id] = stored
        return stored
    raise KeyError(f"Session {session_id} not found")


def _persist(session: dict) -> None:
    _sessions[session["session_id"]] = session
    session_store.save_session(session)


async def create_session() -> dict:
    """Create an empty KYB session — search runs later as user types."""
    session_id = str(uuid.uuid4())
    session = {
        "session_id": session_id,
        "created_at": _now(),
        "updated_at": _now(),
        "public_facts": None,
        "public_facts_confirmed": False,
        "search_status": "idle",
        "user_claims": {
            "legal_name": "",
            "state": "",
            "operating_address": "",
            "business_purpose": "",
            "ein": "",
            "beneficial_owners": [],
            "control_persons": [],
        },
        "documents": [],
        "doc_extractions": [],
        "middesk": None,
    }
    _persist(session)
    md_recorder.init_session(session_id, "(pending)", "—")
    md_recorder.append_step(session_id, "Session started", ["- Awaiting user details…"])
    return {"session_id": session_id}


async def refresh_public_search(
    session_id: str,
    legal_name: str,
    state: str,
    operating_address: str = "",
    business_purpose: str = "",
) -> dict:
    """Run public record search and update session (called on debounced input)."""
    session = _get_session(session_id)
    state_code = state.strip().upper() if state else ""
    name_key = legal_name.strip().lower()
    session["user_claims"].update(
        {
            "legal_name": legal_name,
            "state": state_code,
            "operating_address": operating_address,
            "business_purpose": business_purpose,
        }
    )

    cached_name = session.get("_last_search_name", "")
    cached_state = session.get("_last_search_state", "")
    reuse_public = (
        bool(session.get("public_facts"))
        and name_key == cached_name
        and state_code == cached_state
    )

    if reuse_public:
        public_facts = session["public_facts"]
        session["search_status"] = "done"
    else:
        session["search_status"] = "searching"
        public_facts = await llm_search.search_company_public_info(legal_name, state_code)
        session["public_facts"] = public_facts
        session["public_facts_confirmed"] = not public_facts.get("needs_user_confirm", True)
        session["_last_search_name"] = name_key
        session["_last_search_state"] = state_code
        session["search_status"] = "done"
        md_recorder.append_step(
            session_id,
            "Public record search",
            [
                f"- Legal name: {legal_name}",
                f"- State: {state_code or '(not provided — name-only search)'}",
                f"- Search method: {public_facts.get('search_method', 'unknown')}{' (cached)' if public_facts.get('from_cache') else ''}",
                f"- Confidence: {public_facts.get('confidence', 0):.0%}",
                f"- Status: {public_facts.get('status', 'unknown')}",
            ],
        )

    session["updated_at"] = _now()
    session["middesk"] = kyb_rules.middesk_corroborate(legal_name, state_code)
    _persist(session)

    checks = {}
    if operating_address or business_purpose:
        checks = {
            "address": kyb_rules.check_address(
                operating_address, public_facts.get("registered_agent_address")
            ),
            "purpose": kyb_rules.check_purpose(
                business_purpose, public_facts.get("naics_or_purpose")
            ),
        }

    return {
        "session_id": session_id,
        "public_facts": public_facts,
        "search_status": "done",
        "needs_user_confirm": public_facts.get("needs_user_confirm", True),
        "cross_checks": checks,
        "middesk": session["middesk"],
    }


async def start_kyb(
    legal_name: str,
    state: str,
    operating_address: str = "",
    business_purpose: str = "",
) -> dict:
    session_id = str(uuid.uuid4())
    public_facts = await llm_search.search_company_public_info(legal_name, state)

    session = {
        "session_id": session_id,
        "created_at": _now(),
        "updated_at": _now(),
        "public_facts": public_facts,
        "public_facts_confirmed": not public_facts.get("needs_user_confirm", True),
        "user_claims": {
            "legal_name": legal_name,
            "state": state.upper(),
            "operating_address": operating_address,
            "business_purpose": business_purpose,
            "ein": "",
            "beneficial_owners": [],
            "control_persons": [],
        },
        "documents": [],
        "doc_extractions": [],
        "middesk": kyb_rules.middesk_corroborate(legal_name, state),
    }
    _sessions[session_id] = session

    md_recorder.init_session(session_id, legal_name, state.upper())
    md_lines = [
        f"- Legal name: {legal_name}",
        f"- State: {state.upper()}",
        f"- Search method: {public_facts.get('search_method', 'unknown')}",
        f"- LLM confidence: {public_facts.get('confidence', 0):.0%}",
        f"- Status found: {public_facts.get('status', 'unknown')}",
        f"- Rationale: {public_facts.get('rationale', '')}",
    ]
    for url in public_facts.get("source_urls", [])[:5]:
        md_lines.append(f"- Source: {url}")
    md_recorder.append_step(session_id, "Step 1 — LLM Public Search", md_lines)

    return {
        "session_id": session_id,
        "public_facts": public_facts,
        "needs_user_confirm": public_facts.get("needs_user_confirm", True),
        "middesk": session["middesk"],
    }


async def confirm_entity(session_id: str, confirmed: bool = True) -> dict:
    session = _get_session(session_id)
    session["public_facts_confirmed"] = confirmed
    session["updated_at"] = _now()
    md_recorder.append_step(
        session_id,
        "Step 2 — Entity Confirmation",
        [f"- User confirmed public record: {confirmed}"],
    )
    return {
        "session_id": session_id,
        "confirmed": confirmed,
        "public_facts": session["public_facts"],
    }


async def cross_check_preview(session_id: str, operating_address: str, business_purpose: str) -> dict:
    session = _get_session(session_id)
    session["user_claims"]["operating_address"] = operating_address
    session["user_claims"]["business_purpose"] = business_purpose
    public = session.get("public_facts") or {}
    checks = {
        "address": kyb_rules.check_address(operating_address, public.get("registered_agent_address")),
        "purpose": kyb_rules.check_purpose(business_purpose, public.get("naics_or_purpose")),
    }
    return {"session_id": session_id, "cross_checks": checks}


async def run_verify_only(
    session_id: str,
    uploads: list[tuple[str, str, bytes]],
    legal_name: str = "",
    state: str = "",
    ein: str = "",
    operating_address: str = "",
    business_purpose: str = "",
    beneficial_owners: list[dict] | None = None,
    control_persons: list[dict] | None = None,
) -> dict:
    """VERIFY endpoint — AI parse + public presence + deterministic cross-ref. No credential issued."""
    session = _get_session(session_id)
    if legal_name:
        session["user_claims"]["legal_name"] = legal_name
    if state:
        session["user_claims"]["state"] = state.upper()
    session["user_claims"].update(
        {
            "ein": ein,
            "operating_address": operating_address,
            "business_purpose": business_purpose,
            "beneficial_owners": beneficial_owners or [],
            "control_persons": control_persons or [],
        }
    )
    session["updated_at"] = _now()
    verify_result = await verify_service.run_verify(session, uploads, refresh_public=True)
    _log_verify_session(session_id, verify_result)
    return {"session_id": session_id, **verify_result}


def _log_verify_session(session_id: str, verify_result: dict) -> None:
    ai = verify_result.get("ai", {})
    det = verify_result.get("deterministic", {})
    sc = det.get("scorecard", {})
    md_recorder.append_step(
        session_id,
        "VERIFY — AI + Deterministic",
        [
            f"- Public search: {ai.get('public_presence', {}).get('search_method', 'n/a')}",
            f"- Documents parsed: {ai.get('documents', {}).get('count', 0)}",
            f"- KYB status: {sc.get('kyb_status', 'unknown')}",
            f"- Flags: {sc.get('flags_count', 0)}, Blocks: {sc.get('blocks_count', 0)}",
            "- Raw document bytes discarded after parse (not stored)",
        ],
    )
    for ext in ai.get("documents", {}).get("extractions", []):
        cache_note = " (cached)" if ext.get("from_cache") else ""
        md_recorder.append_step(
            session_id,
            f"Doc parse — {ext.get('label', 'document')}{cache_note}",
            [f"- Extracted: {json.dumps(ext.get('extracted', {}))}"],
        )


async def submit_kyb(
    session_id: str,
    ein: str,
    operating_address: str,
    business_purpose: str,
    beneficial_owners: list[dict],
    control_persons: list[dict],
    uploads: list[tuple[str, str, bytes]],
    legal_name: str = "",
    state: str = "",
) -> dict:
    session = _get_session(session_id)
    if legal_name:
        session["user_claims"]["legal_name"] = legal_name
    if state:
        session["user_claims"]["state"] = state.upper()
    session["user_claims"].update(
        {
            "ein": ein,
            "operating_address": operating_address,
            "business_purpose": business_purpose,
            "beneficial_owners": beneficial_owners,
            "control_persons": control_persons,
        }
    )

    verify_result = await verify_service.run_verify(session, uploads, refresh_public=False)
    _log_verify_session(session_id, verify_result)

    scorecard = verify_result["deterministic"]["scorecard"]
    session["updated_at"] = _now()
    _persist(session)

    md_recorder.append_step(
        session_id,
        "Submit complete — x401 deferred",
        [
            f"- KYB status: {scorecard.get('kyb_status', 'unknown')}",
            f"- Flags: {scorecard.get('flags_count', 0)}, Blocks: {scorecard.get('blocks_count', 0)}",
            "- x401 credential issuance not yet simulated",
            "- Raw document bytes discarded after parse (not stored)",
        ],
    )

    return {
        "session_id": session_id,
        "pipeline": {
            "upload": {"document_count": len(uploads), "stored_raw_documents": False},
            "verify": {"stage": "verify", "kyb_status": scorecard.get("kyb_status")},
            "x401": {"status": "deferred", "message": "x401 credential simulation not yet enabled"},
        },
        "scorecard": scorecard,
        "public_facts": session.get("public_facts"),
        "middesk": session.get("middesk"),
        "record_url": f"/api/enterprise/kyb/{session_id}/record",
    }


def get_session_summary(session_id: str) -> dict:
    session = _get_session(session_id)
    return {
        "session_id": session_id,
        "created_at": session.get("created_at"),
        "has_public_facts": bool(session.get("public_facts")),
    }


def get_credential(session_id: str) -> dict | None:
    _get_session(session_id)
    return credential_store.load_credential(session_id)


def get_record(session_id: str) -> str:
    _get_session(session_id)
    return md_recorder.read_session(session_id)
