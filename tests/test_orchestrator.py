"""Tests for the core orchestrator. Ref: design doc §8, §16."""

from unittest.mock import AsyncMock, MagicMock

from cross_review.config import AppConfig, RoleConfig
from cross_review.orchestrator import Orchestrator, RawReviewerOutput
from cross_review.schemas import (
    BuilderResult,
    Confidence,
    FinalResult,
    Mode,
    ReviewRequest,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUILDER_RESULT = BuilderResult(
    summary="Use modular backend",
    recommendation="Start with FastAPI monolith",
    assumptions=["Small team"],
    alternatives=["Microservices"],
    risks=["Scaling"],
    open_questions=["Team size?"],
    confidence=Confidence.MEDIUM,
)

_REVIEWER_OUTPUT = RawReviewerOutput(
    overall_confidence="medium",
    findings=[
        {
            "category": "correctness",
            "severity": "medium",
            "target": "API design",
            "summary": "Missing input validation on user endpoint",
            "quote": None,
            "shortcut_risk": False,
            "rationale": "The endpoint accepts arbitrary payloads without schema checks",
            "recommendation": "Add Pydantic validation",
            "confidence": "medium",
        }
    ],
)

_TOKEN_USAGE = TokenUsage(input_tokens=100, output_tokens=200, total_tokens=300)


def _make_mock_provider_factory(fail_reviewer_index: int | None = None):
    """Return a factory that produces mock providers.

    The factory tracks how many times it is called.  The first factory call
    returns a "builder" provider (always returns BuilderResult).  Subsequent
    factory calls return "reviewer" providers.

    If fail_reviewer_index is set, the N-th reviewer provider (0-based) will
    always raise ConnectionError on every call, so that even retries fail.
    """
    factory_counter = {"n": 0}
    reviewer_factory_counter = {"n": 0}

    def factory(provider_name: str, model: str):
        idx = factory_counter["n"]
        factory_counter["n"] += 1
        provider = MagicMock()
        provider.name.return_value = f"{provider_name}:{model}"

        if idx == 0:
            # Builder provider
            async def builder_call(system_prompt, user_prompt, response_schema, **kwargs):
                return (_BUILDER_RESULT, _TOKEN_USAGE)

            provider.call = AsyncMock(side_effect=builder_call)
        else:
            # Reviewer provider
            rev_idx = reviewer_factory_counter["n"]
            reviewer_factory_counter["n"] += 1

            if fail_reviewer_index is not None and rev_idx == fail_reviewer_index:

                async def failing_call(system_prompt, user_prompt, response_schema, **kwargs):
                    raise ConnectionError("simulated reviewer failure")

                provider.call = AsyncMock(side_effect=failing_call)
            else:

                async def reviewer_call(system_prompt, user_prompt, response_schema, **kwargs):
                    return (_REVIEWER_OUTPUT, _TOKEN_USAGE)

                provider.call = AsyncMock(side_effect=reviewer_call)

        return provider

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFastMode:
    """Fast mode: 1 call (builder only), returns builder result."""

    async def test_fast_mode_single_call(self):
        config = AppConfig()
        events: list[str] = []
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            question="name a variable",
            mode=Mode.FAST,
        )
        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        assert result.mode == Mode.FAST
        assert result.trace.total_calls == 1
        assert result.trace.builder_result is not None
        assert result.trace.builder_result.recommendation == "Start with FastAPI monolith"
        # No reviewer findings in fast mode
        assert result.consensus_findings == []
        assert result.selected_roles == []

    async def test_fast_mode_returns_builder_confidence(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(question="quick q", mode=Mode.FAST)
        result = await orch.run(request)

        assert result.confidence == Confidence.MEDIUM


class TestReviewMode:
    """Review mode: 2 calls (builder + 1 reviewer)."""

    async def test_review_mode_two_calls(self):
        config = AppConfig()
        events: list[str] = []
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            question="Review this backend plan for our API",
            mode=Mode.REVIEW,
        )
        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        assert result.mode == Mode.REVIEW
        assert result.trace.total_calls == 2
        # Only skeptic reviewer in review mode
        assert len(result.selected_roles) == 1
        assert result.selected_roles[0].value == "skeptic"

    async def test_review_mode_has_findings(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review this backend plan for our API",
            mode=Mode.REVIEW,
        )
        result = await orch.run(request)

        # In review mode, all findings are primary (consensus)
        assert len(result.consensus_findings) >= 1

    async def test_review_mode_trace_has_providers(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review this backend plan",
            mode=Mode.REVIEW,
        )
        result = await orch.run(request)

        assert len(result.trace.providers_used) >= 1


class TestArbitrationMode:
    """Arbitration mode: 3 calls (builder + 2 reviewers)."""

    async def test_arbitration_mode_three_calls(self):
        config = AppConfig()
        events: list[str] = []
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            question="Review this auth migration plan for production security",
            mode=Mode.ARBITRATION,
        )
        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        assert result.mode == Mode.ARBITRATION
        assert result.trace.total_calls == 3
        # Both skeptic and pragmatist reviewers
        assert len(result.selected_roles) == 2

    async def test_arbitration_parallel_reviewers(self):
        """Verify multiple reviewer roles are used in arbitration."""
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review security architecture",
            mode=Mode.ARBITRATION,
        )
        result = await orch.run(request)

        role_values = {r.value for r in result.selected_roles}
        assert "skeptic" in role_values
        assert "pragmatist" in role_values


class TestPartialFailure:
    """Partial failure: one reviewer fails, continue with degraded output."""

    async def test_partial_reviewer_failure_continues(self):
        config = AppConfig()
        events: list[str] = []
        # The second reviewer (index 1) will fail
        factory = _make_mock_provider_factory(fail_reviewer_index=1)
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            question="Review auth migration plan",
            mode=Mode.ARBITRATION,
        )
        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        # Should still produce a result despite one reviewer failing
        assert result.mode == Mode.ARBITRATION
        # At least the successful reviewer should be present
        assert len(result.selected_roles) >= 1
        # Trace should have a warning about the failure
        assert any("degraded" in w.lower() or "fail" in w.lower() for w in result.trace.warnings)


class TestBudgetTracking:
    """Budget guard is used to track calls and tokens."""

    async def test_token_tracking(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review this plan",
            mode=Mode.REVIEW,
        )
        result = await orch.run(request)

        # 2 calls x 300 tokens each = 600
        assert result.trace.total_tokens_actual == 600
        assert result.trace.total_calls == 2


class TestProgressEvents:
    """Progress events are emitted via the on_event callback."""

    async def test_events_emitted(self):
        config = AppConfig()
        events: list[str] = []
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            question="Review this backend plan",
            mode=Mode.REVIEW,
        )
        await orch.run(request)

        # Should have emitted at least routing, builder, reviewer events
        assert len(events) >= 3


class TestMaxReviewersBudget:
    """Budget max_reviewers limits reviewer count in arbitration."""

    async def test_max_reviewers_limits_roles(self):
        config = AppConfig()
        # Add a third reviewer role to the config
        config.roles["security_reviewer"] = RoleConfig(
            provider="claude", model="claude-sonnet-4-5-20250514"
        )
        # But budget only allows max_reviewers=2
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review auth plan",
            mode=Mode.ARBITRATION,
            budget=request_budget_with_max_reviewers(2),
        )
        result = await orch.run(request)

        # Should have at most 2 reviewers despite 3 being configured
        assert len(result.selected_roles) <= 2


def request_budget_with_max_reviewers(n: int):
    from cross_review.schemas import BudgetConfig

    return BudgetConfig(max_reviewers=n, max_total_calls=10)
