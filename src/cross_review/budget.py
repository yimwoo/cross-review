"""Budget guard for controlling orchestration cost. Ref: design doc \u00a715."""

from __future__ import annotations

from cross_review.schemas import BudgetConfig, TokenUsage


class BudgetExceeded(Exception):
    """Raised when hard budget limits are exceeded."""


class BudgetGuard:
    """Tracks cumulative usage and enforces limits. Ref: \u00a715.2."""

    def __init__(self, config: BudgetConfig):
        """Initialize the budget guard.

        Args:
            config: Budget configuration with call and token limits.
        """
        self._config = config
        self._total_calls = 0
        self._total_tokens = 0

    @property
    def total_calls(self) -> int:
        """Return the total number of recorded LLM calls."""
        return self._total_calls

    @property
    def total_tokens(self) -> int:
        """Return the total number of consumed tokens."""
        return self._total_tokens

    def record_call(self, usage: TokenUsage) -> None:
        """Record a completed LLM call and its token usage.

        Args:
            usage: Token usage statistics from the call.
        """
        self._total_calls += 1
        self._total_tokens += usage.total_tokens

    def can_call(self) -> bool:
        """Check whether another LLM call is allowed within budget.

        Returns:
            True if both call count and token usage are within limits.
        """
        if self._total_calls >= self._config.max_total_calls:
            return False
        if self._total_tokens >= self._config.hard_token_limit:
            return False
        return True

    def is_over_soft_limit(self) -> bool:
        """Check whether the soft token limit has been exceeded.

        Returns:
            True if accumulated tokens meet or exceed the soft limit.
        """
        return self._total_tokens >= self._config.soft_token_limit

    def can_add_reviewer(self, current_count: int) -> bool:
        """Check whether another reviewer can be added within budget.

        Args:
            current_count: Number of reviewers already selected.

        Returns:
            True if adding one more reviewer stays within max_reviewers.
        """
        return current_count < self._config.max_reviewers
