"""Token usage extraction and cost estimation for Anthropic LLM calls."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

# Claude Sonnet 4.6 — override via env for pricing updates
INPUT_USD_PER_MTOK = float(os.getenv("LLM_INPUT_USD_PER_MTOK", "3.0"))
OUTPUT_USD_PER_MTOK = float(os.getenv("LLM_OUTPUT_USD_PER_MTOK", "15.0"))
WEB_SEARCH_USD_PER_SEARCH = float(os.getenv("LLM_WEB_SEARCH_USD", "0.01"))


def cost_logging_enabled() -> bool:
    return os.getenv("KYB_LOG_LLM_COST", "true").lower() in ("1", "true", "yes")


@dataclass
class UsageRecord:
    operation: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    web_search_requests: int = 0
    from_cache: bool = False
    skipped: bool = False
    agent: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def token_cost_usd(self) -> float:
        if self.from_cache or self.skipped:
            return 0.0
        billable_input = self.input_tokens + self.cache_creation_tokens
        billable_input += int(self.cache_read_tokens * 0.1)
        return (
            billable_input * INPUT_USD_PER_MTOK / 1_000_000
            + self.output_tokens * OUTPUT_USD_PER_MTOK / 1_000_000
        )

    @property
    def search_cost_usd(self) -> float:
        return 0.0 if (self.from_cache or self.skipped) else self.web_search_requests * WEB_SEARCH_USD_PER_SEARCH

    @property
    def total_cost_usd(self) -> float:
        return self.token_cost_usd + self.search_cost_usd

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["token_cost_usd"] = round(self.token_cost_usd, 6)
        d["total_cost_usd"] = round(self.total_cost_usd, 6)
        return d


class UsageSession:
    """Accumulates per-call usage for one KYB verification run."""

    def __init__(self, *, log_to_terminal: bool | None = None) -> None:
        self.records: list[UsageRecord] = []
        self.log_to_terminal = cost_logging_enabled() if log_to_terminal is None else log_to_terminal

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)
        if self.log_to_terminal:
            self._print_call(record, index=len(self.records))

    def add_cache(self, operation: str, *, agent: str = "", note: str = "disk cache hit") -> None:
        self.add(
            UsageRecord(
                operation=operation,
                model="—",
                from_cache=True,
                agent=agent,
                notes=[note],
            )
        )

    def add_skip(self, operation: str, *, agent: str = "", note: str = "no API call") -> None:
        self.add(
            UsageRecord(
                operation=operation,
                model="—",
                skipped=True,
                agent=agent,
                notes=[note],
            )
        )

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records if not r.from_cache and not r.skipped)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records if not r.from_cache and not r.skipped)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.total_cost_usd for r in self.records)

    @property
    def live_api_calls(self) -> int:
        return sum(1 for r in self.records if not r.from_cache and not r.skipped)

    def to_dict(self) -> dict[str, Any]:
        by_agent: dict[str, float] = {}
        for r in self.records:
            key = r.agent or "other"
            by_agent[key] = by_agent.get(key, 0.0) + r.total_cost_usd
        return {
            "calls": [r.to_dict() for r in self.records],
            "live_api_calls": self.live_api_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "by_agent_usd": {k: round(v, 6) for k, v in by_agent.items()},
            "pricing": {
                "input_per_mtok": INPUT_USD_PER_MTOK,
                "output_per_mtok": OUTPUT_USD_PER_MTOK,
            },
        }

    def _print_call(self, record: UsageRecord, index: int) -> None:
        prefix = f"[KYB cost #{index}]"
        if record.from_cache:
            print(f"{prefix} {record.operation} — cache ($0.00)")
            return
        if record.skipped:
            print(f"{prefix} {record.operation} — skipped ($0.00) — {record.notes[0] if record.notes else ''}")
            return
        print(
            f"{prefix} {record.operation} | in={record.input_tokens:,} out={record.output_tokens:,} "
            f"| {format_usd(record.total_cost_usd)}"
        )

    def print_run_summary(self, *, session_id: str = "") -> None:
        if not self.log_to_terminal:
            return
        header = "KYB LLM cost summary"
        if session_id:
            header += f" (session {session_id[:8]}…)"
        print(f"\n{'=' * 56}")
        print(header)
        print(f"{'=' * 56}")
        if not self.records:
            print("  No LLM calls recorded.")
            print(f"{'=' * 56}\n")
            return

        for i, r in enumerate(self.records, 1):
            print(f"\n{i}. {r.operation}" + (f" [{r.agent}]" if r.agent else ""))
            print_usage_record(r)

        print(f"\n{'—' * 56}")
        print(f"  Live API calls:   {self.live_api_calls}")
        print(f"  Total in tokens:  {self.total_input_tokens:,}")
        print(f"  Total out tokens: {self.total_output_tokens:,}")
        print(f"  Total cost:       {format_usd(self.total_cost_usd)}")
        if self.records:
            by_op: dict[str, float] = {}
            for r in self.records:
                by_op[r.operation] = by_op.get(r.operation, 0.0) + r.total_cost_usd
            top = max(by_op.items(), key=lambda x: x[1])
            print(f"  Highest cost:     {top[0]} ({format_usd(top[1])})")
        print(f"{'=' * 56}\n")


def _get_attr(obj: Any, key: str, default: Any = 0) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def usage_from_message(message: Any, *, operation: str, model: str) -> UsageRecord:
    """Build a UsageRecord from an Anthropic messages.create response."""
    usage = _get_attr(message, "usage", None)
    server_tool = _get_attr(usage, "server_tool_use", None) if usage else None
    web_searches = int(_get_attr(server_tool, "web_search_requests", 0))

    return UsageRecord(
        operation=operation,
        model=model,
        input_tokens=int(_get_attr(usage, "input_tokens", 0)),
        output_tokens=int(_get_attr(usage, "output_tokens", 0)),
        cache_read_tokens=int(_get_attr(usage, "cache_read_input_tokens", 0)),
        cache_creation_tokens=int(_get_attr(usage, "cache_creation_input_tokens", 0)),
        web_search_requests=web_searches,
    )


def format_usd(amount: float) -> str:
    if amount < 0.0001:
        return f"${amount:.6f}"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.3f}"


def print_usage_record(record: UsageRecord) -> None:
    print(f"  Operation:      {record.operation}")
    if record.from_cache:
        print("  Source:         cache (no API spend)")
        print("  Total:          $0.00")
        for note in record.notes:
            print(f"  Note:           {note}")
        return
    if record.skipped:
        print("  Source:         skipped (no API spend)")
        print("  Total:          $0.00")
        for note in record.notes:
            print(f"  Note:           {note}")
        return
    print(f"  Model:          {record.model}")
    print(f"  Input tokens:   {record.input_tokens:,}")
    print(f"  Output tokens:  {record.output_tokens:,}")
    if record.cache_read_tokens:
        print(f"  Cache read:     {record.cache_read_tokens:,}")
    if record.web_search_requests:
        print(f"  Web searches:   {record.web_search_requests}")
    print(f"  Token cost:     {format_usd(record.token_cost_usd)}")
    if record.web_search_requests:
        print(f"  Search fees:    {format_usd(record.search_cost_usd)}")
    print(f"  Total:          {format_usd(record.total_cost_usd)}")
    for note in record.notes:
        print(f"  Note:           {note}")


def compare_records(records: list[UsageRecord]) -> None:
    live = [r for r in records if not r.from_cache]
    if len(live) < 2:
        return

    by_total = sorted(live, key=lambda r: r.total_cost_usd, reverse=True)
    winner, runner = by_total[0], by_total[1]
    if runner.total_cost_usd <= 0:
        ratio = "∞"
    else:
        ratio = f"{winner.total_cost_usd / runner.total_cost_usd:.1f}x"

    print("\n=== Verdict ===")
    print(f"Higher cost: {winner.operation} ({format_usd(winner.total_cost_usd)})")
    print(f"Lower cost:  {runner.operation} ({format_usd(runner.total_cost_usd)})")
    print(f"Ratio:       {ratio} more expensive")

    combined = sum(r.total_cost_usd for r in live)
    print(f"\nCombined live API cost this run: {format_usd(combined)}")

    doc_records = [r for r in live if "document" in r.operation.lower()]
    search_records = [r for r in live if "public" in r.operation.lower() or "search" in r.operation.lower()]
    if doc_records and search_records:
        docs_total = sum(r.total_cost_usd for r in doc_records)
        search_total = sum(r.total_cost_usd for r in search_records)
        n_docs = len(doc_records)
        print(
            f"\nTypical KYB submit (1 public search + {n_docs} doc{'s' if n_docs != 1 else ''}): "
            f"{format_usd(search_total + docs_total)}"
        )
