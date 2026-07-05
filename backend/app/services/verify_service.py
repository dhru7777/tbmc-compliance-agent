"""VERIFY stage — agentic orchestrator + deterministic cross-reference."""

from app.services.agents.orchestrator import run_kyb_pipeline


async def run_verify(session, uploads, *, refresh_public=None, on_step=None):
    """Agentic VERIFY pipeline. Raw document bytes discarded after this call."""
    return await run_kyb_pipeline(session, uploads, on_step=on_step)
