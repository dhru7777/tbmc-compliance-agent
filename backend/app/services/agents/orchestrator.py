"""Orchestrator — ReAct loop: extract → think → act → observe → scorecard."""

from __future__ import annotations

import asyncio
from typing import Any

from app.services import kyb_rules
from app.services.agents import doc_extractor, gaps, public_search, research_planner
from app.services.agents.trace import AgentTrace, StepCallback
from app.services.agents.trace_labels import observe_label_for_action, short_words
from app.services.document_cross_check import cross_check_documents
from app.services.llm_usage import UsageSession

MAX_REACT_ROUNDS = 3


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
        label="start verification",
    )

    # --- Document extraction (per-file think / act / observe) ---
    doc_extractions = await doc_extractor.extract_uploads(uploads, trace, usage)
    gaps.enrich_claims_from_documents(user, doc_extractions)

    documents = [{"label": d.get("label"), "filename": d.get("filename")} for d in doc_extractions]
    session["documents"] = documents
    session["doc_extractions"] = [
        {
            "label": d.get("label"),
            "filename": d.get("filename"),
            "extracted": d.get("extracted", {}),
            "text_length": d.get("text_length", 0),
            "note": d.get("note"),
        }
        for d in doc_extractions
    ]

    claims = gaps.build_claims_summary(user, doc_extractions, documents)
    gap_list = gaps.analyze_gaps(user, doc_extractions, documents)
    public_facts = session.get("public_facts")
    last_search: dict | None = None
    search_performed = False

    # --- ReAct loop ---
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
        think_label = decision.get("think_label") or short_words(reason, 4)
        act_label = decision.get("act_label") or short_words(action.replace("_", " "), 4)

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
            break

        if action == "finish":
            break

        if action == "need_internal":
            missing = decision.get("missing_for_search") or []
            await trace.emit(
                "act",
                "public_search",
                f"Cannot search yet — need: {', '.join(missing)}",
                label=act_label,
                missing_for_search=missing,
            )
            gaps.enrich_claims_from_documents(user, doc_extractions)
            claims = gaps.build_claims_summary(user, doc_extractions, documents)
            gap_list = gaps.analyze_gaps(user, doc_extractions, documents)
            await trace.emit(
                "observe",
                "orchestrator",
                "Re-checked internal sources after search agent feedback.",
                label=observe_label_for_action("need_internal"),
                claims=claims,
            )
            continue

        if action == "public_search":
            query = decision.get("public_query") or gaps.public_search_query(user)
            ok, missing = public_search.validate_public_query(query)
            if not ok:
                await trace.emit(
                    "act",
                    "public_search",
                    f"Search blocked — missing public fields: {', '.join(missing)}",
                    label=short_words(f"blocked need {' '.join(missing)}", 4),
                    missing_for_search=missing,
                )
                continue

            safe_name = query["legal_name"]
            safe_state = query["state"]
            await trace.emit(
                "think",
                "public_search",
                f"Registry lookup for {safe_name} in {safe_state}.",
                label=short_words(f"search {safe_state} registry", 4),
            )
            await trace.emit(
                "act",
                "public_search",
                f"Searching public registry for «{safe_name}» ({safe_state}) — public fields only.",
                label=short_words(f"web search {safe_state}", 4),
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
                label=short_words(summary or pf_status or "search done", 4),
                search_performed=True,
                status=status,
                confidence=(public_facts or {}).get("confidence"),
            )

            gap_list = gaps.analyze_gaps(user, doc_extractions, documents)
            if not gaps.public_gaps_remain(gap_list) or round_num >= MAX_REACT_ROUNDS:
                break
            continue

    if not search_performed and not public_facts:
        session["public_facts"] = None

    # --- Deterministic scorecard ---
    await trace.emit(
        "think",
        "orchestrator",
        "Running deterministic scorecard rules on merged claims…",
        label="apply rule checks",
    )
    scorecard = kyb_rules.build_scorecard(session)
    doc_cross_checks = cross_check_documents(user, public_facts, doc_extractions)
    kyb_status = scorecard.get("kyb_status", "unknown")
    flags_count = scorecard.get("flags_count", 0)
    blocks_count = scorecard.get("blocks_count", 0)

    if flags_count:
        summary_label = short_words(f"{flags_count} item{'s' if flags_count != 1 else ''} need review", 4)
    elif blocks_count:
        summary_label = short_words(f"blocked {blocks_count} item{'s' if blocks_count != 1 else ''}", 4)
    elif kyb_status == "passed":
        summary_label = "admission approved"
    else:
        summary_label = short_words(f"done {kyb_status}", 4)

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
