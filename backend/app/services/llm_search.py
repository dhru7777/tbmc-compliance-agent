"""LLM-assisted KYB research: Anthropic web search (primary) → fallback search → extract."""

import json
import os
import re
from typing import Any

import httpx

from app.services import api_cache

CONFIDENCE_AUTO = float(os.getenv("KYB_IDENTITY_CONFIDENCE", "0.8"))
USE_ANTHROPIC_WEB_SEARCH = os.getenv("USE_ANTHROPIC_WEB_SEARCH", "true").lower() in ("1", "true", "yes")
WEB_SEARCH_MAX_USES = int(os.getenv("KYB_WEB_SEARCH_MAX_USES", "2"))

_auth_error_cache: str | None | bool = False  # False = unchecked; str = error; None = ok


def _anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def _check_anthropic_auth() -> str | None:
    """Return error message if API key is missing (no live ping — avoids extra API spend)."""
    global _auth_error_cache
    if _auth_error_cache is not False:
        return _auth_error_cache  # type: ignore[return-value]

    api_key = _anthropic_key()
    if not api_key:
        _auth_error_cache = "ANTHROPIC_API_KEY is not set in backend/.env"
        return _auth_error_cache
    _auth_error_cache = None
    return None


def _tavily_key() -> str:
    return os.getenv("TAVILY_API_KEY", "").strip()


def _parse_json_from_text(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM text (handles markdown fences and prose)."""
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        raw = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise json.JSONDecodeError("No JSON object found", text, 0)
        raw = text[start : end + 1]
    data = json.loads(raw.strip())
    return _normalize_facts(data)


def _normalize_confidence(val: Any) -> float:
    if isinstance(val, (int, float)):
        return max(0.0, min(1.0, float(val)))
    if isinstance(val, str):
        lower = val.lower().strip()
        mapping = {"high": 0.9, "very high": 0.95, "medium": 0.6, "moderate": 0.6, "low": 0.3}
        if lower in mapping:
            return mapping[lower]
        try:
            return max(0.0, min(1.0, float(lower)))
        except ValueError:
            pass
    return 0.5


def _normalize_facts(data: dict[str, Any]) -> dict[str, Any]:
    if "confidence" in data:
        data["confidence"] = _normalize_confidence(data["confidence"])
    status = data.get("status")
    if isinstance(status, str):
        lower = status.lower()
        if lower == "active":
            data["status"] = "active"
        elif lower in ("inactive", "dissolved", "suspended"):
            data["status"] = lower
    if data.get("formation_verified") is None and data.get("status") == "active":
        data["formation_verified"] = True
    return data


def _text_blocks(message) -> str:
    return "\n".join(block.text for block in message.content if block.type == "text")


def _urls_from_message(message) -> list[str]:
    urls: list[str] = []
    for block in message.content:
        if block.type == "text":
            urls.extend(re.findall(r"https?://[^\s\])\"']+", block.text))
        # Citation / search result blocks (SDK shape varies by version)
        citations = getattr(block, "citations", None) or []
        for cite in citations:
            url = getattr(cite, "url", None) or (cite.get("url") if isinstance(cite, dict) else None)
            if url:
                urls.append(url)
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique[:10]


async def _anthropic_web_search_kyb(legal_name: str, state: str = "") -> dict[str, Any] | None:
    """Use Anthropic's built-in web search tool (Brave-backed). Requires API web search enabled."""
    api_key = _anthropic_key()
    if not api_key or not USE_ANTHROPIC_WEB_SEARCH:
        return None

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    state_hint = (
        f"State of incorporation/registration: {state}"
        if state
        else "State of incorporation: unknown — search nationally and infer from official registries (OpenCorporates, SEC EDGAR, Secretary of State portals)."
    )
    prompt = f"""You are a KYB research agent. Use web search to find PUBLIC business registration facts for this company.

Company: {legal_name}
{state_hint}

Search Secretary of State business registries, OpenCorporates, SEC filings, or official state portals.
Extract ONLY facts you can support from search results. Do NOT invent filing numbers or addresses.

Return ONLY valid JSON (no markdown):
{{
  "legal_name": "string or null",
  "status": "active|dissolved|unknown|null",
  "entity_type": "LLC|Corporation|etc or null",
  "formation_date": "YYYY-MM-DD or null",
  "registered_agent_address": "string or null",
  "naics_or_purpose": "string or null",
  "incorporation_state": "2-letter US state code or null",
  "formation_verified": true/false,
  "formation_detail": "short explanation",
  "source_urls": ["url1"],
  "confidence": 0.0,
  "rationale": "one sentence"
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": WEB_SEARCH_MAX_USES,
                    "allowed_callers": ["direct"],
                }
            ],
        )
        text = _text_blocks(message)
        if not text:
            return None
        facts = _parse_json_from_text(text)
        found_urls = _urls_from_message(message)
        if found_urls and not facts.get("source_urls"):
            facts["source_urls"] = found_urls
        facts["search_method"] = "anthropic_web_search"
        return facts
    except anthropic.AuthenticationError:
        return None
    except anthropic.BadRequestError as exc:
        # Web search disabled on org, or tool config issue — fall back silently
        if "web search" in str(exc).lower():
            return None
        raise
    except (json.JSONDecodeError, IndexError, KeyError):
        return None
    except Exception:
        return None


async def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    api_key = _tavily_key()
    if not api_key:
        return []
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results, "include_answer": False},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"title": r.get("title", ""), "snippet": r.get("content", ""), "url": r.get("url", "")}
            for r in data.get("results", [])
        ]


def _duckduckgo_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [
                {"title": r.get("title", ""), "snippet": r.get("body", ""), "url": r.get("href", "")}
                for r in results
            ]
    except Exception:
        return []


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    cached = api_cache.get("web_search", query.strip().lower(), str(max_results))
    if cached is not None:
        return cached
    results = await _tavily_search(query, max_results)
    if not results:
        results = _duckduckgo_search(query, max_results)
    if results:
        api_cache.set("web_search", results, query.strip().lower(), str(max_results))
    return results


def _format_search_context(results: list[dict]) -> str:
    if not results:
        return "No search results returned."
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r.get('title', '')}\nURL: {r.get('url', '')}\n{r.get('snippet', '')}")
    return "\n\n".join(parts)


async def _extract_with_anthropic(
    legal_name: str, state: str, search_context: str, api_error: str | None = None
) -> dict[str, Any]:
    ctx_hash = api_cache.content_hash(search_context)
    cached = api_cache.get("fallback_extract", legal_name.strip().lower(), state.strip().upper(), ctx_hash)
    if cached is not None:
        return api_cache.mark_cached(cached)

    api_key = _anthropic_key()
    if not api_key or api_error:
        return _fallback_extract(legal_name, state, search_context, api_error)

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    state_hint = f"State: {state}" if state else "State: unknown — use search results to infer incorporation state if possible."
    prompt = f"""Extract ONLY facts supported by the search results below.
Do NOT invent filing numbers or addresses. If uncertain, lower confidence and leave fields null.

Company: {legal_name}
{state_hint}

Search results:
{search_context}

Return valid JSON only with schema:
{{"legal_name","status","entity_type","formation_date","registered_agent_address","naics_or_purpose","incorporation_state","formation_verified","formation_detail","source_urls","confidence","rationale"}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        facts = _parse_json_from_text(_text_blocks(message))
        facts["search_method"] = "fallback_extract"
        api_cache.set("fallback_extract", facts, legal_name.strip().lower(), state.strip().upper(), ctx_hash)
        return facts
    except Exception:
        return _fallback_extract(legal_name, state, search_context, api_error)


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


MOCK_ENTITIES = [
    {
        "_key": "acme trading llc|DE",
        "_patterns": ["acmetrading"],
        "_state": "DE",
        "legal_name": "Acme Trading LLC",
        "status": "active",
        "entity_type": "LLC",
        "formation_date": "2019-03-15",
        "registered_agent_address": "1209 Orange St, Wilmington, DE 19801",
        "naics_or_purpose": "Commodity contracts dealing",
        "formation_verified": True,
        "formation_detail": "Delaware LLC in good standing",
        "source_urls": ["https://icis.corp.delaware.gov/"],
    },
    {
        "_key": "stripe inc|DE",
        "_patterns": ["stripeinc", "stripe"],
        "_state": "DE",
        "legal_name": "Stripe, Inc.",
        "status": "active",
        "entity_type": "Corporation",
        "formation_date": "2010-01-21",
        "registered_agent_address": "1209 Orange St, Wilmington, DE 19801",
        "naics_or_purpose": "Payment processing and financial software",
        "formation_verified": True,
        "formation_detail": "Delaware corporation in good standing",
        "source_urls": ["https://icis.corp.delaware.gov/"],
    },
    {
        "_key": "the better money company|DE",
        "_patterns": ["thebettermoneycompany", "bettermoneycompany", "bettermoney", "tbmc"],
        "_state": "DE",
        "legal_name": "The Better Money Company, Inc.",
        "status": "active",
        "entity_type": "Corporation",
        "formation_date": "2024-06-01",
        "registered_agent_address": "1209 Orange St, Wilmington, DE 19801",
        "naics_or_purpose": "Stablecoin clearinghouse and financial infrastructure",
        "formation_verified": True,
        "formation_detail": "Delaware corporation — stablecoin clearinghouse (demo fixture)",
        "source_urls": ["https://bettermoney.com/", "https://icis.corp.delaware.gov/"],
    },
    {
        "_key": "target corporation|MN",
        "_patterns": ["targetcorporation"],
        "_state": "MN",
        "legal_name": "Target Corporation",
        "status": "active",
        "entity_type": "Corporation",
        "formation_date": "1902-01-01",
        "registered_agent_address": "1000 Nicollet Mall, Minneapolis, MN 55403",
        "naics_or_purpose": "General merchandise retail",
        "formation_verified": True,
        "formation_detail": "Minnesota corporation in good standing",
        "source_urls": ["https://mblsportal.sos.mn.gov/", "https://corporate.target.com/"],
    },
    {
        "_key": "walmart inc|DE",
        "_patterns": ["walmartinc", "walmart"],
        "_state": "DE",
        "legal_name": "Walmart Inc.",
        "status": "active",
        "entity_type": "Corporation",
        "formation_date": "1969-10-31",
        "registered_agent_address": "1209 Orange Street, Wilmington, DE 19801",
        "naics_or_purpose": "Retail — discount stores and e-commerce",
        "formation_verified": True,
        "formation_detail": "Delaware corporation in good standing",
        "source_urls": ["https://icis.corp.delaware.gov/", "https://stock.walmart.com/"],
    },
    {
        "_key": "costco wholesale corporation|WA",
        "_patterns": ["costcowholesalecorporation", "costco"],
        "_state": "WA",
        "legal_name": "Costco Wholesale Corporation",
        "status": "active",
        "entity_type": "Corporation",
        "formation_date": "1987-05-12",
        "registered_agent_address": "999 Lake Drive, Issaquah, WA 98027",
        "naics_or_purpose": "Membership warehouse club retail",
        "formation_verified": True,
        "formation_detail": "Washington corporation in good standing",
        "source_urls": ["https://www.sos.wa.gov/", "https://investor.costco.com/"],
    },
]


def _match_mock_entity(legal_name: str, state: str = "") -> dict | None:
    state_code = state.upper().strip() if state else ""
    key = f"{legal_name.lower().strip()}|{state_code}" if state_code else ""
    norm_input = _normalize_name(legal_name)
    if state_code:
        for entity in MOCK_ENTITIES:
            if entity["_key"] == key:
                return entity
    for entity in MOCK_ENTITIES:
        patterns = entity.get("_patterns", [])
        if any(p in norm_input or norm_input in p for p in patterns):
            return entity
        if norm_input in _normalize_name(entity["legal_name"]):
            return entity
    return None


def _apply_incorporation_hints(facts: dict[str, Any], state: str) -> dict[str, Any]:
    """Surface inferred state when user searched by name only."""
    inc = facts.get("incorporation_state")
    if isinstance(inc, str) and len(inc.strip()) == 2:
        inc = inc.strip().upper()
        facts["incorporation_state"] = inc
        if not state:
            facts["suggested_state"] = inc
    return facts


def _fallback_extract(legal_name: str, state: str, search_context: str, api_error: str | None = None) -> dict[str, Any]:
    entity = _match_mock_entity(legal_name, state)
    if entity:
        registered_state = entity.get("_state", state.upper() if state else "")
        state_ok = not state or state.upper() == registered_state
        rationale = "Matched demo fixture (no live search)"
        if state and not state_ok:
            rationale = (
                f"Company matched but you entered {state.upper()} — likely registered in {registered_state}."
            )
        elif not state and registered_state:
            rationale = f"Matched {entity['legal_name']} — incorporated in {registered_state}"
        return {
            **{k: v for k, v in entity.items() if not k.startswith("_")},
            "incorporation_state": registered_state or None,
            "confidence": 0.9 if state_ok else 0.75,
            "rationale": rationale,
            "source_urls": entity.get("source_urls", []),
            "search_method": "demo_fixture",
            "state_mismatch": bool(state and not state_ok),
            "suggested_state": registered_state if (not state or not state_ok) else None,
            "search_error": api_error,
        }
    has_context = len(search_context) > 50 and "No search results" not in search_context
    detail = "Could not verify against public records."
    if api_error:
        detail = api_error
    elif not has_context:
        detail = "No search results — fix ANTHROPIC_API_KEY for live web search, or try demo: Acme Trading LLC + DE"
    return {
        "legal_name": legal_name,
        "status": "unknown",
        "entity_type": None,
        "formation_date": None,
        "registered_agent_address": None,
        "naics_or_purpose": None,
        "formation_verified": False,
        "formation_detail": detail,
        "source_urls": [],
        "confidence": 0.3 if has_context else 0.1,
        "rationale": "Insufficient public data found" if not api_error else "API key invalid — live search unavailable",
        "search_method": "none",
        "search_error": api_error,
    }


async def search_company_public_info(legal_name: str, state: str = "") -> dict[str, Any]:
    api_error = _check_anthropic_auth()
    state_code = state.strip().upper() if state else ""
    name_key = legal_name.strip().lower()

    cached = api_cache.get("public_search", name_key, state_code)
    if cached is not None:
        return api_cache.mark_cached(cached)

    query_state = f" {state_code}" if state_code else ""
    search_queries = [f'"{legal_name}"{query_state} secretary of state business entity']

    # Known demo entities — free fixture, no web search spend
    fixture = _match_mock_entity(legal_name, state_code)
    if fixture and not api_error:
        facts = _fallback_extract(legal_name, state_code, "", None)
        facts = _apply_incorporation_hints(facts, state_code)
        facts["search_queries"] = search_queries
        facts["needs_user_confirm"] = facts.get("confidence", 0) < CONFIDENCE_AUTO
        api_cache.set("public_search", facts, name_key, state_code)
        return facts

    # Demo fixture fast-path when API is down (still useful for demos)
    if api_error:
        if fixture:
            facts = _fallback_extract(legal_name, state_code, "", api_error)
            facts["search_queries"] = search_queries
            facts["needs_user_confirm"] = facts.get("confidence", 0) < CONFIDENCE_AUTO
            api_cache.set("public_search", facts, name_key, state_code)
            return facts

    # 1) Anthropic native web search
    if not api_error:
        facts = await _anthropic_web_search_kyb(legal_name, state_code)
        if facts and facts.get("confidence", 0) >= 0.3:
            facts = _apply_incorporation_hints(facts, state_code)
            facts["search_queries"] = search_queries
            facts["needs_user_confirm"] = facts.get("confidence", 0) < CONFIDENCE_AUTO
            api_cache.set("public_search", facts, name_key, state_code)
            return facts

    # 2) Fallback: Tavily / DuckDuckGo + Claude extract
    if state_code:
        queries = [
            f'"{legal_name}" {state_code} secretary of state business entity status',
            f'"{legal_name}" {state_code} registered agent address incorporation',
        ]
    else:
        queries = [
            f'"{legal_name}" secretary of state business entity incorporation',
            f'"{legal_name}" OpenCorporates company registration',
        ]
    all_results: list[dict] = []
    seen_urls: set[str] = set()
    for q in queries:
        hits = await web_search(q, max_results=4)
        for h in hits:
            url = h.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(h)

    context = _format_search_context(all_results)
    facts = await _extract_with_anthropic(legal_name, state_code, context, api_error)
    facts = _apply_incorporation_hints(facts, state_code)

    # If live search + extract failed, try demo fixtures (reliable for known companies)
    if facts.get("confidence", 0) < 0.3 and _match_mock_entity(legal_name, state_code):
        facts = _fallback_extract(legal_name, state_code, context, api_error)

    facts["search_queries"] = queries
    facts["needs_user_confirm"] = facts.get("confidence", 0) < CONFIDENCE_AUTO
    if api_error and not facts.get("search_error"):
        facts["search_error"] = api_error
    api_cache.set("public_search", facts, name_key, state_code)
    return facts


async def extract_document_claims(label: str, text_content: str) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(_extract_document_claims_sync, label, text_content)


def _extract_document_claims_sync(label: str, text_content: str) -> dict[str, Any]:
    text_hash = api_cache.content_hash(text_content[:8000])
    label_key = label.strip().lower()
    cached = api_cache.get("doc_extract", label_key, text_hash)
    if cached is not None:
        return api_cache.mark_cached(cached)

    api_key = _anthropic_key()
    if not api_key or not text_content.strip():
        result = {"label": label, "extracted": {}, "note": "No text extracted or API key missing"}
        api_cache.set("doc_extract", result, label_key, text_hash)
        return result

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Extract structured KYB claims from this document labeled "{label}".
Return JSON only:
{{
  "document_type": "sos_filing|articles|license|government_id|other|null",
  "entity_name": null,
  "ein": null,
  "person_name": null,
  "address": null,
  "formation_date": null,
  "key_facts": []
}}
Document text:
{text_content[:8000]}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        extracted = _parse_json_from_text(_text_blocks(message))
        result = {"label": label, "extracted": extracted}
        api_cache.set("doc_extract", result, label_key, text_hash)
        return result
    except Exception as exc:
        result = {"label": label, "extracted": {}, "note": str(exc)}
        return result
