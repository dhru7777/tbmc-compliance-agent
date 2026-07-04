"""Stored DB — persist ONLY the signed KYB credential, never raw uploads."""

import json
from pathlib import Path

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"


def save_credential(session_id: str, credential: dict) -> str:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "kyb_credential.json"
    path.write_text(json.dumps(credential, indent=2), encoding="utf-8")
    return str(path)


def load_credential(session_id: str) -> dict | None:
    path = RECORDS_DIR / session_id / "kyb_credential.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
