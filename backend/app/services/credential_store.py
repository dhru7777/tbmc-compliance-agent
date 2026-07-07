"""Stored DB — persist ONLY the signed KYB credential, never raw uploads."""

import json
from pathlib import Path

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"

CERTIFICATE_FILES = {
    "compliance": "compliance_certificate.pdf",
    "kyc": "kyc_credential.pdf",
    "kyb": "kyb_credential.pdf",
    "kya": "kya_agent_credential.pdf",
}


def save_credential(session_id: str, credential: dict, pdf_bytes: bytes | None = None) -> str:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "kyb_credential.json"
    json_path.write_text(json.dumps(credential, indent=2), encoding="utf-8")
    if pdf_bytes:
        (folder / CERTIFICATE_FILES["compliance"]).write_bytes(pdf_bytes)
    return str(json_path)


def save_certificate_bundle(session_id: str, pdfs: dict[str, bytes]) -> None:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    for kind, content in pdfs.items():
        filename = CERTIFICATE_FILES.get(kind)
        if filename and content:
            (folder / filename).write_bytes(content)


def load_credential(session_id: str) -> dict | None:
    path = RECORDS_DIR / session_id / "kyb_credential.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_certificate_pdf(session_id: str, kind: str = "compliance") -> bytes | None:
    filename = CERTIFICATE_FILES.get(kind, CERTIFICATE_FILES["compliance"])
    path = RECORDS_DIR / session_id / filename
    if not path.exists():
        return None
    return path.read_bytes()
