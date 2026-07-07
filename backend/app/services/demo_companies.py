"""Trial/demo company packages for UI selection."""

from __future__ import annotations

import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRIAL_DOC_MARKER = "KYB TRIAL DOCUMENT - NOT FOR PRODUCTION USE"
MOCK_PACKAGE_MARKER = "[MOCK DOCUMENT —"
MOCK_SLOT_MARKER = "DOCUMENT SLOT"

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK_DOCS_DIR = REPO_ROOT / "agent-skill" / "mock documents"

RIVERSTONE_MOCK_FILENAMES = [
    "9_RiverstoneHoldingsLLC_01_ArticlesOfIncorporation.txt",
    "9_RiverstoneHoldingsLLC_02_CertificateOfGoodStanding.txt",
    "9_RiverstoneHoldingsLLC_03_EINConfirmationLetter.txt",
    "9_RiverstoneHoldingsLLC_04_ProofOfBusinessAddress.txt",
    "9_RiverstoneHoldingsLLC_05_OperatingAgreementExcerpt.txt",
    "9_RiverstoneHoldingsLLC_06_BeneficialOwnershipCertification.txt",
    "9_RiverstoneHoldingsLLC_07_BusinessPurposeStatement.txt",
    "9_RiverstoneHoldingsLLC_08_IdentityVerificationResult.txt",
]

DEMO_COMPANIES: list[dict[str, Any]] = [
    {
        "id": "riverstone-holdings",
        "label": "Riverstone Holdings LLC — complete package",
        "complete": True,
        "package_kind": "mock_bundle",
        "mock_document_filenames": RIVERSTONE_MOCK_FILENAMES,
        "hint": "Loads all 8 KYB documents (formation, EIN, ownership, address, purpose, and ID).",
        "legal_name": "Riverstone Holdings LLC",
        "state": "DE",
        "ein": "84-7293841",
        "operating_address": "251 Little Falls Drive, Suite 200, Wilmington, DE 19808",
        "business_purpose": "Commercial real estate holding and property management services",
        "monthly_volume_low_usd": 100000,
        "monthly_volume_high_usd": 250000,
        "beneficial_owners": [{"name": "Marcus Chen", "ownership_pct": 100}],
        "control_persons": [{"name": "Marcus Chen", "title": "Managing Member"}],
        "document_label": "Riverstone Holdings KYB package",
        "file_stem": "riverstone_holdings",
    },
]


def list_demo_companies() -> list[dict[str, Any]]:
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "complete": c["complete"],
            "hint": c["hint"],
        }
        for c in DEMO_COMPANIES
    ]


def get_demo_company(company_id: str) -> dict[str, Any]:
    for c in DEMO_COMPANIES:
        if c["id"] == company_id:
            return c
    raise KeyError(company_id)


def resolve_trial_company_id(company_id: str | None) -> str | None:
    """Validate trial company id from UI form (dropdown), if present."""
    if not company_id or not str(company_id).strip():
        return None
    cid = str(company_id).strip().lower()
    try:
        get_demo_company(cid)
        return cid
    except KeyError:
        return None


def is_trial_document_text(text: str) -> bool:
    t = text or ""
    if TRIAL_DOC_MARKER in t:
        return True
    # Minimal PDF text extractors often replace em dashes with "?"
    if "KYB TRIAL DOCUMENT" in t and "NOT FOR PRODUCTION USE" in t:
        return True
    return match_trial_company_id(t) is not None


def match_trial_company_id(text: str) -> str | None:
    """Parse DEMO-{company-id}- reference embedded in trial PDF text."""
    upper = (text or "").upper()
    for company in DEMO_COMPANIES:
        marker = f"DEMO-{company['id'].upper()}-"
        if marker in upper:
            return company["id"]
    return None


def trial_public_facts(company_id: str) -> dict[str, Any]:
    """Registry-shaped public facts for deterministic trial scorecard checks."""
    company = get_demo_company(company_id)
    complete = bool(company.get("complete"))
    return {
        "legal_name": company["legal_name"],
        "registered_agent_address": company["operating_address"],
        "naics_or_purpose": company["business_purpose"],
        "incorporation_state": company["state"],
        "status": "active - in good standing" if complete else "formation recorded",
        "formation_verified": True,
        "formation_detail": "Trial package formation document on file",
        "confidence": 0.95 if complete else 0.6,
        "search_method": "trial_package_fixture",
        "source_urls": [],
        "trial_company_id": company_id,
        "trial_complete": complete,
    }


def trial_document_fields_from_company(company_id: str) -> dict[str, Any]:
    """Structured extraction from demo profile when PDF text is unreadable."""
    company = get_demo_company(company_id)
    complete = bool(company.get("complete"))
    fields: dict[str, Any] = {
        "document_type": "government_id" if complete else "sos_filing",
        "entity_name": company["legal_name"],
        "ein": company.get("ein") or None,
        "person_name": company["control_persons"][0]["name"] if company.get("control_persons") else None,
        "address": company["operating_address"],
        "formation_date": None,
        "key_facts": [
            f"Business purpose: {company['business_purpose']}",
            "Status: Active - in good standing" if complete else "Status: Formation recorded",
        ],
    }
    if complete:
        fields["key_facts"].append("GOVERNMENT ID - Managing Member")
    return fields


def parse_trial_document_fields(text: str) -> dict[str, Any]:
    """Deterministic structured extraction from trial PDF plain text."""
    company_id = match_trial_company_id(text)
    company = get_demo_company(company_id) if company_id else None
    fields: dict[str, Any] = {
        "document_type": "sos_filing",
        "entity_name": company["legal_name"] if company else None,
        "ein": company.get("ein") if company else None,
        "person_name": None,
        "address": company["operating_address"] if company else None,
        "formation_date": None,
        "key_facts": [],
    }
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("Entity Name:"):
            fields["entity_name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Federal EIN:"):
            ein = line.split(":", 1)[1].strip()
            if "NOT PROVIDED" not in ein.upper():
                fields["ein"] = ein
        elif line.startswith("Principal Office:"):
            fields["address"] = line.split(":", 1)[1].strip()
        elif line.startswith("Business Purpose:"):
            purpose = line.split(":", 1)[1].strip()
            fields["key_facts"].append(f"Business purpose: {purpose}")
        elif line.startswith("Status:"):
            fields["key_facts"].append(line)
        elif line.startswith("Control Person:"):
            fields["person_name"] = line.split(":", 1)[1].split(",")[0].strip()
        elif "GOVERNMENT ID" in line:
            fields["document_type"] = "government_id"
            fields["key_facts"].append(line)
        elif "good standing" in line.lower() or line.lower().startswith("status:"):
            fields["key_facts"].append(line)
    if company and company.get("control_persons"):
        fields["person_name"] = fields["person_name"] or company["control_persons"][0]["name"]
    return fields


def is_mock_slot_document(text: str) -> bool:
    return MOCK_SLOT_MARKER in (text or "")


def is_mock_package_document(text: str) -> bool:
    t = text or ""
    if MOCK_SLOT_MARKER in t:
        return False
    return MOCK_PACKAGE_MARKER in t


def _mock_category_to_type(category: str) -> str:
    cat = category.lower()
    if "ein" in cat:
        return "ein_letter"
    if "beneficial" in cat or "ownership" in cat:
        return "beneficial_ownership"
    if "operating" in cat:
        return "operating_agreement"
    if "good standing" in cat:
        return "good_standing"
    if "articles" in cat or "formation" in cat:
        return "articles"
    if "address" in cat:
        return "address_proof"
    if "purpose" in cat:
        return "business_purpose"
    if "identity" in cat or "government" in cat:
        return "government_id"
    return "other"


def parse_mock_package_fields(text: str) -> dict[str, Any]:
    """Deterministic extraction for agent-skill mock KYB text packages."""
    import re

    ownership_line = re.compile(r"^([^:\n]+?):\s*(\d+(?:\.\d+)?)\s*%\s*ownership", re.I)
    fields: dict[str, Any] = {
        "document_type": "articles" if "certificate of formation" in (text or "").lower() else "other",
        "entity_name": None,
        "ein": None,
        "incorporation_state": None,
        "person_name": None,
        "address": None,
        "formation_date": None,
        "beneficial_owners": [],
        "control_persons": [],
        "key_facts": [],
    }
    seen_owners: set[str] = set()
    seen_control: set[str] = set()

    def add_owner(name: str, pct: float) -> None:
        key = name.lower()
        if key in seen_owners:
            return
        seen_owners.add(key)
        fields["beneficial_owners"].append({"name": name, "ownership_pct": pct})

    def add_control(name: str, title: str) -> None:
        key = name.lower()
        if key in seen_control:
            return
        seen_control.add(key)
        fields["control_persons"].append({"name": name, "title": title})

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Document category:"):
            fields["document_type"] = _mock_category_to_type(line.split(":", 1)[1])
        elif line.startswith("Entity Name:"):
            fields["entity_name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Federal EIN:"):
            ein = line.split(":", 1)[1].strip()
            if "NOT PROVIDED" not in ein.upper():
                fields["ein"] = ein
        elif line.startswith("State of Formation:"):
            st = line.split(":", 1)[1].strip().upper()
            if len(st) == 2:
                fields["incorporation_state"] = st
        elif line.startswith(("Principal Office:", "Mailing Address:", "Business Address:")):
            fields["address"] = line.split(":", 1)[1].strip()
        elif line.startswith("Business Purpose:"):
            purpose = line.split(":", 1)[1].strip()
            fields["key_facts"].append(f"Business purpose: {purpose}")
            if not fields.get("business_purpose"):
                fields["business_purpose"] = purpose
        elif line.startswith("Formation Date:"):
            fields["formation_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("Status:") and "NOT SUBMITTED" not in line.upper():
            fields["key_facts"].append(line)
        elif line.startswith("Responsible Party:"):
            fields["person_name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Managing Member:"):
            person = line.split(":", 1)[1].strip()
            name = person.split(",")[0].strip()
            title = person.split(",", 1)[1].strip() if "," in person else "Managing Member"
            add_control(name, title)
            fields["key_facts"].append(line)
        elif line.startswith("Control Person:"):
            person = line.split(":", 1)[1].strip()
            name = person.split(",")[0].strip()
            title = person.split(",", 1)[1].strip() if "," in person else "Control person"
            add_control(name, title)
            fields["key_facts"].append(line)
        elif line.lower().startswith("signed:"):
            person = line.split(":", 1)[1].strip()
            name = person.split(",")[0].strip()
            title = person.split(",", 1)[1].strip() if "," in person else "Managing Member"
            add_control(name, title)
        elif match := ownership_line.match(line):
            add_owner(match.group(1).strip(), float(match.group(2)))
            fields["key_facts"].append(line)
        elif "GOVERNMENT ID" in line.upper() or "IDENTITY VERIFICATION" in line.upper():
            fields["document_type"] = "government_id"
            fields["key_facts"].append(line)
    return fields


def _pdf_text_lines(company: dict[str, Any]) -> tuple[list[str], str]:
    cid = company["id"]
    instance_id = uuid.uuid4().hex
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rng = random.Random(instance_id)
    file_number = rng.randint(1_000_000, 9_999_999)
    formation_year = rng.randint(2018, 2024)
    formation_month = rng.randint(1, 12)
    formation_day = rng.randint(1, 28)
    lines = [
        TRIAL_DOC_MARKER,
        f"Reference: DEMO-{cid.upper()}-{instance_id[:16]}",
        f"Issued: {issued_at}",
        f"Package: {'complete' if company['complete'] else 'incomplete'}",
        "",
        "SECRETARY OF STATE - BUSINESS ENTITY FILING",
        f"Entity Name: {company['legal_name']}",
        f"State of Formation: {company['state']}",
        f"Entity Type: {'Corporation' if 'Inc' in company['legal_name'] or 'Corporation' in company['legal_name'] else 'Limited Liability Company'}",
        f"Formation Date: {formation_month:02d}/{formation_day:02d}/{formation_year}",
        f"File Number: {file_number}",
        "",
        f"Principal Office: {company['operating_address']}",
        f"Business Purpose: {company['business_purpose']}",
    ]

    if company["complete"]:
        lines.extend(
            [
                "",
                f"Federal EIN: {company['ein']}",
                "Status: Active - in good standing",
                "Annual report filed and current.",
                "",
                "Beneficial Ownership Certification:",
            ]
        )
        for o in company["beneficial_owners"]:
            lines.append(f"  {o['name']}: {o['ownership_pct']}% ownership")
        lines.append("")
        cp = company["control_persons"][0]
        lines.append(f"Control Person: {cp['name']}, {cp['title']}")
        lines.extend(
            [
                "",
                "GOVERNMENT ID - Managing Member",
                f"Name: {company['control_persons'][0]['name']}",
                "Document: Driver License (DE)",
                "ID verification: client-submitted copy on file",
            ]
        )
    elif company["id"] == "redwood-atlas-trading":
        lines.extend(
            [
                "",
                "Federal EIN: NOT PROVIDED - pending IRS assignment",
                "Status: Active - in good standing",
                "",
                "NOTE: Beneficial ownership schedule not attached.",
                "NOTE: Government ID not included in this package.",
            ]
        )
    else:
        cp = company["control_persons"][0]
        lines.extend(
            [
                "",
                f"Federal EIN: {company['ein']}",
                "Status: Formation recorded - standing certificate NOT attached",
                "",
                f"Beneficial Owner: {company['beneficial_owners'][0]['name']} - 100% ownership",
                f"Control Person: {cp['name']}, {cp['title']}",
                "NOTE: Secretary of State compliance certificate missing from submission.",
                "",
                "GOVERNMENT ID - Managing Member",
                f"Name: {cp['name']}",
                "Document: Driver License (NV)",
                "ID verification: client-submitted copy on file",
            ]
        )

    lines.extend(["", f"Document instance: {instance_id}"])
    return lines, instance_id


def build_demo_pdf(company_id: str) -> tuple[bytes, str, str]:
    """Return (pdf_bytes, filename, instance_id). Fresh content on every call."""
    company = get_demo_company(company_id)
    if company.get("package_kind") == "mock_bundle":
        raise ValueError(f"{company_id} uses a mock document bundle, not a PDF")
    lines, instance_id = _pdf_text_lines(company)
    ops: list[str] = ["BT", "/F1 10 Tf", "48 780 Td"]
    for i, line in enumerate(lines[:58]):
        esc = _escape_pdf_text(line[:96])
        if i == 0:
            ops.append(f"({esc}) Tj")
        else:
            ops.append("0 -12 Td")
            ops.append(f"({esc}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", errors="replace")
    stream_len = len(stream)

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(f"4 0 obj<< /Length {stream_len} >>stream\n".encode() + stream + b"\nendstream\nendobj\n")
    objects.append(
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
    )

    header = b"%PDF-1.4\n"
    body = b""
    offsets = [0]
    pos = len(header)
    for obj in objects:
        offsets.append(pos)
        body += obj
        pos += len(obj)

    xref_pos = len(header) + len(body)
    xref = [f"xref\n0 {len(offsets)}\n", "0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref.append(f"{off:010d} 00000 n \n")
    trailer = (
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    pdf = header + body + "".join(xref).encode() + trailer.encode()
    filename = f"{company['file_stem']}_{instance_id[:8]}.pdf"
    return pdf, filename, instance_id


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _label_from_mock_filename(filename: str) -> str:
    match = re.search(r"_\d+_(.+)\.[^.]+$", filename)
    if match:
        return match.group(1).replace("_", " ").strip()
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip()


def load_mock_bundle_documents(company_id: str) -> list[dict[str, str]]:
    """Return all mock-package text files for a trial company."""
    company = get_demo_company(company_id)
    if company.get("package_kind") != "mock_bundle":
        raise ValueError(f"{company_id} is not a mock document bundle")
    filenames = company.get("mock_document_filenames") or []
    if not filenames:
        raise ValueError(f"{company_id} has no mock documents configured")

    documents: list[dict[str, str]] = []
    for filename in filenames:
        path = MOCK_DOCS_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"Mock document missing: {filename}")
        documents.append(
            {
                "filename": filename,
                "label": _label_from_mock_filename(filename),
                "content": path.read_text(encoding="utf-8"),
            }
        )
    return documents


def demo_profile(company_id: str) -> dict[str, Any]:
    c = get_demo_company(company_id)
    profile: dict[str, Any] = {
        "id": c["id"],
        "label": c["label"],
        "complete": c["complete"],
        "hint": c["hint"],
        "package_kind": c.get("package_kind", "single_pdf"),
        "legal_name": c["legal_name"],
        "state": c["state"],
        "ein": c["ein"],
        "operating_address": c["operating_address"],
        "business_purpose": c["business_purpose"],
        "monthly_volume_low_usd": c.get("monthly_volume_low_usd"),
        "monthly_volume_high_usd": c.get("monthly_volume_high_usd"),
        "beneficial_owners": c["beneficial_owners"],
        "control_persons": c["control_persons"],
        "document_label": c["document_label"],
        "document_filename_prefix": c["file_stem"],
    }
    if c.get("package_kind") == "mock_bundle":
        profile["document_count"] = len(c.get("mock_document_filenames") or [])
    return profile
