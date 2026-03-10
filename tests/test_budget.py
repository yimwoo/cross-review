"""Tests for budget guard. Ref: design doc §15."""

from cross_review.budget import BudgetGuard
from cross_review.schemas import BudgetConfig, TokenUsage


class TestBudgetGuard:
    def test_allows_calls_within_budget(self):
        config = BudgetConfig(max_total_calls=4, hard_token_limit=30000)
        guard = BudgetGuard(config)
        guard.record_call(TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500))
        assert guard.can_call() is True

    def test_blocks_when_calls_exceeded(self):
        config = BudgetConfig(max_total_calls=2)
        guard = BudgetGuard(config)
        guard.record_call(TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150))
        guard.record_call(TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150))
        assert guard.can_call() is False

    def test_soft_limit_warning(self):
        config = BudgetConfig(soft_token_limit=1000, hard_token_limit=2000)
        guard = BudgetGuard(config)
        guard.record_call(TokenUsage(input_tokens=800, output_tokens=300, total_tokens=1100))
        assert guard.is_over_soft_limit() is True
        assert guard.can_call() is True

    def test_hard_limit_blocks(self):
        config = BudgetConfig(hard_token_limit=1000)
        guard = BudgetGuard(config)
        guard.record_call(TokenUsage(input_tokens=800, output_tokens=300, total_tokens=1100))
        assert guard.can_call() is False

    def test_max_reviewers_enforced(self):
        config = BudgetConfig(max_reviewers=1)
        guard = BudgetGuard(config)
        assert guard.can_add_reviewer(current_count=0) is True
        assert guard.can_add_reviewer(current_count=1) is False

    def test_tracks_total_tokens(self):
        config = BudgetConfig()
        guard = BudgetGuard(config)
        guard.record_call(TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500))
        guard.record_call(TokenUsage(input_tokens=2000, output_tokens=800, total_tokens=2800))
        assert guard.total_tokens == 4300

    def test_tracks_total_calls(self):
        config = BudgetConfig()
        guard = BudgetGuard(config)
        guard.record_call(TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150))
        guard.record_call(TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150))
        assert guard.total_calls == 2
