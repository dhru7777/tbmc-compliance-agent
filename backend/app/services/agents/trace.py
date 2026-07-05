"""Agent activity trace — think / act / observe steps for UI and audit."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Awaitable

StepCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class TraceStep:
    type: str  # think | act | observe | complete | error
    agent: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentTrace:
    def __init__(self, on_step: StepCallback | None = None) -> None:
        self.steps: list[TraceStep] = []
        self._on_step = on_step

    async def emit(
        self,
        step_type: str,
        agent: str,
        message: str,
        **detail: Any,
    ) -> None:
        step = TraceStep(type=step_type, agent=agent, message=message, detail=detail)
        self.steps.append(step)
        if self._on_step:
            result = self._on_step(step.to_dict())
            if result is not None and hasattr(result, "__await__"):
                await result
