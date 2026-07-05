"""Agent 2 — research planner: ReAct THINK steps, decides whether/how to search."""

from __future__ import annotations

from app.services.agents import gaps, llm_client
from app.services.agents.trace_labels import act_label_for_action, short_words
from app.services.llm_usage import UsageSession


def _with_labels(decision: dict) -> dict:
    action = decision.get("action", "finish")
    reason = decision.get("reason") or ""
    decision.setdefault("think_label", short_words(decision.get("think_label") or reason, 4))
    decision.setdefault("act_label", short_words(decision.get("act_label") or act_label_for_action(action), 4))
    return decision


def _deterministic_decision(
    claims: dict,
    gap_list: list[dict],
    public_facts: dict | None,
    round_num: int,
) -> dict:
    """Fallback when research API key missing or LLM fails."""
    skip, reason = gaps.can_skip_public_search(gap_list)
    if skip:
        return _with_labels(
            {
                "action": "skip_search",
                "reason": reason,
                "public_query": None,
                "missing_for_search": [],
            }
        )
    if public_facts and public_facts.get("legal_name"):
        reason = "Public facts already retrieved this session."
        return _with_labels(
            {
                "action": "finish",
                "reason": reason,
                "public_query": None,
                "missing_for_search": [],
            }
        )
    query = gaps.public_search_query({"legal_name": claims.get("legal_name"), "state": claims.get("state")})
    if not query.get("legal_name"):
        reason = "Cannot search without a legal business name."
        return _with_labels(
            {
                "action": "skip_search",
                "reason": reason,
                "public_query": None,
                "missing_for_search": ["legal_name"],
            }
        )
    if not query.get("state"):
        reason = "State required before public registry search."
        return _with_labels(
            {
                "action": "need_internal",
                "reason": reason,
                "public_query": None,
                "missing_for_search": ["state"],
            }
        )
    reason = "Public gaps remain for registry-sourced fields."
    return _with_labels(
        {
            "action": "public_search",
            "reason": reason,
            "public_query": query,
            "missing_for_search": [],
        }
    )


def plan_next_action(
    *,
    claims: dict,
    gap_list: list[dict],
    public_facts: dict | None,
    last_search_result: dict | None,
    round_num: int,
    usage_session: UsageSession | None = None,
) -> dict:
    """
    THINK step — returns:
      action: skip_search | public_search | need_internal | finish
      reason, think_label, act_label, public_query, missing_for_search
    """
    api_key = llm_client.research_api_key()
    if not api_key:
        if usage_session:
            usage_session.add_skip(
                f"Research planner (round {round_num})",
                agent="research_planner",
                note="deterministic rules — no research API key",
            )
        return _deterministic_decision(claims, gap_list, public_facts, round_num)

    public_gap_text = "\n".join(
        f"- {g['field']}: {g['reason']}" for g in gaps.public_gaps_remain(gap_list)
    ) or "None"
    private_gap_text = "\n".join(
        f"- {g['field']}: {g['reason']}" for g in gap_list if not g.get("public_searchable")
    ) or "None"

    prev = ""
    if last_search_result:
        prev = f"Last search status: {last_search_result.get('status')} — {last_search_result.get('summary', '')}"

    prompt = f"""You are the KYB research planner. Decide the NEXT action after reviewing internal claims.
Round: {round_num}

INTERNAL CLAIMS SUMMARY (do NOT send private fields to web search):
{claims}

PUBLIC GAPS (may be filled via registry/web):
{public_gap_text}

PRIVATE GAPS (never web search — user/docs only):
{private_gap_text}

{prev}

RULES:
- action "skip_search" if documents+form already satisfy public verification (name, state, active status in SOS doc).
- action "public_search" ONLY if a public gap exists AND you have legal_name + state for a registry lookup.
- action "need_internal" if search would help but legal_name or state is missing — list missing_for_search.
- action "finish" if public_facts already sufficient or no further public steps help.
- public_query may ONLY contain legal_name and state (2-letter). NEVER include EIN, addresses, owner names.
- think_label and act_label MUST be your own 2-4 word phrases (not copied from rules).

Return JSON only:
{{
  "action": "skip_search|public_search|need_internal|finish",
  "reason": "one sentence for audit log",
  "think_label": "2-4 words: why you chose this",
  "act_label": "2-4 words: what happens next",
  "public_query": {{"legal_name": "...", "state": "DE"}} or null,
  "missing_for_search": []
}}"""

    try:
        decision = llm_client.call_json(
            api_key=api_key,
            prompt=prompt,
            max_tokens=llm_client.PLANNER_OUTPUT_MAX,
            operation=f"Research planner (round {round_num})",
            agent="research_planner",
            usage_session=usage_session,
        )
        action = decision.get("action", "finish")
        if action not in ("skip_search", "public_search", "need_internal", "finish"):
            action = "finish"
        decision["action"] = action
        pq = decision.get("public_query")
        if isinstance(pq, dict):
            decision["public_query"] = {
                "legal_name": str(pq.get("legal_name", "")).strip(),
                "state": str(pq.get("state", "")).strip().upper()[:2],
            }
        return _with_labels(decision)
    except Exception:
        if usage_session:
            usage_session.add_skip(
                f"Research planner (round {round_num})",
                agent="research_planner",
                note="LLM failed — deterministic fallback",
            )
        return _deterministic_decision(claims, gap_list, public_facts, round_num)
