#!/usr/bin/env python3
"""Compare Anthropic token spend: public web search vs document extraction.

Usage (from backend/):
  python scripts/llm_cost_analysis.py
  python scripts/llm_cost_analysis.py --company "Stripe, Inc." --state DE --docs 3
  python scripts/llm_cost_analysis.py --estimate-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# Load .env before importing app modules
_env = BACKEND_ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

from app.services import llm_usage  # noqa: E402

SAMPLE_DOC = BACKEND_ROOT / "fixtures" / "sample_sos_filing.txt"
WEB_SEARCH_MAX_USES = int(os.getenv("KYB_WEB_SEARCH_MAX_USES", "2"))
MODEL = "claude-sonnet-4-6"


def _anthropic_client():
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set in backend/.env")
    return anthropic.Anthropic(api_key=api_key)


def probe_public_search(legal_name: str, state: str) -> llm_usage.UsageRecord:
    """Mirror llm_search._anthropic_web_search_kyb with usage capture."""
    client = _anthropic_client()
    state_hint = (
        f"State of incorporation/registration: {state}"
        if state
        else "State of incorporation: unknown — search nationally and infer from official registries."
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

    message = client.messages.create(
        model=MODEL,
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
    record = llm_usage.usage_from_message(message, operation="Public record search (web)", model=MODEL)
    record.notes.append(f"max_tokens=2048, web_search max_uses={WEB_SEARCH_MAX_USES}")
    return record


def probe_doc_extract(label: str, text_content: str) -> llm_usage.UsageRecord:
    """Mirror llm_search._extract_document_claims_sync with usage capture."""
    client = _anthropic_client()
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

    message = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    record = llm_usage.usage_from_message(
        message, operation=f"Document extraction ({label})", model=MODEL
    )
    chars = min(len(text_content), 8000)
    record.notes.append(f"max_tokens=512, document chars sent={chars:,}")
    return record


def estimate_from_prompts(legal_name: str, state: str, doc_text: str, n_docs: int) -> None:
    """Offline estimate from prompt sizes (no API key required)."""
    # Rough heuristic: ~4 chars per token for English text
    def approx_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    search_prompt = f"company={legal_name} state={state} ..."  # placeholder size
    search_prompt_tokens = approx_tokens(
        f"""KYB web search prompt for {legal_name} {state} """
        + "x" * 400  # base prompt ~400 chars beyond company name
    )
    # Web search typically injects 2-5k tokens of result content per search
    search_input = search_prompt_tokens + 2500 * WEB_SEARCH_MAX_USES
    search_output = 400  # JSON response

    doc_prompt_base = 250
    doc_input = doc_prompt_base + approx_tokens(doc_text[:8000])
    doc_output = 150

    search_record = llm_usage.UsageRecord(
        operation="Public record search (estimated)",
        model=MODEL,
        input_tokens=search_input,
        output_tokens=search_output,
        web_search_requests=WEB_SEARCH_MAX_USES,
        notes=["Offline estimate — run without --estimate-only for live usage"],
    )
    doc_record = llm_usage.UsageRecord(
        operation=f"Document extraction x{n_docs} (estimated)",
        model=MODEL,
        input_tokens=doc_input * n_docs,
        output_tokens=doc_output * n_docs,
        notes=["Offline estimate — run without --estimate-only for live usage"],
    )

    print("=== LLM Cost Analysis (offline estimate) ===\n")
    print("1. Public record search")
    llm_usage.print_usage_record(search_record)
    print()
    print(f"2. Document extraction ({n_docs} doc{'s' if n_docs != 1 else ''})")
    llm_usage.print_usage_record(doc_record)
    llm_usage.compare_records([search_record, doc_record])
    print("\nNote: Public search usually dominates due to web search fees + injected result tokens.")


async def run_live(legal_name: str, state: str, doc_text: str, n_docs: int) -> None:
    os.environ["API_CACHE_ENABLED"] = "false"

    print("=== LLM Cost Analysis (live API) ===")
    print(f"Company: {legal_name} | State: {state or '(none)'}")
    print(f"Pricing: ${llm_usage.INPUT_USD_PER_MTOK}/MTok in, "
          f"${llm_usage.OUTPUT_USD_PER_MTOK}/MTok out, "
          f"${llm_usage.WEB_SEARCH_USD_PER_SEARCH}/search\n")

    records: list[llm_usage.UsageRecord] = []

    print("Running public record search…")
    try:
        records.append(probe_public_search(legal_name, state))
    except Exception as exc:
        print(f"  Public search failed: {exc}\n")
        if "web search" in str(exc).lower():
            print("  Tip: Web search may be disabled on your Anthropic org — fallback path uses")
            print("       DuckDuckGo/Tavily + a second LLM call (not measured here).\n")

    print("Running document extraction…")
    for i in range(n_docs):
        label = "Secretary of State filing" if i == 0 else f"KYB document {i + 1}"
        try:
            records.append(probe_doc_extract(label, doc_text))
        except Exception as exc:
            print(f"  Doc extract {i + 1} failed: {exc}")

    for idx, record in enumerate(records, 1):
        print(f"\n{idx}. {record.operation}")
        llm_usage.print_usage_record(record)

    if records:
        llm_usage.compare_records(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare LLM token cost: public search vs doc extract")
    parser.add_argument("--company", default="Acme Trading LLC", help="Company name for public search")
    parser.add_argument("--state", default="DE", help="State code for public search")
    parser.add_argument("--docs", type=int, default=1, help="Number of document extractions to simulate")
    parser.add_argument(
        "--doc-file",
        type=Path,
        default=SAMPLE_DOC,
        help="Text file to use as sample KYB document",
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="Estimate from prompt sizes without calling the API",
    )
    args = parser.parse_args()

    doc_text = args.doc_file.read_text(encoding="utf-8") if args.doc_file.exists() else ""

    if args.estimate_only or not os.getenv("ANTHROPIC_API_KEY", "").strip():
        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            print("No ANTHROPIC_API_KEY — showing offline estimate.\n")
        estimate_from_prompts(args.company, args.state, doc_text, max(1, args.docs))
        return

    asyncio.run(run_live(args.company, args.state, doc_text, max(1, args.docs)))


if __name__ == "__main__":
    main()
