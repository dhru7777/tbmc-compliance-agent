"""Trial/demo company packages — unique PDFs for UI selection (not disk-cached)."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Any

TRIAL_DOC_MARKER = "KYB TRIAL DOCUMENT — NOT FOR PRODUCTION USE"

DEMO_COMPANIES: list[dict[str, Any]] = [
    {
        "id": "nexbridge-capital",
        "label": "Nexbridge Capital LLC — complete",
        "complete": True,
        "hint": "Full formation, EIN, ownership, and ID in one file.",
        "legal_name": "Nexbridge Capital LLC",
        "state": "DE",
        "ein": "84-3928174",
        "operating_address": "1209 Orange Street, Wilmington, DE 19801",
        "business_purpose": "Payment processing and financial technology services",
        "monthly_volume_low_usd": 80000,
        "monthly_volume_high_usd": 220000,
        "beneficial_owners": [
            {"name": "Alex Morgan", "ownership_pct": 60},
            {"name": "Riley Chen", "ownership_pct": 40},
        ],
        "control_persons": [{"name": "Alex Morgan", "title": "CEO"}],
        "document_label": "Certificate of Formation and Government ID",
        "file_stem": "nexbridge_capital_formation",
    },
    {
        "id": "summit-harbor-logistics",
        "label": "Summit Harbor Logistics Inc. — complete",
        "complete": True,
        "hint": "Delaware corporation — all KYB fields present.",
        "legal_name": "Summit Harbor Logistics Inc.",
        "state": "DE",
        "ein": "52-1847392",
        "operating_address": "251 Little Falls Drive, Wilmington, DE 19808",
        "business_purpose": "Freight brokerage and supply chain logistics",
        "monthly_volume_low_usd": 45000,
        "monthly_volume_high_usd": 120000,
        "beneficial_owners": [{"name": "Jordan Ellis", "ownership_pct": 100}],
        "control_persons": [{"name": "Jordan Ellis", "title": "President"}],
        "document_label": "Articles of Incorporation and Government ID",
        "file_stem": "summit_harbor_articles",
    },
    {
        "id": "clearline-payments",
        "label": "Clearline Payments Corporation — complete",
        "complete": True,
        "hint": "Wyoming fintech — full package for network admission trial.",
        "legal_name": "Clearline Payments Corporation",
        "state": "WY",
        "ein": "88-2049156",
        "operating_address": "30 N Gould St Ste R, Sheridan, WY 82801",
        "business_purpose": "Digital payment infrastructure and merchant services",
        "monthly_volume_low_usd": 150000,
        "monthly_volume_high_usd": 400000,
        "beneficial_owners": [
            {"name": "Samira Okonkwo", "ownership_pct": 70},
            {"name": "Dev Patel", "ownership_pct": 30},
        ],
        "control_persons": [{"name": "Samira Okonkwo", "title": "CEO"}],
        "document_label": "SOS Formation Certificate and Government ID",
        "file_stem": "clearline_payments_sos",
    },
    {
        "id": "redwood-atlas-trading",
        "label": "Redwood Atlas Trading LLC — incomplete (EIN missing)",
        "complete": False,
        "hint": "Formation on file but EIN and ownership not included.",
        "legal_name": "Redwood Atlas Trading LLC",
        "state": "DE",
        "ein": "",
        "operating_address": "850 New Burton Road, Dover, DE 19904",
        "business_purpose": "Commodity trading and import-export",
        "monthly_volume_low_usd": 30000,
        "monthly_volume_high_usd": 90000,
        "beneficial_owners": [],
        "control_persons": [],
        "document_label": "Certificate of Formation — information missing",
        "file_stem": "redwood_atlas_formation_incomplete",
    },
    {
        "id": "borealis-gate-ventures",
        "label": "Borealis Gate Ventures LLC — incomplete (good standing missing)",
        "complete": False,
        "hint": "Entity named but active status certificate not included.",
        "legal_name": "Borealis Gate Ventures LLC",
        "state": "NV",
        "ein": "91-7734520",
        "operating_address": "401 Ryland St, Reno, NV 89502",
        "business_purpose": "Venture capital and advisory services",
        "monthly_volume_low_usd": 10000,
        "monthly_volume_high_usd": 50000,
        "beneficial_owners": [{"name": "Casey Nguyen", "ownership_pct": 100}],
        "control_persons": [{"name": "Casey Nguyen", "title": "Managing Member"}],
        "document_label": "Articles of Organization and Government ID — good standing missing",
        "file_stem": "borealis_gate_articles_incomplete",
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


def is_trial_document_text(text: str) -> bool:
    return TRIAL_DOC_MARKER in (text or "")


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
        "SECRETARY OF STATE — BUSINESS ENTITY FILING",
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
                "Status: Active — in good standing",
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
                "GOVERNMENT ID — Managing Member",
                f"Name: {company['control_persons'][0]['name']}",
                "Document: Driver License (DE)",
                "ID verification: client-submitted copy on file",
            ]
        )
    elif company["id"] == "redwood-atlas-trading":
        lines.extend(
            [
                "",
                "Federal EIN: NOT PROVIDED — pending IRS assignment",
                "Status: Active — in good standing",
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
                "Status: Formation recorded — standing certificate NOT attached",
                "",
                f"Beneficial Owner: {company['beneficial_owners'][0]['name']} — 100% ownership",
                f"Control Person: {cp['name']}, {cp['title']}",
                "NOTE: Secretary of State compliance certificate missing from submission.",
                "",
                "GOVERNMENT ID — Managing Member",
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


def demo_profile(company_id: str) -> dict[str, Any]:
    c = get_demo_company(company_id)
    return {
        "id": c["id"],
        "label": c["label"],
        "complete": c["complete"],
        "hint": c["hint"],
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
