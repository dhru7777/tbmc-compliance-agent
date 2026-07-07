"""Agent 3 — bounded public search (public-safe fields only, tight token budget)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from app.services import api_cache, llm_search
from app.services.agents import llm_client
from app.services.llm_usage import UsageSession, usage_from_message

USE_ANTHROPIC_WEB_SEARCH = os.getenv("USE_ANTHROPIC_WEB_SEARCH", "true").lower() in ("1", "true", "yes")
BOUNDED_WEB_SEARCH_MAX_USES = int(os.getenv("KYB_BOUNDED_WEB_SEARCH_MAX_USES", "1"))

# ~1–1.5k input budget for snippet fallback path (Tavily/DuckDuckGo).
_SNIPPET_CHAR_BUDGET = 700


def _parse_yes_no_json(text: str) -> dict[str, Any]:
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


def _anthropic_web_verify_prompt(legal_name: str, state: str) -> str:
    body = f"""Search public US business registries (Secretary of State, OpenCorporates, SEC) for:

Company: {legal_name}
State: {state}

After searching, answer ONLY with JSON (no markdown). Keep rationale under 20 words.
{{
  "publicly_verified": true or false,
  "name_match": true or false,
  "active_status": true or false,
  "rationale": "short reason"
}}

Rules:
- publicly_verified=true only if you find a registry match for this name in this state.
- active_status=true only if records show active, good standing, or in existence.
- If no reliable match, set all booleans false."""
    return llm_client.truncate_for_budget(body, llm_client.SEARCH_INPUT_BUDGET)


async def _anthropic_web_verify(
    legal_name: str,
    state: str,
    api_key: str,
    usage_session: UsageSession | None = None,
) -> dict[str, Any] | None:
    """Anthropic built-in web search + compact yes/no JSON (research API key)."""
    if not api_key or not USE_ANTHROPIC_WEB_SEARCH:
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _anthropic_web_verify_prompt(legal_name, state)

    def _call() -> Any:
        return client.messages.create(
            model=llm_client.DEFAULT_MODEL,
            max_tokens=llm_client.SEARCH_OUTPUT_MAX,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": BOUNDED_WEB_SEARCH_MAX_USES,
                    "allowed_callers": ["direct"],
                }
            ],
        )

    try:
        message = await asyncio.to_thread(_call)
        if usage_session is not None:
            record = usage_from_message(
                message,
                operation="Public verification (Anthropic web search)",
                model=llm_client.DEFAULT_MODEL,
            )
            record.agent = "public_search"
            usage_session.add(record)

        text = llm_search._text_blocks(message)
        if not text:
            return None
        raw = _parse_yes_no_json(text)
        urls = llm_search._urls_from_message(message)
        facts = _facts_from_verification(
            legal_name,
            state,
            raw,
            source_urls=urls,
            search_method="anthropic_web_search",
        )
        return {"facts": facts, "raw": raw}
    except anthropic.AuthenticationError:
        return None
    except anthropic.BadRequestError as exc:
        if "web search" in str(exc).lower():
            return None
        raise
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    except Exception:
        return None


def _truncate_snippets(results: list[dict], max_chars: int = _SNIPPET_CHAR_BUDGET) -> str:
    parts: list[str] = []
    used = 0
    for i, r in enumerate(results[:2], 1):
        snippet = llm_client.truncate_for_budget(r.get("snippet", ""), 280)
        line = f"[{i}] {r.get('title', '')}\n{snippet}\n{r.get('url', '')}"
        if used + len(line) > max_chars:
            break
        parts.append(line)
        used += len(line)
    return "\n\n".join(parts) if parts else "No search results."


def _facts_from_verification(
    legal_name: str,
    state: str,
    data: dict[str, Any],
    *,
    source_urls: list[str] | None = None,
    search_method: str = "agent_bounded_search",
) -> dict[str, Any]:
    """Map compact yes/no verification JSON to scorecard-compatible public_facts."""
    verified = bool(data.get("publicly_verified"))
    name_match = data.get("name_match")
    if name_match is None:
        name_match = verified
    active = data.get("active_status")
    if active is None:
        active = verified
    rationale = str(data.get("rationale") or "").strip() or (
        "Public registry corroboration found" if verified else "No reliable public registry match"
    )

    if verified and name_match:
        status = "active" if active else "unknown"
        confidence = 0.88 if active else 0.72
        formation_verified = bool(active)
        formation_detail = rationale
    else:
        status = "unknown"
        confidence = 0.35
        formation_verified = False
        formation_detail = rationale

    return llm_search._normalize_facts(
        {
            "legal_name": legal_name if name_match else data.get("legal_name") or legal_name,
            "status": status,
            "entity_type": data.get("entity_type"),
            "formation_date": None,
            "registered_agent_address": data.get("registered_agent_address"),
            "naics_or_purpose": data.get("naics_or_purpose"),
            "incorporation_state": state or data.get("incorporation_state"),
            "formation_verified": formation_verified,
            "formation_detail": formation_detail,
            "source_urls": source_urls or data.get("source_urls") or [],
            "confidence": confidence,
            "rationale": rationale,
            "search_method": search_method,
            "publicly_verified": verified,
        }
    )


def _verification_prompt(legal_name: str, state: str, context: str) -> str:
    body = f"""You verify whether a US business exists in PUBLIC records (Secretary of State, OpenCorporates, SEC).

Company: {legal_name}
State: {state}

Search snippets:
{context}

Answer ONLY with JSON (no markdown). Keep rationale under 20 words.
{{
  "publicly_verified": true or false,
  "name_match": true or false,
  "active_status": true or false,
  "rationale": "short reason"
}}

Rules:
- publicly_verified=true only if snippets support a real registry match for this name in this state.
- active_status=true only if snippets mention active, good standing, or in existence.
- If snippets are empty or inconclusive, all booleans false."""
    return llm_client.truncate_for_budget(body, llm_client.SEARCH_INPUT_BUDGET)


async def run_trial_registry_search(
    trial_company_id: str,
    legal_name: str,
    state: str,
    usage_session: UsageSession | None = None,
) -> dict:
    """Use trial package registry fixture instead of live web search (demo entities are fictional)."""
    from app.services.demo_companies import get_demo_company, trial_public_facts

    if usage_session:
        usage_session.add_skip(
            "Public trial registry lookup",
            agent="public_search",
            note="trial package fixture — no web search",
        )

    company = get_demo_company(trial_company_id)
    facts = dict(trial_public_facts(trial_company_id))
    facts["legal_name"] = legal_name or facts.get("legal_name")
    facts["incorporation_state"] = state.strip().upper()[:2] if state else facts.get("incorporation_state")
    facts["publicly_verified"] = True
    facts["search_method"] = "trial_package_fixture"

    if company.get("complete"):
        summary = "Trial registry entity verified in good standing."
    else:
        summary = "Trial registry matched formation record on file."

    return {
        "status": "completed",
        "public_facts": facts,
        "summary": summary,
    }


async def run_bounded_search(
    legal_name: str,
    state: str,
    usage_session: UsageSession | None = None,
) -> dict:
    """
    Public registry yes/no check.
    Primary: Anthropic web search tool (research API key, no DuckDuckGo/Tavily needed).
    Fallback: Tavily or DuckDuckGo snippets + LLM synthesis if web search unavailable.
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
        facts["publicly_verified"] = facts.get("confidence", 0) >= 0.7
        summary = "Yes — publicly verified (demo fixture)" if facts["publicly_verified"] else facts.get("rationale", "")
        out = {"status": "completed", "public_facts": facts, "summary": summary}
        api_cache.set("agent_public_search", out, name_key, state_code)
        return out

    api_key = llm_client.research_api_key()

    # 1) Anthropic built-in web search (research key) — preferred path
    web_result = await _anthropic_web_verify(legal_name, state_code, api_key, usage_session)
    if web_result:
        facts = web_result["facts"]
        verified = bool(facts.get("publicly_verified"))
        summary = (
            f"Yes — publicly verified. {facts.get('rationale', '')}"
            if verified
            else f"No — not publicly verified. {facts.get('rationale', '')}"
        )
        out = {"status": "completed", "public_facts": facts, "summary": summary.strip()}
        api_cache.set("agent_public_search", out, name_key, state_code)
        return out

    # 2) Fallback: Tavily / DuckDuckGo snippets + yes/no synthesis (no extra search API required)
    if usage_session:
        usage_session.add_skip(
            "Anthropic web search",
            agent="public_search",
            note="unavailable — falling back to snippet search",
        )

    query = f'"{legal_name}" {state_code} secretary of state business entity status'.strip()
    results = await llm_search.web_search(query, max_results=2)
    context = _truncate_snippets(results)

    if not api_key:
        if usage_session:
            usage_session.add_skip("Public search synthesis", agent="public_search", note="no API key")
        facts = llm_search._fallback_extract(legal_name, state_code, context, "ANTHROPIC API key missing")
        return {
            "status": "completed",
            "public_facts": facts,
            "summary": facts.get("formation_detail", "No API key"),
        }

    prompt = _verification_prompt(legal_name, state_code, context)

    try:
        raw = await asyncio.to_thread(
            llm_client.call_json,
            api_key=api_key,
            prompt=prompt,
            max_tokens=llm_client.SEARCH_OUTPUT_MAX,
            operation="Public verification (snippet fallback)",
            agent="public_search",
            usage_session=usage_session,
        )
        urls = [r.get("url") for r in results if r.get("url")][:3]
        facts = _facts_from_verification(
            legal_name,
            state_code,
            raw,
            source_urls=urls,
            search_method="snippet_fallback_search",
        )
        verified = bool(facts.get("publicly_verified"))
        summary = (
            f"Yes — publicly verified. {facts.get('rationale', '')}"
            if verified
            else f"No — not publicly verified. {facts.get('rationale', '')}"
        )
        out = {
            "status": "completed",
            "public_facts": facts,
            "summary": summary.strip(),
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
