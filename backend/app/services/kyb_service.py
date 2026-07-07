"""KYB session orchestration: upload → VERIFY (AI + deterministic) → x401 → credential store."""

import json
import os
import uuid
from datetime import datetime, timezone

from app.services import credential_store, demo_companies, kyb_rules, md_recorder, session_store, verify_service, x401_service
from app.services.agents import kyb_coach, public_search
from app.services.verification_store import save_verification_record

_sessions: dict[str, dict] = {}

AGENT_CHAT_ENABLED = os.getenv("KYB_AGENT_CHAT_ENABLED", "false").lower() in ("1", "true", "yes")


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


def _store_uploads(session_id: str, uploads: list[tuple[str, str, bytes]]) -> None:
    for _label, filename, content in uploads:
        session_store.save_upload(session_id, filename, content)


def _merge_upload_lists(
    incoming: list[tuple[str, str, bytes]],
    stored: list[tuple[str, str, bytes]],
) -> list[tuple[str, str, bytes]]:
    by_name = {fn: (label, fn, content) for label, fn, content in stored}
    for label, fn, content in incoming:
        by_name[fn] = (label, fn, content)
    return list(by_name.values())


def _append_verify_attempt(session: dict, verify_result: dict, uploads: list) -> None:
    attempts = list(session.get("verify_attempts") or [])
    sc = (verify_result.get("deterministic") or {}).get("scorecard") or {}
    attempts.append(
        {
            "attempt": len(attempts) + 1,
            "at": _now(),
            "pipeline_status": verify_result.get("pipeline_status") or verify_result.get("stage"),
            "documents_count": len(uploads),
            "kyb_status": sc.get("kyb_status"),
            "flags_count": sc.get("flags_count"),
            "document_gaps": verify_result.get("document_gaps"),
        }
    )
    session["verify_attempts"] = attempts[-20:]


async def extract_documents_preview(
    session_id: str,
    uploads: list[tuple[str, str, bytes]],
    trial_company_id: str | None = None,
) -> dict:
    """Extract structured claims from uploads and merge into session (for form auto-fill)."""
    from app.services.agents import doc_extractor
    from app.services.agents.trace import AgentTrace
    from app.services.demo_companies import is_trial_document_text, match_trial_company_id, resolve_trial_company_id

    session = _get_session(session_id)
    trace = AgentTrace()
    trial_id = resolve_trial_company_id(trial_company_id) or session.get("trial_company_id")

    if not trial_id:
        for _label, filename, content in uploads:
            text = doc_extractor.extract_text_from_upload(filename, content)
            if is_trial_document_text(text):
                trial_id = match_trial_company_id(text)
                if trial_id:
                    break

    if trial_id:
        session["trial_company_id"] = trial_id

    prior = list(session.get("doc_extractions") or [])
    doc_extractions = await doc_extractor.extract_uploads_merged(
        uploads, prior, trace, usage_session=None, trial_company_id=trial_id
    )

    _store_uploads(session_id, uploads)

    user = session.get("user_claims") or {}
    merged = kyb_rules.merge_user_claims_from_extractions(user, doc_extractions)

    session["user_claims"] = merged
    session["documents"] = [{"label": d.get("label"), "filename": d.get("filename")} for d in doc_extractions]
    session["doc_extractions"] = [
        {
            "label": d.get("label"),
            "filename": d.get("filename"),
            "extracted": d.get("extracted", {}),
            "text_length": d.get("text_length", 0),
            "content_hash": d.get("content_hash"),
            "note": d.get("note"),
        }
        for d in doc_extractions
    ]
    session["updated_at"] = _now()
    _persist(session)

    warnings: list[str] = []
    for ext in doc_extractions:
        if ext.get("text_length", 0) == 0:
            warnings.append(f"No readable text in {ext.get('filename') or ext.get('label')} — use text PDFs or .txt")
        elif not (ext.get("extracted") or {}):
            warnings.append(f"Could not parse {ext.get('filename') or ext.get('label')}")

    field_sources: dict[str, str] = {}
    for ext in doc_extractions:
        extracted = ext.get("extracted") or {}
        source = ext.get("filename") or ext.get("label") or "document"
        if extracted.get("entity_name") and not field_sources.get("legal_name"):
            field_sources["legal_name"] = source
        if extracted.get("ein") and not field_sources.get("ein"):
            field_sources["ein"] = source
        if extracted.get("address") and not field_sources.get("operating_address"):
            field_sources["operating_address"] = source
        if extracted.get("incorporation_state") and not field_sources.get("state"):
            field_sources["state"] = source
        for fact in extracted.get("key_facts") or []:
            if "business purpose:" in str(fact).lower() and not field_sources.get("business_purpose"):
                field_sources["business_purpose"] = source
        if extracted.get("beneficial_owners") and not field_sources.get("beneficial_owners"):
            field_sources["beneficial_owners"] = source
        if extracted.get("control_persons") and not field_sources.get("control_persons"):
            field_sources["control_persons"] = source

    session["updated_at"] = _now()
    _persist(session)

    return {
        "session_id": session_id,
        "document_count": len(doc_extractions),
        "suggested_claims": {
            "legal_name": merged.get("legal_name") or "",
            "state": merged.get("state") or "",
            "ein": merged.get("ein") or "",
            "operating_address": merged.get("operating_address") or "",
            "business_purpose": merged.get("business_purpose") or "",
            "beneficial_owners": merged.get("beneficial_owners") or [],
            "control_persons": merged.get("control_persons") or [],
        },
        "field_sources": field_sources,
        "extractions": session["doc_extractions"],
        "warnings": warnings,
        "chat_messages": session.get("chat_messages") or [],
    }


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
            "monthly_volume_low_usd": None,
            "monthly_volume_high_usd": None,
            "beneficial_owners": [],
            "control_persons": [],
        },
        "documents": [],
        "doc_extractions": [],
        "middesk": None,
        "pipeline_status": "draft",
        "document_gaps": None,
        "verify_attempts": [],
        "last_scorecard": None,
        "chat_messages": [],
        "objective_status": kyb_coach.OBJECTIVE_IN_PROGRESS,
        "coach_last_message": "",
    }
    _persist(session)
    md_recorder.init_session(session_id, "(pending)", "—")
    md_recorder.append_step(session_id, "Session started", ["- Awaiting user details…"])
    return {"session_id": session_id, "chat_messages": session.get("chat_messages") or []}


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
        trial_id = session.get("trial_company_id")
        if trial_id:
            search_result = await public_search.run_trial_registry_search(
                trial_id, legal_name, state_code
            )
        else:
            search_result = await public_search.run_bounded_search(legal_name, state_code)
        public_facts = search_result.get("public_facts") or {}
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
    sc = det.get("scorecard") or {}
    status = verify_result.get("pipeline_status") or verify_result.get("stage", "verify")
    lines = [
        f"- Pipeline status: {status}",
        f"- Public search performed: {verify_result.get('search_performed', False)}",
        f"- Documents parsed: {ai.get('documents', {}).get('count', 0)}",
    ]
    if sc:
        lines.extend(
            [
                f"- KYB status: {sc.get('kyb_status', 'unknown')}",
                f"- Flags: {sc.get('flags_count', 0)}, Blocks: {sc.get('blocks_count', 0)}",
            ]
        )
    gaps_info = verify_result.get("document_gaps") or {}
    if gaps_info.get("missing_documents"):
        lines.append("- Missing documents requested:")
        for md in gaps_info["missing_documents"]:
            lines.append(f"  - {md.get('label', md.get('document_type'))}: {md.get('reason', '')}")
    md_recorder.append_step(session_id, "VERIFY — Agentic pipeline", lines)
    for step in verify_result.get("agent_trace", []):
        md_recorder.append_step(
            session_id,
            f"Agent [{step.get('type')}] {step.get('agent')}",
            [f"- {step.get('message', '')}"],
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
    monthly_volume_low_usd: float | None = None,
    monthly_volume_high_usd: float | None = None,
    trial_company_id: str | None = None,
    on_step=None,
) -> dict:
    session = _get_session(session_id)
    claims = session.setdefault("user_claims", {})
    if legal_name:
        claims["legal_name"] = legal_name
    if state:
        claims["state"] = state.upper()
    if ein:
        claims["ein"] = kyb_rules._normalize_ein(ein)
    if operating_address:
        claims["operating_address"] = operating_address
    if business_purpose:
        claims["business_purpose"] = business_purpose
    if beneficial_owners:
        claims["beneficial_owners"] = beneficial_owners
    if control_persons:
        claims["control_persons"] = control_persons
    if monthly_volume_low_usd is not None:
        session["user_claims"]["monthly_volume_low_usd"] = monthly_volume_low_usd
    if monthly_volume_high_usd is not None:
        session["user_claims"]["monthly_volume_high_usd"] = monthly_volume_high_usd
    session["trial_company_id"] = demo_companies.resolve_trial_company_id(trial_company_id)
    if session.get("pipeline_status") != "needs_documents":
        session["public_facts"] = None

    stored = session_store.load_stored_uploads(session_id)
    uploads = _merge_upload_lists(uploads, stored)
    for _label, filename, content in uploads:
        from app.services.agents import doc_extractor
        from app.services.demo_companies import is_mock_package_document

        if is_mock_package_document(doc_extractor.extract_text_from_upload(filename, content)):
            session["trial_company_id"] = None
            break
    _store_uploads(session_id, uploads)

    verify_result = await verify_service.run_verify(session, uploads, on_step=on_step)
    _log_verify_session(session_id, verify_result)
    _append_verify_attempt(session, verify_result, uploads)
    session["updated_at"] = _now()

    event = "verify_needs_documents" if verify_result.get("pipeline_status") == "needs_documents" else "verify_complete"
    coach = None
    if AGENT_CHAT_ENABLED:
        coach = kyb_coach.generate_coach_turn(
            session=session, event=event, verify_result=verify_result
        )
        kyb_coach.append_chat(session, coach)
    _persist(session)

    if verify_result.get("pipeline_status") == "needs_documents":
        gaps_payload = verify_result.get("document_gaps") or {}
        missing = gaps_payload.get("missing_documents") or []
        missing_line = ", ".join(
            str(m.get("label") or m.get("document_type") or "document") for m in missing[:4]
        )
        md_recorder.append_step(
            session_id,
            "Awaiting additional documents",
            [
                f"- Missing: {missing_line or 'see chat'}",
                f"- Attempt #{len(session.get('verify_attempts') or [])}",
            ],
        )
        return {
            "session_id": session_id,
            "pipeline_status": "needs_documents",
            "document_gaps": gaps_payload,
            "scorecard": None,
            "credential": None,
            "credential_url": None,
            "certificate_pdf_url": None,
            "public_facts": session.get("public_facts"),
            "search_performed": verify_result.get("search_performed", False),
            "agent_trace": verify_result.get("agent_trace", []),
            "cost_analysis": verify_result.get("cost_analysis", {}),
            "record_url": f"/api/enterprise/kyb/{session_id}/record",
            "verify_attempts": session.get("verify_attempts") or [],
            "chat_messages": session.get("chat_messages") or [],
            "coach_turn": coach,
            "objective_status": session.get("objective_status"),
        }

    scorecard = verify_result["deterministic"]["scorecard"]

    search_note = (
        "Public search performed"
        if verify_result.get("search_performed")
        else "Public search skipped — documents satisfied requirements"
    )

    verification_record = save_verification_record(
        session_id=session_id,
        session=session,
        scorecard=scorecard,
        uploads=uploads,
        verify_result=verify_result,
    )

    credential = None
    x401_status = "skipped"
    x401_message = "No credential — KYB did not pass"
    if scorecard.get("kyb_status") == "passed":
        enterprise_id = verification_record.get("enterprise_id") if verification_record else None
        credential = x401_service.issue_compliance_credential(
            session_id=session_id,
            session=session,
            scorecard=scorecard,
            enterprise_id=enterprise_id,
        )
        if credential:
            pdf_bytes = x401_service.render_certificate_pdf(credential)
            credential_store.save_credential(session_id, credential, pdf_bytes)
            x401_status = "issued"
            x401_message = "Signed x401 compliance credential issued"
            md_recorder.append_step(
                session_id,
                "Stage 3 — x401 credential issued",
                [
                    f"- Credential ID: {credential.get('credential_id')}",
                    f"- Credit limit: ${credential.get('allowed_scope', {}).get('credit_limit_usd', 0):,.2f}",
                    f"- Expires: {credential.get('expiry_date')}",
                    f"- Signing key: {credential.get('signing_key_id')}",
                ],
            )
    else:
        md_recorder.append_step(
            session_id,
            "Submit complete — no credential",
            [
                f"- KYB status: {scorecard.get('kyb_status', 'unknown')}",
                f"- {search_note}",
                f"- Flags: {scorecard.get('flags_count', 0)}, Blocks: {scorecard.get('blocks_count', 0)}",
                "- x401 credential not issued (passed required)",
            ],
        )

    if x401_status == "issued":
        md_recorder.append_step(
            session_id,
            "Submit complete",
            [
                f"- KYB status: {scorecard.get('kyb_status', 'unknown')}",
                f"- {search_note}",
                f"- Confidence: {scorecard.get('confidence_score', '—')}",
            ],
        )

    return {
        "session_id": session_id,
        "enterprise_id": verification_record.get("enterprise_id") if verification_record else None,
        "verification_record": verification_record,
        "pipeline": {
            "upload": {"document_count": len(uploads), "stored_raw_documents": False},
            "verify": {"stage": "verify", "kyb_status": scorecard.get("kyb_status")},
            "x401": {
                "status": x401_status,
                "message": x401_message,
                "credential_id": credential.get("credential_id") if credential else None,
            },
        },
        "scorecard": scorecard,
        "credential": credential,
        "credential_url": f"/api/enterprise/kyb/{session_id}/credential" if credential else None,
        "certificate_pdf_url": f"/api/enterprise/kyb/{session_id}/credential.pdf" if credential else None,
        "public_facts": session.get("public_facts"),
        "search_performed": verify_result.get("search_performed", False),
        "agent_trace": verify_result.get("agent_trace", []),
        "cost_analysis": verify_result.get("cost_analysis", {}),
        "middesk": session.get("middesk"),
        "record_url": f"/api/enterprise/kyb/{session_id}/record",
        "verify_attempts": session.get("verify_attempts") or [],
        "pipeline_status": session.get("pipeline_status"),
        "chat_messages": session.get("chat_messages") or [],
        "coach_turn": coach,
        "objective_status": session.get("objective_status"),
    }


async def post_chat_message(session_id: str, message: str) -> dict:
    """User message in coach chat bar — agent responds until objective achieved."""
    if not AGENT_CHAT_ENABLED:
        return {
            "session_id": session_id,
            "chat_messages": [],
            "coach_turn": None,
            "objective_status": kyb_coach.OBJECTIVE_IN_PROGRESS,
        }
    session = _get_session(session_id)
    if message.strip():
        kyb_coach.append_chat(
            session,
            {"role": "user", "message": message.strip(), "at": _now(), "event": "user_message"},
        )
    coach = kyb_coach.generate_coach_turn(
        session=session, event="user_message", user_message=message.strip() or None
    )
    kyb_coach.append_chat(session, coach)
    session["updated_at"] = _now()
    _persist(session)
    md_recorder.append_step(
        session_id,
        "Coach chat",
        [f"- User: {message[:200]}", f"- Agent: {(coach or {}).get('message', '')[:300]}"],
    )
    return {
        "session_id": session_id,
        "chat_messages": session.get("chat_messages") or [],
        "coach_turn": coach,
        "objective_status": session.get("objective_status"),
    }


def get_session_summary(session_id: str) -> dict:
    session = _get_session(session_id)
    user = session.get("user_claims") or {}
    return {
        "session_id": session_id,
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "has_public_facts": bool(session.get("public_facts")),
        "pipeline_status": session.get("pipeline_status", "draft"),
        "document_gaps": session.get("document_gaps"),
        "verify_attempts": session.get("verify_attempts") or [],
        "documents": session.get("documents") or [],
        "doc_extractions_count": len(session.get("doc_extractions") or []),
        "user_claims": {
            "legal_name": user.get("legal_name", ""),
            "state": user.get("state", ""),
            "ein": user.get("ein", ""),
            "operating_address": user.get("operating_address", ""),
            "business_purpose": user.get("business_purpose", ""),
            "beneficial_owners": user.get("beneficial_owners") or [],
            "control_persons": user.get("control_persons") or [],
            "monthly_volume_low_usd": user.get("monthly_volume_low_usd"),
            "monthly_volume_high_usd": user.get("monthly_volume_high_usd"),
        },
        "last_scorecard_status": (session.get("last_scorecard") or {}).get("kyb_status"),
        "record_url": f"/api/enterprise/kyb/{session_id}/record",
        "chat_messages": session.get("chat_messages") or [],
        "objective_status": session.get("objective_status"),
        "coach_last_message": session.get("coach_last_message", ""),
    }


def get_credential(session_id: str) -> dict | None:
    _get_session(session_id)
    return credential_store.load_credential(session_id)


def get_certificate_pdf(session_id: str) -> bytes | None:
    _get_session(session_id)
    pdf = credential_store.load_certificate_pdf(session_id)
    if pdf:
        return pdf
    cred = credential_store.load_credential(session_id)
    if not cred:
        return None
    pdf = x401_service.render_certificate_pdf(cred)
    credential_store.save_credential(session_id, cred, pdf)
    return pdf


def get_record(session_id: str) -> str:
    _get_session(session_id)
    return md_recorder.read_session(session_id)
