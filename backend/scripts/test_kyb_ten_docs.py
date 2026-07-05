#!/usr/bin/env python3
"""End-to-end KYB submit test with 10 dummy documents.

Usage (from backend/, server running on :8000):
  python scripts/test_kyb_ten_docs.py
  python scripts/test_kyb_ten_docs.py --base-url http://127.0.0.1:8000 --no-docs   # expect 400
"""

from __future__ import annotations

import argparse
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

SAMPLE = BACKEND_ROOT / "fixtures" / "sample_sos_filing.txt"
ENTITY = "Harborline Trading LLC"
STATE = "DE"

DOC_SPECS = [
    ("Articles of Incorporation", "articles"),
    ("Certificate of Formation", "sos_filing"),
    ("DE SOS Good Standing", "sos_filing"),
    ("Operating Agreement excerpt", "articles"),
    ("Registered Agent letter", "license"),
    ("EIN assignment letter", "other"),
    ("Business license copy", "license"),
    ("Annual report excerpt", "sos_filing"),
    ("Organizer affidavit", "other"),
    ("Supporting identity doc", "government_id"),
]


def _doc_body(label: str, kind: str, index: int) -> str:
    base = SAMPLE.read_text(encoding="utf-8") if SAMPLE.exists() else ""
    return f"""{base}

--- Document {index + 1}: {label} ---
Entity Name: {ENTITY}
State of Formation: Delaware
Document Type: {kind}
Status: Good Standing — active
Reference: HL-{index + 1:03d}
"""


def _parse_sse(raw: str) -> dict | None:
    result = None
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if payload.get("type") == "complete" and payload.get("session_id"):
                    result = payload
    return result


def run_test(*, base_url: str, doc_count: int, expect_fail: bool) -> int:
    base = base_url.rstrip("/")
    print(f"API: {base}")
    print(f"Documents: {doc_count}")

    with httpx.Client(timeout=600.0) as client:
        session = client.post(f"{base}/api/enterprise/kyb/session", json={})
        session.raise_for_status()
        session_id = session.json()["session_id"]
        print(f"Session: {session_id}")

        multipart: list[tuple[str, tuple]] = [
            ("legal_name", (None, ENTITY)),
            ("state", (None, STATE)),
            ("ein", (None, "12-3456789")),
            ("operating_address", (None, "1209 Orange Street, Wilmington, DE 19801")),
            ("business_purpose", (None, "General trading and commerce")),
            ("beneficial_owners", (None, json.dumps([{"name": "Jane Doe", "ownership_pct": 100}]))),
            ("control_persons", (None, json.dumps([{"name": "Jane Doe", "title": "CEO"}]))),
        ]
        for i, (label, kind) in enumerate(DOC_SPECS[:doc_count]):
            filename = f"dummy_{i + 1}_{kind}.txt"
            multipart.append(("document_labels", (None, label)))
            multipart.append(
                ("documents", (filename, _doc_body(label, kind, i).encode("utf-8"), "text/plain"))
            )

        if doc_count == 0:
            empty_form: list[tuple[str, tuple]] = [
                ("legal_name", (None, ENTITY)),
                ("state", (None, STATE)),
                ("beneficial_owners", (None, "[]")),
                ("control_persons", (None, "[]")),
            ]
            resp = client.post(
                f"{base}/api/enterprise/kyb/{session_id}/submit",
                files=empty_form,
            )
            if expect_fail:
                if resp.status_code == 400:
                    print("OK — submit rejected without documents (400)")
                    return 0
                print(f"FAIL — expected 400, got {resp.status_code}: {resp.text[:200]}")
                return 1
            resp.raise_for_status()
            return 0

        resp = client.post(
            f"{base}/api/enterprise/kyb/{session_id}/submit/stream",
            files=multipart,
        )
        if expect_fail:
            print(f"FAIL — expected error but got {resp.status_code}")
            return 1

        if resp.status_code != 200:
            print(f"FAIL — HTTP {resp.status_code}: {resp.text[:400]}")
            return 1

        result = _parse_sse(resp.text)
        if not result:
            print("FAIL — no complete event in SSE stream")
            print(resp.text[-800:])
            return 1

        status = result.get("scorecard", {}).get("kyb_status", "?")
        docs = result.get("pipeline", {}).get("upload", {}).get("document_count", doc_count)
        cost = result.get("cost_analysis", {}).get("total_cost_usd", 0)
        print(f"OK — kyb_status={status}, documents={docs}, cost=${cost:.4f}")
        trace_steps = len(result.get("agent_trace") or [])
        print(f"Agent trace steps: {trace_steps}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Test KYB with 10 dummy documents")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--docs", type=int, default=10, help="Number of documents (max 10)")
    parser.add_argument("--no-docs", action="store_true", help="Verify submit fails without documents")
    args = parser.parse_args()

    doc_count = 0 if args.no_docs else max(0, min(args.docs, len(DOC_SPECS)))
    raise SystemExit(run_test(base_url=args.base_url, doc_count=doc_count, expect_fail=args.no_docs))


if __name__ == "__main__":
    main()
