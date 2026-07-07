"""Branded PDF certificates — separate documents for compliance, KYC, KYB, and KYA."""

from __future__ import annotations

from datetime import datetime

from app.services.x401.credential import ISSUER_NAME

COLOR_BG = (245 / 255, 241 / 255, 230 / 255)
COLOR_TEXT = (26 / 255, 26 / 255, 26 / 255)
COLOR_MUTED = (107 / 255, 107 / 255, 107 / 255)
COLOR_BORDER = (212 / 255, 207 / 255, 196 / 255)
COLOR_GOLD = (196 / 255, 160 / 255, 53 / 255)
COLOR_PASS = (22 / 255, 163 / 255, 74 / 255)
COLOR_ACCENT = (30 / 255, 64 / 255, 120 / 255)

PAGE_W = 612
PAGE_H = 792
MARGIN = 54
CONTENT_W = PAGE_W - 2 * MARGIN


def _ascii_safe(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    for src, dst in {
        "\u2014": "-",
        "\u2013": "-",
        "\u2026": "...",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
    }.items():
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


def _short_ref(text: str, *, head: int = 8, tail: int = 6) -> str:
    text = _ascii_safe(text or "").strip()
    if not text:
        return "-"
    if len(text) <= head + tail + 1:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def _chunk_text(text: str, chunk: int = 32) -> list[str]:
    text = _ascii_safe(text or "").strip() or "-"
    return [text[i : i + chunk] for i in range(0, len(text), chunk)] or ["-"]


def _wrap_lines(text: str, max_len: int = 64) -> list[str]:
    text = _ascii_safe(text or "").strip()
    if not text:
        return ["-"]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_len:
            if current:
                lines.append(current)
                current = ""
            for i in range(0, len(word), max_len):
                lines.append(word[i : i + max_len])
            continue
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or ["-"]


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

    def text(self, x: float, y: float, text: str, *, size: float = 10, font: str = "F1", rgb: tuple[float, float, float] = COLOR_TEXT) -> None:
        r, g, b = rgb
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        self._ops.append("BT")
        self._ops.append(f"/{font} {size:.1f} Tf")
        self._ops.append(f"{x:.2f} {y:.2f} Td")
        self._ops.append(f"({_escape_pdf(text)}) Tj")
        self._ops.append("ET")

    def tbmc_logo(self, x: float, y: float, size: float = 36) -> None:
        scale = size / 28.0
        cx = x + 14 * scale
        cy = y + 14 * scale
        r = 13 * scale
        sq = 14 * scale
        self.stroke_rgb(COLOR_TEXT, 1.4 * scale)
        self._ops.extend(_circle_bezier(cx, cy, r))
        self._ops.append("S")
        self.fill_rgb(COLOR_TEXT)
        self._ops.append(f"{x + 7 * scale:.2f} {y + 7 * scale:.2f} {sq:.2f} {sq:.2f} re f")


class _Layout:
    def __init__(self, *, accent: tuple[float, float, float] = COLOR_GOLD) -> None:
        self.accent = accent
        self.pages: list[_PdfCanvas] = []
        self.c = _PdfCanvas()
        self.pages.append(self.c)
        self.y = PAGE_H - MARGIN - 44
        self._page_frame()

    def new_page(self) -> None:
        self.c = _PdfCanvas()
        self.pages.append(self.c)
        self.y = PAGE_H - MARGIN - 44
        self._page_frame()

    def ensure_space(self, needed: float) -> None:
        if self.y < MARGIN + needed:
            self.new_page()

    def _page_frame(self) -> None:
        self.c.fill_rect(0, 0, PAGE_W, PAGE_H, COLOR_BG)
        self.c.stroke_rgb(COLOR_BORDER, 1)
        self.c._ops.append(
            f"{MARGIN - 8:.2f} {MARGIN - 8:.2f} {PAGE_W - 2 * MARGIN + 16:.2f} {PAGE_H - 2 * MARGIN + 16:.2f} re S"
        )

    def header(self, title: str, subtitle: str, *, badge: str = "") -> None:
        top = PAGE_H - MARGIN - 36
        self.c.tbmc_logo(MARGIN, top, 36)
        self.c.text(MARGIN + 48, top + 22, "The Better Money Company", size=13, font="F2")
        self.c.text(MARGIN + 48, top + 6, "Clearinghouse Compliance Agent", size=8, font="F1", rgb=COLOR_MUTED)
        rule_y = top - 10
        self.c.stroke_line(MARGIN, rule_y, PAGE_W - MARGIN, rule_y, self.accent, 2)
        self.y = rule_y - 28
        self.c.text(MARGIN, self.y, title, size=20, font="F2")
        self.y -= 20
        self.c.text(MARGIN, self.y, subtitle, size=10, font="F1", rgb=COLOR_MUTED)
        self.y -= 16
        if badge:
            self.c.fill_rect(PAGE_W - MARGIN - 100, self.y + 4, 100, 22, COLOR_PASS)
            self.c.text(PAGE_W - MARGIN - 86, self.y + 8, badge, size=10, font="F2", rgb=(1, 1, 1))
            self.y -= 8
        self.rule()

    def rule(self) -> None:
        self.y -= 8
        self.c.stroke_line(MARGIN, self.y, PAGE_W - MARGIN, self.y, COLOR_BORDER, 0.75)
        self.y -= 20

    def section(self, title: str) -> None:
        self.y -= 4
        self.c.text(MARGIN, self.y, title, size=12, font="F2")
        self.y -= 18

    def field(self, label: str, value: str) -> None:
        self.ensure_space(40)
        self.c.text(MARGIN, self.y, label.upper(), size=7, font="F1", rgb=COLOR_MUTED)
        self.y -= 12
        for line in _wrap_lines(value, 72):
            self.c.text(MARGIN, self.y, line, size=10, font="F2")
            self.y -= 13
        self.y -= 6

    def field_ref(self, label: str, value: str, *, hint: str = "Full value in signed JSON credential") -> None:
        full = _ascii_safe(value or "").strip()
        self.field(label, _short_ref(full) if full else "-")
        if full and len(full) > 18:
            self.c.text(MARGIN, self.y, hint, size=7, font="F1", rgb=COLOR_MUTED)
            self.y -= 12

    def bullet_list(self, items: list[str], *, max_items: int = 12) -> None:
        for item in items[:max_items]:
            for i, line in enumerate(_wrap_lines(item, 68)):
                prefix = "- " if i == 0 else "  "
                self.c.text(MARGIN + 4, self.y, f"{prefix}{line}", size=9, font="F1")
                self.y -= 12
        if len(items) > max_items:
            self.c.text(MARGIN + 4, self.y, f"... and {len(items) - max_items} more", size=8, font="F1", rgb=COLOR_MUTED)
            self.y -= 12
        self.y -= 4

    def check_grid(self, checks: list[tuple[str, bool]]) -> None:
        col_w = CONTENT_W / 2 - 8
        left_x = MARGIN
        right_x = MARGIN + col_w + 16
        row_h = 16
        for i, (label, ok) in enumerate(checks):
            x = left_x if i % 2 == 0 else right_x
            if i % 2 == 0 and i > 0:
                self.y -= row_h
            mark = "Yes" if ok else "No"
            self.c.text(x, self.y, mark, size=9, font="F2", rgb=COLOR_PASS if ok else COLOR_MUTED)
            self.c.text(x + 26, self.y, label, size=9, font="F1")
        self.y -= row_h + 8

    def crypto_appendix(
        self,
        *,
        cred_id: str,
        signing_key: str,
        signature: str,
        note: str,
        extra_fields: list[tuple[str, str]] | None = None,
    ) -> None:
        """Page 2 — full hashes and signatures, chunked for readability."""
        self.new_page()
        top = PAGE_H - MARGIN - 28
        self.c.text(MARGIN, top, "Cryptographic verification", size=14, font="F2")
        self.c.text(MARGIN, top - 16, "Machine-verifiable references (appendix)", size=8, font="F1", rgb=COLOR_MUTED)
        self.y = top - 40
        self.c.stroke_line(MARGIN, self.y, PAGE_W - MARGIN, self.y, COLOR_BORDER, 0.75)
        self.y -= 22

        def block(title: str, value: str, *, chunk: int = 36) -> None:
            self.ensure_space(56)
            self.c.text(MARGIN, self.y, title.upper(), size=7, font="F1", rgb=COLOR_MUTED)
            self.y -= 12
            for line in _chunk_text(value, chunk):
                self.c.text(MARGIN + 6, self.y, line, size=9, font="F1")
                self.y -= 11
            self.y -= 8

        block("Credential ID", cred_id, chunk=36)
        if signing_key:
            block("Signing key", signing_key, chunk=40)
        if signature:
            block("Ed25519 signature", signature, chunk=48)

        for label, value in extra_fields or []:
            if value:
                block(label, value, chunk=32 if len(value) > 40 else 36)

        if note:
            self.ensure_space(36)
            self.y -= 4
            self.c.stroke_line(MARGIN, self.y, PAGE_W - MARGIN, self.y, COLOR_BORDER, 0.75)
            self.y -= 16
            for line in _wrap_lines(note, 88):
                self.c.text(MARGIN, self.y, line, size=8, font="F1", rgb=COLOR_MUTED)
                self.y -= 11

    def finish(self) -> bytes:
        return _build_pdf_bytes(self.pages)


def _build_pdf_bytes(pages: list[_PdfCanvas]) -> bytes:
    page_count = len(pages)
    objects: list[bytes] = []

    font_f1_obj = 3 + page_count * 2 + 1
    font_f2_obj = font_f1_obj + 1

    page_obj_nums = list(range(3, 3 + page_count))
    content_obj_nums = list(range(3 + page_count, 3 + page_count * 2))

    page_kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(f"2 0 obj<< /Type /Pages /Kids [{page_kids}] /Count {page_count} >>endobj\n".encode())

    for i, canvas in enumerate(pages):
        page_num = page_obj_nums[i]
        content_num = content_obj_nums[i]
        objects.append(
            (
                f"{page_num} 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_num} 0 R /Resources << /Font << /F1 {font_f1_obj} 0 R /F2 {font_f2_obj} 0 R >> >> >>endobj\n"
            ).encode()
        )
        stream = "\n".join(canvas.save()).encode("latin-1", errors="replace")
        objects.append(f"{content_num} 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n")

    objects.append(f"{font_f1_obj} 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n".encode())
    objects.append(f"{font_f2_obj} 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>endobj\n".encode())

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
    xref += [f"{off:010d} 00000 n \n" for off in offsets[1:]]
    trailer = f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    return header + body + "".join(xref).encode() + trailer.encode()


def render_compliance_certificate_pdf(
    credential: dict,
    *,
    master_credential_id: str | None = None,
) -> bytes:
    """Network admission certificate — entity, scope, criteria only."""
    issued_to = credential.get("issued_to") or {}
    scope = credential.get("allowed_scope") or {}
    criteria = credential.get("criteria_checked") or {}
    volume = credential.get("declared_monthly_volume_usd") or {}
    confidence = credential.get("confidence_score")
    conf_pct = f"{int(round(float(confidence) * 100))}%" if confidence is not None else "-"
    status = str(credential.get("compliance_status", "")).upper()

    lay = _Layout()
    lay.header("Compliance Certificate", "Network admission credential", badge=status or "PENDING")

    lay.field("Issued to", issued_to.get("legal_name", ""))
    lay.field("EIN", issued_to.get("ein", ""))
    lay.field("Entity file number", issued_to.get("entity_file_number") or "Not on file")
    lay.field("Issued by", ISSUER_NAME)
    lay.field("Issuance date", _fmt_date(credential.get("issuance_date", "")))
    lay.field("Expiry date", _fmt_date(credential.get("expiry_date", "")))
    lay.field("Confidence score", conf_pct)

    lay.section("Approved scope")
    lay.field(
        "Expected monthly volume (USD)",
        f"{_fmt_usd(float(volume.get('low', 0)))} to {_fmt_usd(float(volume.get('high', 0)))}",
    )
    lay.field("Credit limit (USD)", _fmt_usd(float(scope.get("credit_limit_usd", 0))))
    lay.field("Approved assets", ", ".join(scope.get("approved_asset_classes") or ["USDC"]))
    lay.field("Counterparty type", _ascii_safe(str(scope.get("approved_counterparty_type", "")).replace("_", " ")))

    lay.section("Verification criteria attested")
    lay.check_grid(
        [
            ("Entity verified", criteria.get("entity_verified")),
            ("EIN confirmed", criteria.get("ein_confirmed")),
            ("Good standing active", criteria.get("good_standing_active")),
            ("Address match", criteria.get("address_match")),
            ("Beneficial ownership", criteria.get("beneficial_ownership_disclosed")),
            ("Sanctions clear", criteria.get("sanctions_clear")),
            ("Business purpose", criteria.get("business_purpose_verified")),
        ]
    )

    if master_credential_id:
        lay.section("Related client proof")
        lay.field_ref("Master verification credential (C4)", master_credential_id)
        lay.c.text(MARGIN, lay.y, "Present C4 as x401 proof-of-ownership on the network.", size=8, font="F1", rgb=COLOR_MUTED)
        lay.y -= 20

    lay.c.text(MARGIN, lay.y, "Full cryptographic references are on page 2.", size=8, font="F1", rgb=COLOR_MUTED)
    lay.crypto_appendix(
        cred_id=credential.get("credential_id", ""),
        signing_key=credential.get("signing_key_id", ""),
        signature=credential.get("signature", ""),
        note="Verify using the signed JSON credential and the clearinghouse public key.",
        extra_fields=[("Client master credential (C4)", master_credential_id or "")],
    )
    return lay.finish()


def render_kyc_credential_pdf(c1: dict, layered: dict | None = None) -> bytes:
    """C1 — individual identity / control-person verification."""
    issued = c1.get("issued_to") or {}
    docs = c1.get("documents_verified") or []

    lay = _Layout(accent=COLOR_ACCENT)
    lay.header("KYC Verification Credential", "Tier C1 — identity and control person", badge="C1")

    lay.field("Primary subject", issued.get("primary_subject", ""))
    control = issued.get("control_persons") or []
    if control:
        names = [f"{p.get('name', '')} ({p.get('title', '')})" for p in control]
        lay.field("Control person(s)", "; ".join(names))

    lay.field("Issuance date", _fmt_date(c1.get("issuance_date", "")))
    lay.field("Expiry date", _fmt_date(c1.get("expiry_date", "")))
    lay.field_ref("Session ID", c1.get("session_id", ""))

    lay.section("Documents verified (KYC)")
    lay.c.text(MARGIN, lay.y, f"{len(docs)} document(s) — private identity records", size=8, font="F1", rgb=COLOR_MUTED)
    lay.y -= 14
    lay.bullet_list([f"{d.get('label', d.get('document_id', 'document'))} [{d.get('visibility', 'private')}]" for d in docs])

    lay.section("Checklist attestation")
    for item in c1.get("checklist_items") or []:
        mark = item.get("result", "")
        lay.c.text(MARGIN, lay.y, f"{mark}: {item.get('item', '')}", size=9, font="F1")
        lay.y -= 13

    lay.c.text(MARGIN, lay.y, "Full cryptographic references are on page 2.", size=8, font="F1", rgb=COLOR_MUTED)
    lay.crypto_appendix(
        cred_id=c1.get("credential_id", ""),
        signing_key=c1.get("signing_key_id", ""),
        signature=c1.get("signature", ""),
        note="Independent KYC credential — verifiable separately from KYB or master proof.",
    )
    return lay.finish()


def render_kyb_credential_pdf(c2: dict, layered: dict | None = None) -> bytes:
    """C2 — business entity verification."""
    issued = c2.get("issued_to") or {}
    docs = c2.get("documents_verified") or []
    cat = (layered or {}).get("document_categorization") or {}
    summary = cat.get("summary") or {}

    lay = _Layout(accent=(22 / 255, 101 / 255, 52 / 255))
    lay.header("KYB Verification Credential", "Tier C2 — business entity", badge="C2")

    lay.field("Legal name", issued.get("legal_name", ""))
    lay.field("EIN", issued.get("ein", ""))
    lay.field("State", issued.get("state", ""))
    owners = issued.get("beneficial_owners") or []
    if owners:
        lay.field("Beneficial owners", "; ".join(f"{o.get('name')} ({o.get('ownership_pct')}%)" for o in owners))

    lay.field("Issuance date", _fmt_date(c2.get("issuance_date", "")))
    lay.field("Expiry date", _fmt_date(c2.get("expiry_date", "")))
    lay.field_ref("Session ID", c2.get("session_id", ""))

    if summary:
        lay.section("Document summary")
        lay.field(
            "Categorized uploads",
            f"{summary.get('kyb_count', 0)} KYB documents "
            f"({summary.get('public_count', 0)} public, {summary.get('private_count', 0)} private)",
        )

    lay.section("Documents verified (KYB)")
    lay.bullet_list([f"{d.get('label', d.get('document_id', 'document'))} [{d.get('visibility', '')}]" for d in docs])

    lay.section("Checklist attestation")
    for item in c2.get("checklist_items") or []:
        lay.c.text(MARGIN, lay.y, f"{item.get('result', '')}: {item.get('item', '')}", size=9, font="F1")
        lay.y -= 13

    creds = (layered or {}).get("credentials") or {}
    c3 = creds.get("C3") or {}
    c4 = creds.get("C4") or {}
    if c3.get("credential_id") or c4.get("credential_id"):
        lay.section("Related credentials")
        if c3.get("credential_id"):
            lay.field_ref("Combined KYC+KYB (C3)", c3.get("credential_id", ""))
        if c4.get("credential_id"):
            lay.field_ref("Master proof (C4)", c4.get("credential_id", ""))

    lay.c.text(MARGIN, lay.y, "Full cryptographic references are on page 2.", size=8, font="F1", rgb=COLOR_MUTED)
    lay.crypto_appendix(
        cred_id=c2.get("credential_id", ""),
        signing_key=c2.get("signing_key_id", ""),
        signature=c2.get("signature", ""),
        note="Independent KYB credential — business entity proof for network transfer.",
        extra_fields=[
            ("Combined credential (C3)", c3.get("credential_id", "")),
            ("Master credential (C4)", c4.get("credential_id", "")),
        ],
    )
    return lay.finish()


def render_kya_credential_pdf(kya_proof: dict) -> bytes:
    """KYA — agent auditability proof (agent_id + session + audit.md binding)."""
    cred = kya_proof.get("credential") or {}
    llm = cred.get("llm_calls") or {}
    trace = cred.get("agent_trace") or {}
    outcome = cred.get("verification_outcome") or {}

    lay = _Layout(accent=(88 / 255, 55 / 255, 130 / 255))
    lay.header("KYA Agent Proof Credential", "Know Your Agent — verification auditability", badge="KYA")

    lay.field_ref("Agent ID", kya_proof.get("agent_id") or cred.get("agent_id", ""))
    lay.field_ref("Session ID", kya_proof.get("session_id") or cred.get("session_id", ""))
    lay.field_ref("Enterprise ID", kya_proof.get("enterprise_id") or cred.get("enterprise_id", ""))
    lay.field("Issuance date", _fmt_date(cred.get("issuance_date", "")))
    lay.field("Expiry date", _fmt_date(cred.get("expiry_date", "")))

    lay.section("Verification outcome")
    lay.field("KYB status", outcome.get("kyb_status", ""))
    lay.field("Confidence score", str(outcome.get("confidence_score", "")))
    if cred.get("client_master_credential_id"):
        lay.field_ref("Client master credential (C4)", cred.get("client_master_credential_id", ""))

    lay.section("Audit trail binding")
    audit_hash = kya_proof.get("audit_md_sha256") or cred.get("audit_md_sha256", "")
    lay.field_ref("audit.md SHA-256", audit_hash, hint="Full hash on page 2")
    lay.c.text(MARGIN, lay.y, "Full audit record: session trail, LLM calls, and agent trace.", size=8, font="F1", rgb=COLOR_MUTED)
    lay.y -= 16

    lay.section("Agent activity summary")
    lay.field("Live LLM API calls", str(llm.get("live_api_calls", 0)))
    lay.field("Total input tokens", f"{llm.get('total_input_tokens', 0):,}")
    lay.field("Total output tokens", f"{llm.get('total_output_tokens', 0):,}")
    lay.field("Total cost (USD)", str(llm.get("total_cost_usd", 0)))
    lay.field("Agent trace steps", str(trace.get("step_count", 0)))

    lay.c.text(MARGIN, lay.y, "Full cryptographic references are on page 2.", size=8, font="F1", rgb=COLOR_MUTED)
    lay.crypto_appendix(
        cred_id=cred.get("credential_id", ""),
        signing_key=cred.get("signing_key_id", ""),
        signature=cred.get("signature", ""),
        note="KYA proof = agent_id + session_id + audit.md credential. Proof of agent ownership and verification.",
        extra_fields=[
            ("audit.md SHA-256", audit_hash),
            ("Client master credential (C4)", cred.get("client_master_credential_id", "")),
        ],
    )
    return lay.finish()


def render_certificate_pdf(
    credential: dict,
    layered_credentials: dict | None = None,
    kya_proof: dict | None = None,
) -> bytes:
    """Backward-compatible alias — compliance admission PDF only."""
    c4_id = None
    if layered_credentials:
        c4_id = (layered_credentials.get("credentials") or {}).get("C4", {}).get("credential_id")
    return render_compliance_certificate_pdf(credential, master_credential_id=c4_id)


def render_all_certificate_pdfs(
    credential: dict,
    layered_credentials: dict | None = None,
    kya_proof: dict | None = None,
) -> dict[str, bytes]:
    """Generate all four PDF documents for a passed verification."""
    creds = (layered_credentials or {}).get("credentials") or {}
    c1 = creds.get("C1") or {}
    c2 = creds.get("C2") or {}
    c4_id = (creds.get("C4") or {}).get("credential_id")

    out: dict[str, bytes] = {
        "compliance": render_compliance_certificate_pdf(credential, master_credential_id=c4_id),
    }
    if c1:
        out["kyc"] = render_kyc_credential_pdf(c1, layered_credentials)
    if c2:
        out["kyb"] = render_kyb_credential_pdf(c2, layered_credentials)
    if kya_proof:
        out["kya"] = render_kya_credential_pdf(kya_proof)
    return out
