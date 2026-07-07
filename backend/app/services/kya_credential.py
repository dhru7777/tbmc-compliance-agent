"""
KYA (Know Your Agent) proof credentials for agent-side auditability.

KYA proof = agent_id + session_id + audit.md (content-bound via SHA-256) + signed credential.

The audit record combines:
  - session.md audit trail
  - LLM call log (operations, tokens, cost)
  - Agent trace steps (think / act / observe)

Issued when verification passes; audit.md is written on every completed verify run.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services import md_recorder
from app.services.x401.credential import verify_credential
from app.services.x401.signing import sign_canonical_payload, signing_key_id

KYB_EXPIRY_DAYS = int(os.getenv("KYB_EXPIRY_DAYS", "180"))
KYA_AGENT_ID = os.getenv("KYA_AGENT_ID", "tbmc-compliance-agent-v1")
ISSUER_NAME = "Better Money Company Clearinghouse Compliance Agent"

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"
AUDIT_MD_FILENAME = "audit.md"
KYA_CREDENTIAL_FILENAME = "kya_agent_credential.json"


def agent_id() -> str:
    return KYA_AGENT_ID


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_digest(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return _sha256_hex(canonical)


def _audit_path(session_id: str) -> Path:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder / AUDIT_MD_FILENAME


def _format_llm_section(cost_analysis: dict | None) -> str:
    cost = cost_analysis or {}
    calls = cost.get("calls") or []
    lines = [
        "## LLM calls",
        "",
        f"**Live API calls:** {cost.get('live_api_calls', 0)}  ",
        f"**Total input tokens:** {cost.get('total_input_tokens', 0):,}  ",
        f"**Total output tokens:** {cost.get('total_output_tokens', 0):,}  ",
        f"**Total cost (USD):** {cost.get('total_cost_usd', 0)}  ",
        "",
    ]
    if not calls:
        lines.append("_No LLM calls recorded._\n")
        return "\n".join(lines)

    lines.extend(
        [
            "| # | Operation | Agent | In | Out | Cost USD | Source |",
            "|---|-----------|-------|----|-----|----------|--------|",
        ]
    )
    for i, call in enumerate(calls, 1):
        source = "cache" if call.get("from_cache") else ("skip" if call.get("skipped") else "live")
        lines.append(
            f"| {i} | {call.get('operation', '')} | {call.get('agent', '')} | "
            f"{call.get('input_tokens', 0)} | {call.get('output_tokens', 0)} | "
            f"{call.get('total_cost_usd', 0)} | {source} |"
        )
    lines.append("")
    return "\n".join(lines)


def _format_trace_section(agent_trace: list[dict] | None) -> str:
    trace = agent_trace or []
    lines = ["## Agent trace", "", f"**Steps:** {len(trace)}  ", ""]
    if not trace:
        lines.append("_No agent trace steps recorded._\n")
        return "\n".join(lines)

    lines.extend(
        [
            "| # | Type | Agent | Label | Message |",
            "|---|------|-------|-------|---------|",
        ]
    )
    for i, step in enumerate(trace, 1):
        label = (step.get("label") or "").replace("|", "/")
        message = (step.get("message") or "").replace("|", "/")[:120]
        lines.append(
            f"| {i} | {step.get('type', '')} | {step.get('agent', '')} | {label} | {message} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_audit_md(
    *,
    session_id: str,
    verify_result: dict,
    scorecard: dict | None = None,
    enterprise_id: str | None = None,
    client_master_credential_id: str | None = None,
) -> str:
    """Assemble audit.md: session trail + LLM calls + agent trace."""
    sc = scorecard or (verify_result.get("deterministic") or {}).get("scorecard") or {}
    now = _now_utc().isoformat()
    session_trail = md_recorder.read_session(session_id)

    header = (
        f"# KYA Agent Audit Record\n\n"
        f"**Agent ID:** {agent_id()}  \n"
        f"**Session ID:** {session_id}  \n"
        f"**Generated:** {now}  \n"
        f"**Verification outcome:** {sc.get('kyb_status', verify_result.get('pipeline_status', 'unknown'))}  \n"
    )
    if enterprise_id:
        header += f"**Enterprise ID:** {enterprise_id}  \n"
    if client_master_credential_id:
        header += f"**Client master credential (C4):** {client_master_credential_id}  \n"
    header += "\n---\n\n"

    body = (
        "## Session audit trail\n\n"
        f"{session_trail.strip() or '_No session steps recorded._'}\n\n"
        "---\n\n"
        f"{_format_llm_section(verify_result.get('cost_analysis'))}"
        "---\n\n"
        f"{_format_trace_section(verify_result.get('agent_trace'))}"
    )

    content = header + body
    digest = _sha256_hex(content)
    footer = (
        "---\n\n"
        "## Cryptographic binding\n\n"
        f"This audit record is bound to the KYA agent credential via SHA-256:\n\n"
        f"`{digest}`\n"
    )
    return content + footer


def save_audit_md(session_id: str, content: str) -> Path:
    path = _audit_path(session_id)
    path.write_text(content, encoding="utf-8")
    return path


def read_audit_md(session_id: str) -> str:
    path = _audit_path(session_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _sign_payload(payload: dict[str, Any]) -> dict[str, Any]:
    signable = {k: v for k, v in payload.items() if k not in ("signature", "signing_key_id")}
    canonical = json.dumps(signable, sort_keys=True, separators=(",", ":"))
    payload["signature"] = sign_canonical_payload(canonical)
    payload["signing_key_id"] = signing_key_id()
    return payload


def issue_kya_credential(
    *,
    session_id: str,
    audit_md: str,
    verify_result: dict,
    scorecard: dict,
    enterprise_id: str | None = None,
    client_master_credential_id: str | None = None,
) -> dict | None:
    """
    Issue signed KYA proof credential when verification passed.
    Binds agent_id + session_id + audit.md hash + LLM/trace digests.
    """
    if scorecard.get("kyb_status") != "passed":
        return None

    audit_sha = audit_content_digest(audit_md)
    cost = verify_result.get("cost_analysis") or {}
    trace = verify_result.get("agent_trace") or []
    llm_digest = _canonical_digest(cost.get("calls") or [])
    trace_digest = _canonical_digest(trace)

    now = _now_utc()
    expiry = (now + timedelta(days=KYB_EXPIRY_DAYS)).isoformat()
    cred_id = str(uuid.uuid4())

    credential = _sign_payload(
        {
            "credential_type": "kya_agent_proof_credential",
            "credential_tier": "KYA",
            "credential_id": cred_id,
            "agent_id": agent_id(),
            "session_id": session_id,
            "enterprise_id": enterprise_id,
            "issued_by": ISSUER_NAME,
            "issuance_date": now.isoformat(),
            "expiry_date": expiry,
            "proof_purpose": "proof_of_agent_verification_and_ownership",
            "audit_md_sha256": audit_sha,
            "audit_md_filename": AUDIT_MD_FILENAME,
            "verification_outcome": {
                "kyb_status": scorecard.get("kyb_status"),
                "flags_count": scorecard.get("flags_count", 0),
                "blocks_count": scorecard.get("blocks_count", 0),
                "confidence_score": scorecard.get("confidence_score"),
            },
            "client_master_credential_id": client_master_credential_id,
            "llm_calls": {
                "live_api_calls": cost.get("live_api_calls", 0),
                "total_input_tokens": cost.get("total_input_tokens", 0),
                "total_output_tokens": cost.get("total_output_tokens", 0),
                "total_cost_usd": cost.get("total_cost_usd", 0),
                "call_digest_sha256": llm_digest,
            },
            "agent_trace": {
                "step_count": len(trace),
                "trace_digest_sha256": trace_digest,
            },
        }
    )

    return {
        "agent_id": agent_id(),
        "session_id": session_id,
        "enterprise_id": enterprise_id,
        "audit_md_sha256": audit_sha,
        "audit_md_filename": AUDIT_MD_FILENAME,
        "audit_md_url": f"/api/enterprise/kyb/{session_id}/audit.md",
        "kya_credential_url": f"/api/enterprise/kyb/{session_id}/kya-credential",
        "credential": credential,
        "issued_at": now.isoformat(),
        "expiry_date": expiry,
    }


def verify_kya_proof(kya_proof: dict) -> dict[str, bool]:
    cred = (kya_proof or {}).get("credential") or kya_proof
    valid = bool(cred and verify_credential(cred))
    audit_sha = (kya_proof or {}).get("audit_md_sha256") or cred.get("audit_md_sha256")
    return {
        "credential_valid": valid,
        "audit_md_sha256": audit_sha,
        "agent_id": cred.get("agent_id") if cred else None,
        "session_id": cred.get("session_id") if cred else None,
    }


def audit_content_digest(audit_md: str) -> str:
    """SHA-256 of audit body (excludes cryptographic binding footer)."""
    marker = "## Cryptographic binding"
    idx = audit_md.find(marker)
    body = audit_md[:idx].rstrip() if idx >= 0 else audit_md.rstrip()
    return _sha256_hex(body)


def verify_audit_binding(session_id: str, kya_proof: dict) -> bool:
    """Confirm on-disk audit.md still matches the credential hash."""
    cred = (kya_proof or {}).get("credential") or kya_proof
    expected = cred.get("audit_md_sha256") or (kya_proof or {}).get("audit_md_sha256")
    if not expected:
        return False
    audit = read_audit_md(session_id)
    if not audit:
        return False
    return audit_content_digest(audit) == expected


def save_kya_proof(session_id: str, kya_proof: dict) -> str:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / KYA_CREDENTIAL_FILENAME
    path.write_text(json.dumps(kya_proof, indent=2), encoding="utf-8")
    return str(path)


def load_kya_proof(session_id: str) -> dict | None:
    path = RECORDS_DIR / session_id / KYA_CREDENTIAL_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
