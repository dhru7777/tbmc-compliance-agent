#!/usr/bin/env python3
"""Local x401 Stage 3 test — complete demo company (Nexbridge Capital).

Uses cached LLM responses when API_CACHE_ENABLED=true and the same
document/search inputs were seen before.

Usage (backend running on :8000):
  python scripts/test_x401_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

_env = BACKEND_ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            import os

            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

import httpx

from app.services.demo_companies import build_demo_pdf, demo_profile, get_demo_company

COMPANY_ID = "nexbridge-capital"
BASE = "http://127.0.0.1:8000"


def _parse_sse(raw: str) -> dict | None:
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if payload.get("type") == "complete" and payload.get("session_id"):
                    return payload
    return None


def main() -> int:
    profile = demo_profile(COMPANY_ID)
    company = get_demo_company(COMPANY_ID)
    pdf_bytes, filename, _ = build_demo_pdf(COMPANY_ID)

    print("=" * 60)
    print("TBMC x401 Stage 3 — local demo test")
    print("=" * 60)
    print(f"Company:     {profile['legal_name']} ({COMPANY_ID})")
    print(f"Volume:      ${profile['monthly_volume_low_usd']:,} – ${profile['monthly_volume_high_usd']:,}/mo")
    print(f"Document:    {filename} ({len(pdf_bytes):,} bytes)")
    print(f"API:         {BASE}")
    print()

    with httpx.Client(timeout=600.0) as client:
        health = client.get(f"{BASE}/api/health")
        health.raise_for_status()
        print(f"Health:      {health.json()}")

        session = client.post(f"{BASE}/api/enterprise/kyb/session", json={})
        session.raise_for_status()
        session_id = session.json()["session_id"]
        print(f"Session:     {session_id}")
        print()
        print("Submitting (stream) — uses API cache when inputs match prior runs…")

        multipart: list[tuple[str, tuple]] = [
            ("legal_name", (None, profile["legal_name"])),
            ("state", (None, profile["state"])),
            ("ein", (None, profile["ein"])),
            ("operating_address", (None, profile["operating_address"])),
            ("business_purpose", (None, profile["business_purpose"])),
            ("monthly_volume_low_usd", (None, str(profile["monthly_volume_low_usd"]))),
            ("monthly_volume_high_usd", (None, str(profile["monthly_volume_high_usd"]))),
            (
                "beneficial_owners",
                (None, json.dumps(profile["beneficial_owners"])),
            ),
            (
                "control_persons",
                (None, json.dumps(profile["control_persons"])),
            ),
            ("document_labels", (None, profile["document_label"])),
            ("documents", (filename, pdf_bytes, "application/pdf")),
        ]

        with client.stream(
            "POST",
            f"{BASE}/api/enterprise/kyb/{session_id}/submit/stream",
            files=multipart,
        ) as resp:
            resp.raise_for_status()
            raw = "".join(resp.iter_text())
        result = _parse_sse(raw)
        if not result:
            print("FAIL — no complete event in SSE stream")
            return 1

        sc = result.get("scorecard", {})
        pipe = result.get("pipeline", {})
        cred = result.get("credential")
        status = sc.get("kyb_status")
        print()
        print("-" * 60)
        print(f"KYB status:  {status}")
        print(f"Confidence:  {sc.get('confidence_score')}")
        print(f"Flags:       {sc.get('flags_count')}  Blocks: {sc.get('blocks_count')}")
        print(f"x401:        {pipe.get('x401', {})}")

        if status != "passed" or not cred:
            print("\nScorecard items:")
            for item in sc.get("items", []):
                print(f"  [{item.get('result')}] #{item.get('num')} {item.get('item')}: {item.get('detail')}")
            print("\nNo credential issued — fix flags/blocks and resubmit.")
            return 1

        scope = cred.get("allowed_scope", {})
        print()
        print("CREDENTIAL ISSUED")
        print(f"  ID:          {cred.get('credential_id')}")
        print(f"  Issued to:   {cred.get('issued_to', {}).get('legal_name')}")
        print(f"  Expires:     {cred.get('expiry_date')}")
        print(f"  Credit limit: ${scope.get('credit_limit_usd'):,.2f} USDC")
        print(f"  Signing key: {cred.get('signing_key_id')}")
        print(f"  Signature:   {str(cred.get('signature', ''))[:40]}…")

        cred_resp = client.get(f"{BASE}/api/enterprise/kyb/{session_id}/credential")
        cred_resp.raise_for_status()
        verify_ok = cred_resp.json().get("signature_valid")
        print(f"  Verify API:  signature_valid={verify_ok}")

        pdf_resp = client.get(f"{BASE}/api/enterprise/kyb/{session_id}/credential.pdf")
        pdf_resp.raise_for_status()
        print(f"  PDF:         {len(pdf_resp.content):,} bytes")

        pub = client.get(f"{BASE}/.well-known/tbmc-signing-key.json")
        pub.raise_for_status()
        print(f"  Public key:  {pub.json().get('signing_key_id')}")

        print()
        print("-" * 60)
        print("OPEN IN BROWSER")
        print(f"  UI scorecard:  http://127.0.0.1:5173  (re-run same session won't auto-load)")
        print(f"  PDF direct:    {BASE}/api/enterprise/kyb/{session_id}/credential.pdf")
        print(f"  JSON cred:     {BASE}/api/enterprise/kyb/{session_id}/credential")
        print(f"  Session ID:    {session_id}")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
