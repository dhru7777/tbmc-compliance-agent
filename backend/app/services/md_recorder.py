from datetime import datetime, timezone
from pathlib import Path

RECORDS_DIR = Path(__file__).resolve().parents[2] / "records" / "kyb"


def _session_path(session_id: str) -> Path:
    folder = RECORDS_DIR / session_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "session.md"


def init_session(session_id: str, legal_name: str, state: str) -> Path:
    path = _session_path(session_id)
    if not path.exists():
        now = datetime.now(timezone.utc).isoformat()
        path.write_text(
            f"# KYB Session — {legal_name}\n\n"
            f"**Session ID:** {session_id}  \n"
            f"**State:** {state}  \n"
            f"**Started:** {now}\n",
            encoding="utf-8",
        )
    return path


def append_step(session_id: str, title: str, lines: list[str]) -> Path:
    path = _session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    block = f"\n## {title}\n\n" + "\n".join(lines) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


def read_session(session_id: str) -> str:
    path = _session_path(session_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
