"""Stored DB — persist ONLY the signed KYB credential, never raw uploads."""

import json
from pathlib import Path

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"


def save_credential(session_id: str, credential: dict, pdf_bytes: bytes | None = None) -> str:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "kyb_credential.json"
    json_path.write_text(json.dumps(credential, indent=2), encoding="utf-8")
    if pdf_bytes:
        (folder / "compliance_certificate.pdf").write_bytes(pdf_bytes)
    return str(json_path)


def load_credential(session_id: str) -> dict | None:
    path = RECORDS_DIR / session_id / "kyb_credential.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_certificate_pdf(session_id: str) -> bytes | None:
    path = RECORDS_DIR / session_id / "compliance_certificate.pdf"
    if not path.exists():
        return None
    return path.read_bytes()
