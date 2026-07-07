"""Orchestrator — ReAct loop: extract → think → act → observe → scorecard."""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.agents import doc_extractor, document_gap_advisor, gaps, public_search, research_planner
from app.services.demo_companies import is_mock_package_document, is_trial_document_text, match_trial_company_id, trial_public_facts
from app.services.document_cross_check import cross_check_documents
from app.services.agents.trace import AgentTrace, StepCallback
from app.services.agents.trace_labels import (
    doc_extract_act_label,
    doc_extract_observe_label,
    observe_label_for_action,
    public_search_act_label,
    public_search_observe_label,
    scorecard_result_label,
    short_words,
)
from app.services.agents.third_party_trace import build_scorecard_with_third_party_trace
from app.services.llm_usage import UsageSession

MAX_REACT_ROUNDS = 3


def _missing_doc_summary(missing: list[dict]) -> str:
    if not missing:
        return "Additional documents requested."
    names = [str(m.get("label") or m.get("document_type") or "document") for m in missing[:3]]
    extra = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
    return f"Missing: {', '.join(names)}{extra}"


async def run_kyb_pipeline(
    session: dict,
    uploads: list[tuple[str, str, bytes]],
    *,
    on_step: StepCallback | None = None,
) -> dict:
    """
    Full agentic VERIFY pipeline.
    Returns verify result shape + agent_trace + search_performed flag.
    """
    trace = AgentTrace(on_step=on_step)
    usage = UsageSession()
    user = session["user_claims"]
    session_id = session.get("session_id", "")

    await trace.emit(
        "think",
        "orchestrator",
        "Starting verification — documents first, then research planner decides on public search.",
        label="Starting KYB verification pipeline now",
    )

    trial_id = session.get("trial_company_id")
    for _label, filename, content in uploads:
        text = doc_extractor.extract_text_from_upload(filename, content)
        if is_mock_package_document(text):
            trial_id = None
            session["trial_company_id"] = None
            break
        if is_trial_document_text(text):
            trial_id = match_trial_company_id(text) or trial_id
            if trial_id:
                session["trial_company_id"] = trial_id
            break

    if not trial_id:
        for _label, filename, content in uploads:
            text = doc_extractor.extract_text_from_upload(filename, content)
            if is_trial_document_text(text):
                trial_id = match_trial_company_id(text)
                if trial_id:
                    session["trial_company_id"] = trial_id
                    break

    if trial_id:
        session["public_facts"] = trial_public_facts(trial_id)
        await trace.emit(
            "observe",
            "orchestrator",
            f"Trial package «{trial_id}» — loaded registry fixture for scorecard cross-check.",
            label="Loaded trial company registry fixture data",
            trial_company_id=trial_id,
        )

    # --- Document extraction (always re-parse on verify — avoids stale session cache) ---
    doc_extractions = await doc_extractor.extract_uploads_merged(
        uploads, [], trace, usage, trial_company_id=trial_id
    )
    gaps.enrich_claims_from_documents(user, doc_extractions)
    session["user_claims"] = dict(user)

    documents = [{"label": d.get("label"), "filename": d.get("filename")} for d in doc_extractions]
    session["documents"] = documents
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

    claims = gaps.build_claims_summary(user, doc_extractions, documents)
    gap_list = gaps.analyze_gaps(user, doc_extractions, documents)

    # --- Agentic document gap review (research API) ---
    gap_advice = await asyncio.to_thread(
        document_gap_advisor.advise_document_gaps,
        claims=claims,
        gap_list=gap_list,
        documents=documents,
        extractions=doc_extractions,
        usage_session=usage,
    )
    await trace.emit(
        "think",
        "document_gap_advisor",
        gap_advice.get("think_label") or "Reviewing document package",
        label=gap_advice.get("think_label") or "Reviewing submitted document package",
        action=gap_advice.get("action"),
    )
    if gap_advice.get("action") == "request_documents":
        missing = gap_advice.get("missing_documents") or []
        await trace.emit(
            "observe",
            "document_gap_advisor",
            _missing_doc_summary(missing),
            label=gap_advice.get("act_label") or "Request additional KYB documents",
            pipeline_status="needs_documents",
            missing_documents=missing,
        )
        session["pipeline_status"] = "needs_documents"
        session["document_gaps"] = gap_advice
        usage.print_run_summary(session_id=session_id)
        return {
            "stage": "needs_documents",
            "pipeline_status": "needs_documents",
            "search_performed": False,
            "document_gaps": gap_advice,
            "agent_trace": [s.to_dict() for s in trace.steps],
            "cost_analysis": usage.to_dict(),
            "ai": {
                "public_presence": {
                    "legal_name": user.get("legal_name") or None,
                    "state": user.get("state") or None,
                    "public_facts": session.get("public_facts"),
                    "search_performed": False,
                },
                "documents": {
                    "count": len(doc_extractions),
                    "extractions": session["doc_extractions"],
                },
            },
            "deterministic": {
                "scorecard": None,
                "document_cross_checks": [],
            },
        }

    await trace.emit(
        "observe",
        "document_gap_advisor",
        "Document package sufficient — continuing verification.",
        label=gap_advice.get("act_label") or "Proceed to verification scorecard",
    )
    session["pipeline_status"] = "verifying"

    public_facts = session.get("public_facts")
    last_search: dict | None = None
    search_performed = False

    # Public lookup: trial packages use registry fixture; real names use bounded web search.
    if user.get("legal_name") and user.get("state"):
        safe_name = str(user["legal_name"]).strip()
        safe_state = str(user["state"]).strip().upper()[:2]
        await trace.emit(
            "act",
            "public_search",
            f"Public verification for «{safe_name}» ({safe_state}) — registry check.",
            label=public_search_act_label(safe_name, safe_state),
            public_query={"legal_name": safe_name, "state": safe_state},
        )
        if trial_id:
            last_search = await public_search.run_trial_registry_search(
                trial_id, safe_name, safe_state, usage
            )
        else:
            last_search = await public_search.run_bounded_search(safe_name, safe_state, usage)
        public_facts = last_search.get("public_facts")
        session["public_facts"] = public_facts
        search_performed = True
        summary = last_search.get("summary", "")
        await trace.emit(
            "observe",
            "public_search",
            summary or "Public verification complete.",
            label=public_search_observe_label(
                publicly_verified=(public_facts or {}).get("publicly_verified"),
                summary=summary,
            ),
            search_performed=True,
            confidence=(public_facts or {}).get("confidence"),
            publicly_verified=(public_facts or {}).get("publicly_verified"),
        )
        gap_list = gaps.analyze_gaps(user, doc_extractions, documents)

    # --- ReAct loop (planner may request follow-up search if gaps remain) ---
    need_internal_rounds = 0
    for round_num in range(1, MAX_REACT_ROUNDS + 1):
        decision = await asyncio.to_thread(
            research_planner.plan_next_action,
            claims=claims,
            gap_list=gap_list,
            public_facts=public_facts,
            last_search_result=last_search,
            round_num=round_num,
            usage_session=usage,
        )
        action = decision.get("action", "finish")
        reason = decision.get("reason", "")
        think_label = decision.get("think_label") or short_words(reason, 6)
        act_label = decision.get("act_label") or short_words(action.replace("_", " "), 6)
        if action == "skip_search":
            think_label = "Documents already cover public verification needs"
        elif action == "public_search":
            think_label = "Public registry search is still required"
        elif action == "finish":
            think_label = "No further public research steps needed"

        await trace.emit(
            "think",
            "research_planner",
            reason or f"Evaluating round {round_num}…",
            label=think_label,
            action=action,
            round=round_num,
            missing_for_search=decision.get("missing_for_search") or [],
        )

        if action == "skip_search":
            await trace.emit(
                "observe",
                "research_planner",
                reason or "Documents and form cover public verification.",
                label=observe_label_for_action("skip_search"),
            )
            break

        if action == "finish":
            break

        if action == "need_internal":
            missing = decision.get("missing_for_search") or []
            gaps.enrich_claims_from_documents(user, doc_extractions)
            claims = gaps.build_claims_summary(user, doc_extractions, documents)
            gap_list = gaps.analyze_gaps(user, doc_extractions, documents)
            need_internal_rounds += 1
            if need_internal_rounds >= 2 or not missing:
                await trace.emit(
                    "observe",
                    "orchestrator",
                    "Continuing without public registry search.",
                    label="Proceeding with document-based verification",
                )
                break
            await trace.emit(
                "act",
                "public_search",
                f"Cannot search yet — need: {', '.join(missing)}",
                label=act_label,
                missing_for_search=missing,
            )
            await trace.emit(
                "observe",
                "orchestrator",
                "Re-checked internal sources after search agent feedback.",
                label=observe_label_for_action("need_internal"),
                claims=claims,
            )
            continue

        if action == "public_search":
            if trial_id:
                break
            query = decision.get("public_query") or gaps.public_search_query(user)
            ok, missing = public_search.validate_public_query(query)
            if not ok:
                await trace.emit(
                    "act",
                    "public_search",
                    f"Search blocked — missing public fields: {', '.join(missing)}",
                    label=short_words(f"Search blocked missing {' '.join(missing)}"),
                    missing_for_search=missing,
                )
                continue

            safe_name = query["legal_name"]
            safe_state = query["state"]
            await trace.emit(
                "think",
                "public_search",
                f"Registry lookup for {safe_name} in {safe_state}.",
                label=public_search_act_label(safe_name, safe_state),
            )
            await trace.emit(
                "act",
                "public_search",
                f"Searching public registry for «{safe_name}» ({safe_state}) — public fields only.",
                label=public_search_act_label(safe_name, safe_state),
                public_query=query,
            )

            last_search = await public_search.run_bounded_search(safe_name, safe_state, usage)
            public_facts = last_search.get("public_facts")
            session["public_facts"] = public_facts
            search_performed = True

            status = last_search.get("status", "completed")
            summary = last_search.get("summary", "")
            pf_status = (public_facts or {}).get("status", "unknown")
            observe_msg = f"Registry result: {pf_status} — {summary}"
            await trace.emit(
                "observe",
                "public_search",
                observe_msg,
                label=public_search_observe_label(
                    publicly_verified=(public_facts or {}).get("publicly_verified"),
                    summary=summary,
                ),
                search_performed=True,
                status=status,
                confidence=(public_facts or {}).get("confidence"),
            )

            gap_list = gaps.analyze_gaps(user, doc_extractions, documents)
            if not gaps.public_gaps_remain(gap_list) or round_num >= MAX_REACT_ROUNDS:
                break
            continue
    if not search_performed:
        public_facts = session.get("public_facts")
        if not public_facts or not public_facts.get("trial_company_id"):
            session["public_facts"] = None

    # --- Deterministic scorecard (mock Middesk + Persona trace) ---
    scorecard = await build_scorecard_with_third_party_trace(trace, session, usage)
    doc_cross_checks = cross_check_documents(user, public_facts, doc_extractions)
    kyb_status = scorecard.get("kyb_status", "unknown")
    flags_count = scorecard.get("flags_count", 0)
    blocks_count = scorecard.get("blocks_count", 0)

    summary_label = scorecard_result_label(
        kyb_status=kyb_status,
        flags_count=flags_count,
        blocks_count=blocks_count,
    )

    await trace.emit(
        "observe",
        "orchestrator",
        f"Verification complete — status: {kyb_status}.",
        label=summary_label,
        search_performed=search_performed,
        kyb_status=kyb_status,
        flags_count=flags_count,
        blocks_count=blocks_count,
    )

    session["pipeline_status"] = "complete" if kyb_status == "passed" else "review"
    session["last_scorecard"] = scorecard

    usage.print_run_summary(session_id=session_id)

    ai_public = {
        "legal_name": user.get("legal_name") or None,
        "state": user.get("state") or None,
        "public_facts": public_facts,
        "search_method": (public_facts or {}).get("search_method") if public_facts else None,
        "confidence": (public_facts or {}).get("confidence") if public_facts else None,
        "search_performed": search_performed,
    }

    return {
        "stage": "verify",
        "pipeline_status": session.get("pipeline_status", "review"),
        "search_performed": search_performed,
        "agent_trace": [s.to_dict() for s in trace.steps],
        "cost_analysis": usage.to_dict(),
        "ai": {
            "public_presence": ai_public,
            "documents": {
                "count": len(doc_extractions),
                "extractions": session["doc_extractions"],
            },
        },
        "deterministic": {
            "scorecard": scorecard,
            "document_cross_checks": doc_cross_checks,
        },
    }
