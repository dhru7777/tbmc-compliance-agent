"""Agent 3 — bounded public search (public-safe fields only, token budget)."""

from __future__ import annotations

import asyncio

from app.services import api_cache, llm_search
from app.services.agents import llm_client
from app.services.llm_usage import UsageSession


def _truncate_snippets(results: list[dict], max_chars: int = 900) -> str:
    parts: list[str] = []
    used = 0
    for i, r in enumerate(results[:2], 1):
        snippet = llm_client.truncate_for_budget(r.get("snippet", ""), 400)
        line = f"[{i}] {r.get('title', '')}\n{snippet}\n{r.get('url', '')}"
        if used + len(line) > max_chars:
            break
        parts.append(line)
        used += len(line)
    return "\n\n".join(parts) if parts else "No search results."


async def run_bounded_search(
    legal_name: str,
    state: str,
    usage_session: UsageSession | None = None,
) -> dict:
    """
    Public registry lookup using lightweight web snippets + small LLM synthesis.
    Does NOT use Anthropic native web search (avoids ~20k token injection).
    """
    name_key = legal_name.strip().lower()
    state_code = state.strip().upper()
    cached = api_cache.get("agent_public_search", name_key, state_code)
    if cached is not None:
        if usage_session:
            usage_session.add_cache("Public bounded search", agent="public_search")
        return {**api_cache.mark_cached(cached), "status": "completed"}

    # Demo fixtures for known entities (no API spend)
    fixture = llm_search._match_mock_entity(legal_name, state_code)
    if fixture:
        if usage_session:
            usage_session.add_skip(
                "Public bounded search",
                agent="public_search",
                note="demo fixture — no LLM",
            )
        facts = llm_search._fallback_extract(legal_name, state_code, "", None)
        facts["search_method"] = "demo_fixture"
        out = {"status": "completed", "public_facts": facts, "summary": facts.get("rationale", "Demo fixture")}
        api_cache.set("agent_public_search", out, name_key, state_code)
        return out

    query = f'"{legal_name}" {state_code} secretary of state business entity status'.strip()
    results = await llm_search.web_search(query, max_results=2)
    context = _truncate_snippets(results)

    api_key = llm_client.research_api_key()
    if not api_key:
        if usage_session:
            usage_session.add_skip("Public search synthesis", agent="public_search", note="no API key")
        facts = llm_search._fallback_extract(legal_name, state_code, context, "ANTHROPIC API key missing")
        return {
            "status": "completed",
            "public_facts": facts,
            "summary": facts.get("formation_detail", "No API key"),
        }

    prompt = llm_client.truncate_for_budget(
        f"""Extract PUBLIC business registry facts from search snippets only.
Company: {legal_name}
State: {state_code}

Snippets:
{context}

Return JSON only:
{{"legal_name","status","entity_type","formation_date","registered_agent_address","incorporation_state","formation_verified","formation_detail","source_urls","confidence","rationale"}}""",
        llm_client.SEARCH_INPUT_BUDGET,
    )

    try:
        facts = await asyncio.to_thread(
            llm_client.call_json,
            api_key=api_key,
            prompt=prompt,
            max_tokens=llm_client.SEARCH_OUTPUT_MAX,
            operation="Public search synthesis",
            agent="public_search",
            usage_session=usage_session,
        )
        facts = llm_search._normalize_facts(facts)
        facts["search_method"] = "agent_bounded_search"
        if results and not facts.get("source_urls"):
            facts["source_urls"] = [r.get("url") for r in results if r.get("url")][:3]
        out = {
            "status": "completed",
            "public_facts": facts,
            "summary": facts.get("rationale") or facts.get("formation_detail") or "Search complete",
        }
        api_cache.set("agent_public_search", out, name_key, state_code)
        return out
    except Exception as exc:
        facts = llm_search._fallback_extract(legal_name, state_code, context, str(exc))
        return {"status": "error", "public_facts": facts, "summary": str(exc)}


def validate_public_query(query: dict | None) -> tuple[bool, list[str]]:
    """Search agent gate — only legal_name + state allowed."""
    if not query:
        return False, ["legal_name", "state"]
    missing = []
    if not str(query.get("legal_name", "")).strip():
        missing.append("legal_name")
    if not str(query.get("state", "")).strip():
        missing.append("state")
    return len(missing) == 0, missing
