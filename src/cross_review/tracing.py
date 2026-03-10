"""Run tracing and progress events. Ref: design doc \u00a718."""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from cross_review.schemas import BuilderResult, Trace, TokenUsage


class RunTracer:  # pylint: disable=too-many-instance-attributes
    """Accumulates trace data during an orchestration run."""

    def __init__(self, request_id: str, on_event: Optional[Callable[[str], None]] = None):
        """Initialize the tracer for a single orchestration run.

        Args:
            request_id: Unique identifier for the current request.
            on_event: Optional callback for emitting progress events.
        """
        self.request_id = request_id
        self._on_event = on_event or self._default_event
        self._calls: int = 0
        self._tokens: int = 0
        self._providers: list[str] = []
        self._warnings: list[str] = []
        self._builder_result: Optional[BuilderResult] = None
        self._retries: int = 0
        self._degraded: bool = False
        self._start_time: float = time.monotonic()

    def emit(self, event: str) -> None:
        """Emit a progress event via the configured callback.

        Args:
            event: Description of the event.
        """
        self._on_event(event)

    def record_call(self, provider: str, usage: TokenUsage) -> None:
        """Record a completed provider call and its token usage.

        Args:
            provider: Name of the provider that was called.
            usage: Token usage statistics from the call.
        """
        self._calls += 1
        self._tokens += usage.total_tokens
        if provider not in self._providers:
            self._providers.append(provider)

    def record_builder_result(self, result: BuilderResult) -> None:
        """Store the builder result for inclusion in the final trace.

        Args:
            result: The validated builder output.
        """
        self._builder_result = result

    def record_retry(self) -> None:
        """Increment the retry counter."""
        self._retries += 1

    def record_warning(self, warning: str) -> None:
        """Append a warning message to the trace.

        Args:
            warning: Human-readable warning string.
        """
        self._warnings.append(warning)

    def mark_degraded(self) -> None:
        """Mark this run as degraded (one or more reviewers failed)."""
        self._degraded = True

    def elapsed_seconds(self) -> float:
        """Return wall-clock seconds elapsed since the run started.

        Returns:
            Elapsed time in seconds.
        """
        return time.monotonic() - self._start_time

    def to_trace(self) -> Trace:
        """Build a Trace snapshot from accumulated data.

        Returns:
            A Trace model with current counters and recorded data.
        """
        return Trace(
            total_calls=self._calls,
            total_tokens_actual=self._tokens,
            providers_used=self._providers,
            builder_result=self._builder_result,
            warnings=self._warnings,
        )

    @staticmethod
    def _default_event(event: str) -> None:
        """Print a progress event to stderr when no callback is configured.

        Args:
            event: Description of the event.
        """
        print(f"[cross-review] {event}", file=sys.stderr)
