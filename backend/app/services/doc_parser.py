"""In-memory document parsing — raw bytes are never persisted."""

import asyncio
from io import BytesIO

from app.services import llm_search


def extract_text_from_upload(filename: str, content: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""
    if lower.endswith((".txt", ".md")):
        return content.decode("utf-8", errors="ignore")
    return ""


async def _parse_one(label: str, filename: str, content: bytes) -> dict:
    text = extract_text_from_upload(filename, content)
    extraction = await llm_search.extract_document_claims(
        label,
        text or f"[binary or unreadable file: {filename}]",
    )
    extraction["filename"] = filename
    extraction["text_length"] = len(text)
    return extraction


async def parse_uploads(uploads: list[tuple[str, str, bytes]]) -> list[dict]:
    """Parse each uploaded document via LLM in parallel. File bytes stay in memory only."""
    if not uploads:
        return []
    return list(await asyncio.gather(*[_parse_one(label, filename, content) for label, filename, content in uploads]))
