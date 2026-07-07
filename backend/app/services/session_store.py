"""Persist KYB session state to disk so uvicorn reload does not drop in-memory sessions."""

import json
from pathlib import Path

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"


def _session_path(session_id: str) -> Path:
    return RECORDS_DIR / session_id / "session.json"


def save_session(session: dict) -> None:
    path = _session_path(session["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")


def load_session(session_id: str) -> dict | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def uploads_dir(session_id: str) -> Path:
    return RECORDS_DIR / session_id / "uploads"


def save_upload(session_id: str, filename: str, content: bytes) -> None:
    folder = uploads_dir(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    safe = Path(filename).name
    (folder / safe).write_bytes(content)


def load_stored_uploads(session_id: str) -> list[tuple[str, str, bytes]]:
    folder = uploads_dir(session_id)
    if not folder.is_dir():
        return []
    out: list[tuple[str, str, bytes]] = []
    for path in sorted(folder.iterdir()):
        if path.is_file():
            out.append((path.stem.replace("_", " "), path.name, path.read_bytes()))
    return out
