"""Professional compliance certificate PDF for issued x401 credentials."""

from __future__ import annotations

import re
from datetime import datetime

from app.services.x401.credential import ISSUER_NAME

# Brand palette (TBMC light theme)
COLOR_BG = (245 / 255, 241 / 255, 230 / 255)
COLOR_TEXT = (26 / 255, 26 / 255, 26 / 255)
COLOR_MUTED = (107 / 255, 107 / 255, 107 / 255)
COLOR_BORDER = (212 / 255, 207 / 255, 196 / 255)
COLOR_GOLD = (196 / 255, 160 / 255, 53 / 255)
COLOR_PASS = (22 / 255, 163 / 255, 74 / 255)

PAGE_W = 612
PAGE_H = 792
MARGIN = 54


def _ascii_safe(text: str) -> str:
    """Helvetica Type1 only supports WinAnsi; strip/replace Unicode that becomes '?'."""
    if text is None:
        return ""
    text = str(text)
    replacements = {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2026": "...",  # ellipsis
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _escape_pdf(text: str) -> str:
    return _ascii_safe(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fmt_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def _fmt_date(iso: str) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return _ascii_safe(iso[:10])


def _circle_bezier(cx: float, cy: float, r: float) -> list[str]:
    k = 0.5522847498 * r
    return [
        f"{cx + r:.2f} {cy:.2f} m",
        f"{cx + r:.2f} {cy + k:.2f} {cx + k:.2f} {cy + r:.2f} {cx:.2f} {cy + r:.2f} c",
        f"{cx - k:.2f} {cy + r:.2f} {cx - r:.2f} {cy + k:.2f} {cx - r:.2f} {cy:.2f} c",
        f"{cx - r:.2f} {cy - k:.2f} {cx - k:.2f} {cy - r:.2f} {cx:.2f} {cy - r:.2f} c",
        f"{cx + k:.2f} {cy - r:.2f} {cx + r:.2f} {cy - k:.2f} {cx + r:.2f} {cy:.2f} c",
        "h",
    ]


class _PdfCanvas:
    def __init__(self) -> None:
        self._ops: list[str] = []

    def save(self) -> list[str]:
        return self._ops

    def stroke_rgb(self, rgb: tuple[float, float, float], width: float = 1) -> None:
        r, g, b = rgb
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG")
        self._ops.append(f"{width:.2f} w")

    def fill_rgb(self, rgb: tuple[float, float, float]) -> None:
        r, g, b = rgb
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg")

    def fill_rect(self, x: float, y: float, w: float, h: float, rgb: tuple[float, float, float]) -> None:
        self.fill_rgb(rgb)
        self._ops.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def stroke_line(self, x1: float, y1: float, x2: float, y2: float, rgb: tuple[float, float, float], width: float = 1) -> None:
        self.stroke_rgb(rgb, width)
        self._ops.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = 10,
        font: str = "F1",
        rgb: tuple[float, float, float] = COLOR_TEXT,
    ) -> None:
        r, g, b = rgb
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        self._ops.append("BT")
        self._ops.append(f"/{font} {size:.1f} Tf")
        self._ops.append(f"{x:.2f} {y:.2f} Td")
        self._ops.append(f"({_escape_pdf(text)}) Tj")
        self._ops.append("ET")

    def tbmc_logo(self, x: float, y: float, size: float = 40) -> None:
        """Vector mark matching site header: circle + square."""
        scale = size / 28.0
        cx = x + 14 * scale
        cy = y + 14 * scale
        r = 13 * scale
        sq = 14 * scale
        sq_x = x + 7 * scale
        sq_y = y + 7 * scale

        self.stroke_rgb(COLOR_TEXT, 1.4 * scale)
        self._ops.extend(_circle_bezier(cx, cy, r))
        self._ops.append("S")

        self.fill_rgb(COLOR_TEXT)
        self._ops.append(f"{sq_x:.2f} {sq_y:.2f} {sq:.2f} {sq:.2f} re f")


def render_certificate_pdf(credential: dict) -> bytes:
    """Build a single-page branded compliance certificate."""
    issued_to = credential.get("issued_to") or {}
    scope = credential.get("allowed_scope") or {}
    criteria = credential.get("criteria_checked") or {}
    volume = credential.get("declared_monthly_volume_usd") or {}

    sig = credential.get("signature") or ""
    sig_short = f"{sig[:28]}...{sig[-16:]}" if len(sig) > 48 else sig

    status = str(credential.get("compliance_status", "")).upper()
    confidence = credential.get("confidence_score")
    conf_pct = f"{int(round(float(confidence) * 100))}%" if confidence is not None else "-"

    c = _PdfCanvas()

    # Page background + border frame
    c.fill_rect(0, 0, PAGE_W, PAGE_H, COLOR_BG)
    c.stroke_rgb(COLOR_BORDER, 1)
    c._ops.append(f"{MARGIN - 8:.2f} {MARGIN - 8:.2f} {PAGE_W - 2 * MARGIN + 16:.2f} {PAGE_H - 2 * MARGIN + 16:.2f} re S")

    # Header
    header_y = PAGE_H - MARGIN - 40
    c.tbmc_logo(MARGIN, header_y, 40)
    c.text(MARGIN + 52, header_y + 26, "The Better Money Company", size=14, font="F2")
    c.text(MARGIN + 52, header_y + 8, "Clearinghouse Compliance Agent", size=9, font="F1", rgb=COLOR_MUTED)

    rule_y = header_y - 14
    c.stroke_line(MARGIN, rule_y, PAGE_W - MARGIN, rule_y, COLOR_GOLD, 2)

    # Title block
    title_y = rule_y - 36
    c.text(MARGIN, title_y, "Compliance Certificate", size=22, font="F2")
    c.text(MARGIN, title_y - 22, "Network admission credential", size=11, font="F1", rgb=COLOR_MUTED)

    # Status badge
    badge_x = PAGE_W - MARGIN - 118
    badge_y = title_y - 4
    c.fill_rect(badge_x, badge_y - 18, 118, 28, COLOR_PASS if status == "PASSED" else (0.85, 0.85, 0.85))
    c.text(badge_x + 14, badge_y - 2, status or "PENDING", size=11, font="F2", rgb=(1, 1, 1) if status == "PASSED" else COLOR_TEXT)

    # Two-column details
    col1_x = MARGIN
    col2_x = PAGE_W / 2 + 8
    row_y = title_y - 58
    line_h = 17

    def row(label: str, value: str, x: float, y: float) -> float:
        c.text(x, y, label, size=8, font="F1", rgb=COLOR_MUTED)
        c.text(x, y - 11, value or "-", size=10, font="F2")
        return y - line_h - 8

    row_y = row("ISSUED TO", issued_to.get("legal_name", ""), col1_x, row_y)
    row_y = row("EIN", issued_to.get("ein", ""), col1_x, row_y)
    row_y = row("ENTITY FILE NUMBER", issued_to.get("entity_file_number") or "Not on file", col1_x, row_y)

    row_y2 = title_y - 58
    row_y2 = row("ISSUED BY", ISSUER_NAME, col2_x, row_y2)
    row_y2 = row("ISSUANCE DATE", _fmt_date(credential.get("issuance_date", "")), col2_x, row_y2)
    row_y2 = row("EXPIRY DATE", _fmt_date(credential.get("expiry_date", "")), col2_x, row_y2)
    row_y2 = row("CONFIDENCE SCORE", conf_pct, col2_x, row_y2)

    section_y = min(row_y, row_y2) - 18
    c.stroke_line(MARGIN, section_y, PAGE_W - MARGIN, section_y, COLOR_BORDER, 0.75)

    # Approved scope
    scope_y = section_y - 28
    c.text(MARGIN, scope_y, "Approved scope", size=12, font="F2")
    scope_y -= 22
    scope_y = row(
        "EXPECTED MONTHLY VOLUME (USD)",
        f"{_fmt_usd(float(volume.get('low', 0)))} to {_fmt_usd(float(volume.get('high', 0)))}",
        col1_x,
        scope_y,
    )
    scope_y = row("CREDIT LIMIT (USD)", _fmt_usd(float(scope.get("credit_limit_usd", 0))), col1_x, scope_y)
    row(
        "APPROVED ASSETS",
        ", ".join(scope.get("approved_asset_classes") or ["USDC"]),
        col2_x,
        scope_y + line_h + 8,
    )
    row(
        "COUNTERPARTY TYPE",
        _ascii_safe(str(scope.get("approved_counterparty_type", "")).replace("_", " ")),
        col2_x,
        scope_y,
    )

    # Criteria checklist
    crit_y = scope_y - 36
    c.text(MARGIN, crit_y, "Verification criteria attested", size=12, font="F2")
    c.text(MARGIN, crit_y - 14, "Selective disclosure only. Raw documents are not included.", size=8, font="F1", rgb=COLOR_MUTED)

    checks = [
        ("Entity verified", criteria.get("entity_verified")),
        ("EIN confirmed", criteria.get("ein_confirmed")),
        ("Good standing active", criteria.get("good_standing_active")),
        ("Address match", criteria.get("address_match")),
        ("Beneficial ownership disclosed", criteria.get("beneficial_ownership_disclosed")),
        ("Sanctions clear", criteria.get("sanctions_clear")),
        ("Business purpose verified", criteria.get("business_purpose_verified")),
    ]

    item_y = crit_y - 38
    col_a = MARGIN
    col_b = PAGE_W / 2 + 8
    for i, (label, ok) in enumerate(checks):
        x = col_a if i % 2 == 0 else col_b
        if i % 2 == 0 and i > 0:
            item_y -= 20
        mark = "Yes" if ok else "No"
        color = COLOR_PASS if ok else COLOR_MUTED
        c.text(x, item_y, mark, size=9, font="F2", rgb=color)
        c.text(x + 28, item_y, label, size=9, font="F1")

    # Footer / cryptographic reference
    foot_y = MARGIN + 72
    c.stroke_line(MARGIN, foot_y + 52, PAGE_W - MARGIN, foot_y + 52, COLOR_BORDER, 0.75)
    c.text(MARGIN, foot_y + 32, "Credential reference", size=9, font="F2", rgb=COLOR_MUTED)
    c.text(MARGIN, foot_y + 16, f"ID: {credential.get('credential_id', '')}", size=8, font="F1", rgb=COLOR_MUTED)
    c.text(MARGIN, foot_y + 2, f"Signing key: {credential.get('signing_key_id', '')}", size=8, font="F1", rgb=COLOR_MUTED)

    sig_wrapped = re.sub(r"(.{64})", r"\1 ", sig_short).strip()
    c.text(PAGE_W / 2, foot_y + 16, f"Ed25519 signature: {sig_wrapped[:90]}", size=7, font="F1", rgb=COLOR_MUTED)

    c.text(
        MARGIN,
        MARGIN + 14,
        "Verify using the signed JSON credential and the clearinghouse public key.",
        size=7,
        font="F1",
        rgb=COLOR_MUTED,
    )
    c.text(
        MARGIN,
        MARGIN,
        "Simulated x401 credential issuance per published protocol spec. Not integrated with the live Proof SDK.",
        size=7,
        font="F1",
        rgb=COLOR_MUTED,
    )

    stream = "\n".join(c.save()).encode("latin-1", errors="replace")
    stream_len = len(stream)

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>endobj\n"
    )
    objects.append(f"4 0 obj<< /Length {stream_len} >>stream\n".encode() + stream + b"\nendstream\nendobj\n")
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
    objects.append(b"6 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>endobj\n")

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
    return header + body + "".join(xref).encode() + trailer.encode()
