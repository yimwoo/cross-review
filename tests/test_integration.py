"""End-to-end integration tests for the full cross-review pipeline.

Tests the complete orchestration flow (routing -> builder -> reviewer(s) ->
reconciliation -> rendering) with mocked provider backends so no real API
calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from cross_review.config import AppConfig
from cross_review.orchestrator import Orchestrator, RawReviewerOutput
from cross_review.rendering import (
    _format_prose,
    render,
    render_json,
    render_markdown,
    render_summary,
)
from cross_review.schemas import (
    BuilderResult,
    Confidence,
    FinalResult,
    Mode,
    ReviewRequest,
    TokenUsage,
    Trace,
)


# ---------------------------------------------------------------------------
# Mock provider factory
# ---------------------------------------------------------------------------

_BUILDER_RESULT = BuilderResult(
    summary="Use a modular monolith with clear domain boundaries",
    recommendation="Start with FastAPI monolith, extract services later",
    assumptions=["Team of 3-5 engineers", "MVP timeline of 8 weeks"],
    alternatives=["Microservices from day one", "Serverless functions"],
    risks=["Tight coupling if boundaries are not enforced early"],
    open_questions=["Expected request volume at launch?"],
    confidence=Confidence.HIGH,
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
            "recommendation": "Add Pydantic request-body validation",
            "confidence": "medium",
        },
        {
            "category": "security",
            "severity": "high",
            "target": "Auth layer",
            "summary": "JWT tokens lack expiry enforcement",
            "quote": None,
            "shortcut_risk": True,
            "rationale": "Tokens without expiry can be replayed indefinitely",
            "recommendation": "Set max token lifetime to 15 minutes",
            "confidence": "high",
        },
    ],
)

_TOKEN_USAGE = TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)


def _make_mock_provider_factory():
    """Return a provider factory that dispatches on response_schema.

    When the orchestrator asks for a BuilderResult schema the mock returns
    a BuilderResult; when it asks for RawReviewerOutput the mock returns
    reviewer findings.  This mirrors real provider behaviour without
    hitting any external API.
    """
    call_log: list[str] = []

    async def mock_call(system_prompt, user_prompt, response_schema, **kwargs):
        usage = TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
        if response_schema is BuilderResult or (
            hasattr(response_schema, "__name__") and "Builder" in response_schema.__name__
        ):
            call_log.append("builder")
            return _BUILDER_RESULT, usage
        else:
            call_log.append("reviewer")
            return _REVIEWER_OUTPUT, usage

    def factory(provider_name: str, model: str):
        mock = MagicMock()
        mock.call = mock_call  # Use the real async function, not AsyncMock
        mock.name.return_value = f"{provider_name}:{model}"
        mock.model_id.return_value = f"{provider_name}:{model}"
        return mock

    factory.call_log = call_log  # type: ignore[attr-defined]
    return factory


# ---------------------------------------------------------------------------
# Review mode (builder + 1 reviewer)
# ---------------------------------------------------------------------------


class TestFullPipelineReviewMode:
    """Full pipeline in REVIEW mode: builder + critic."""

    async def test_review_mode_end_to_end(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        events: list[str] = []
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            request_id="integration-review-001",
            question="Review this backend API design for scalability",
            mode=Mode.REVIEW,
        )
        result = await orch.run(request)

        # Basic structural assertions
        assert isinstance(result, FinalResult)
        assert result.mode == Mode.REVIEW
        assert result.request_id == "integration-review-001"

        # Should have exactly 2 provider calls (builder + 1 reviewer)
        assert result.trace.total_calls == 2

        # Builder result should be stored in trace
        assert result.trace.builder_result is not None
        assert result.trace.builder_result.recommendation == _BUILDER_RESULT.recommendation

        # Selected roles should contain the critic
        assert len(result.selected_roles) >= 1
        role_values = {r.value for r in result.selected_roles}
        assert "critic" in role_values

        # Consensus findings should be populated (review mode promotes all)
        assert len(result.consensus_findings) >= 1

        # Builder model should be set
        assert result.builder_model != ""

        # Reviewer summaries should be populated (1 reviewer in review mode)
        assert len(result.reviewer_summaries) == 1
        rs = result.reviewer_summaries[0]
        assert rs.reviewer_type.value == "critic"
        assert rs.model != ""
        assert rs.verdict != ""
        assert rs.key_concern != ""

        # Final recommendation should start with the builder recommendation prose
        assert result.final_recommendation.startswith(_BUILDER_RESULT.recommendation)
        # Should contain bullet stats
        assert "supporting findings" in result.final_recommendation

        # All three render formats should work without error
        md = render_markdown(result)
        assert "Cross-Review Result" in md
        assert "review" in md.lower()

        # Perspectives table should be present
        assert "Perspectives" in md
        assert "Builder" in md
        assert "Critic" in md

        js = render_json(result)
        parsed = json.loads(js)
        assert parsed["mode"] == "review"
        assert parsed["request_id"] == "integration-review-001"

        summary = render_summary(result)
        assert "[review]" in summary
        assert "confidence=" in summary

    async def test_review_mode_token_tracking(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review this backend API design for scalability",
            mode=Mode.REVIEW,
        )
        result = await orch.run(request)

        # 2 calls x 1500 tokens each = 3000
        assert result.trace.total_tokens_actual == 3000
        assert result.trace.total_calls == 2

    async def test_review_mode_events_emitted(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        events: list[str] = []
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            question="Review this backend API design for scalability",
            mode=Mode.REVIEW,
        )
        await orch.run(request)

        # Should emit routing, builder, reviewer, and orchestration events
        assert len(events) >= 4
        event_text = " ".join(events).lower()
        assert "routing" in event_text
        assert "builder" in event_text
        assert "complete" in event_text


# ---------------------------------------------------------------------------
# Deep mode (builder + 2 reviewers)
# ---------------------------------------------------------------------------


class TestFullPipelineDeepMode:
    """Full pipeline in DEEP mode: builder + 2 reviewers in parallel."""

    async def test_deep_mode_end_to_end(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        events: list[str] = []
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            request_id="integration-arb-001",
            question="Review this authentication migration plan for production security",
            mode=Mode.DEEP,
        )
        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        assert result.mode == Mode.DEEP
        assert result.request_id == "integration-arb-001"

        # Should have exactly 3 provider calls (builder + 2 reviewers)
        assert result.trace.total_calls == 3

        # Both critic and advisor should be selected
        assert len(result.selected_roles) == 2
        role_values = {r.value for r in result.selected_roles}
        assert "critic" in role_values
        assert "advisor" in role_values

        # Builder model should be set
        assert result.builder_model != ""

        # Reviewer summaries for both reviewers
        assert len(result.reviewer_summaries) == 2
        summary_roles = {rs.reviewer_type.value for rs in result.reviewer_summaries}
        assert "critic" in summary_roles
        assert "advisor" in summary_roles

        # Builder result in trace
        assert result.trace.builder_result is not None

        # Final recommendation should start with the builder recommendation prose
        assert result.final_recommendation.startswith(_BUILDER_RESULT.recommendation)
        assert "supporting findings" in result.final_recommendation

        # Token tracking: 3 calls x 1500 = 4500
        assert result.trace.total_tokens_actual == 4500

        # Render all three formats
        md = render_markdown(result)
        assert "Cross-Review Result" in md

        js = render_json(result)
        parsed = json.loads(js)
        assert parsed["mode"] == "deep"

        summary = render_summary(result)
        assert "[deep]" in summary

    async def test_deep_mode_call_log_order(self):
        """Verify the mock factory logged builder first, then reviewers."""
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review this authentication migration for production security",
            mode=Mode.DEEP,
        )
        await orch.run(request)

        log = factory.call_log  # type: ignore[attr-defined]
        assert log[0] == "builder"
        assert log.count("reviewer") == 2

    async def test_deep_consensus_with_multiple_reviewers(self):
        """In deep mode with identical findings from 2 reviewers,
        consensus_strength >= 2 should produce consensus findings."""
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="Review this authentication migration for production security",
            mode=Mode.DEEP,
        )
        result = await orch.run(request)

        # Both reviewers return the same findings, so the reconciler should
        # cluster them and consensus_strength >= 2 should yield consensus.
        assert len(result.consensus_findings) >= 1


# ---------------------------------------------------------------------------
# Fast mode (builder only)
# ---------------------------------------------------------------------------


class TestFullPipelineFastMode:
    """Full pipeline in FAST mode: builder only, no reviewers."""

    async def test_fast_mode_end_to_end(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        events: list[str] = []
        orch = Orchestrator(config, provider_factory=factory, on_event=events.append)

        request = ReviewRequest(
            request_id="integration-fast-001",
            question="name a variable",
            mode=Mode.FAST,
        )
        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        assert result.mode == Mode.FAST
        assert result.request_id == "integration-fast-001"

        # Only 1 call (builder)
        assert result.trace.total_calls == 1

        # No reviewers in fast mode
        assert result.selected_roles == []
        assert result.consensus_findings == []
        assert result.conflicting_findings == []
        assert result.likely_shortcuts == []
        assert result.reviewer_summaries == []
        assert result.builder_model != ""

        # Builder recommendation is used directly as final_recommendation in fast mode
        assert result.final_recommendation == _BUILDER_RESULT.recommendation

        # Builder result in trace
        assert result.trace.builder_result is not None
        assert result.trace.builder_result.confidence == Confidence.HIGH

        # Confidence should match builder confidence
        assert result.confidence == Confidence.HIGH

        # Render all three formats
        md = render_markdown(result)
        assert "Cross-Review Result" in md
        assert "fast" in md.lower()

        js = render_json(result)
        parsed = json.loads(js)
        assert parsed["mode"] == "fast"

        summary = render_summary(result)
        assert "[fast]" in summary

    async def test_fast_mode_single_provider_call(self):
        """Verify only one provider call is recorded."""
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="quick check",
            mode=Mode.FAST,
        )
        await orch.run(request)

        log = factory.call_log  # type: ignore[attr-defined]
        assert len(log) == 1
        assert log[0] == "builder"

    async def test_fast_mode_token_tracking(self):
        config = AppConfig()
        factory = _make_mock_provider_factory()
        orch = Orchestrator(config, provider_factory=factory)

        request = ReviewRequest(
            question="quick check",
            mode=Mode.FAST,
        )
        result = await orch.run(request)

        # 1 call x 1500 tokens
        assert result.trace.total_tokens_actual == 1500
        assert result.trace.total_calls == 1


# ---------------------------------------------------------------------------
# Verbose / trace rendering
# ---------------------------------------------------------------------------


def _build_final_result(**overrides) -> FinalResult:
    """Build a minimal FinalResult for rendering tests."""
    defaults = dict(
        request_id="test-verbose",
        mode=Mode.REVIEW,
        selected_roles=[],
        consensus_findings=[],
        conflicting_findings=[],
        likely_shortcuts=[],
        final_recommendation="Use approach A.",
        decision_points=[],
        trace=Trace(total_calls=2, total_tokens_actual=3000, providers_used=["anthropic"]),
        confidence=Confidence.HIGH,
    )
    defaults.update(overrides)
    return FinalResult(**defaults)


class TestVerboseRendering:
    """Trace footer should be hidden by default and shown with verbose=True."""

    def test_render_markdown_hides_trace_by_default(self):
        """Trace line should not appear in default markdown output."""
        result = _build_final_result()
        md = render_markdown(result)
        assert "Trace:" not in md

    def test_render_markdown_shows_trace_when_verbose(self):
        """Trace line should appear when verbose=True."""
        result = _build_final_result()
        md = render_markdown(result, verbose=True)
        assert "Trace:" in md

    def test_render_dispatcher_passes_verbose_to_markdown(self):
        """render() with verbose=True should include trace in markdown."""
        result = _build_final_result()
        md = render(result, output_format="markdown", verbose=True)
        assert "Trace:" in md

    def test_render_dispatcher_default_hides_trace(self):
        """render() without verbose should hide trace in markdown."""
        result = _build_final_result()
        md = render(result, output_format="markdown")
        assert "Trace:" not in md


class TestFormatProse:
    """Tests for _format_prose inline-list reformatting."""

    def test_numbered_items(self):
        text = "Gate on: (1) add validation; (2) define semantics; (3) fix tests."
        result = _format_prose(text)
        assert "1. add validation" in result
        assert "2. define semantics" in result
        assert "3. fix tests" in result
        # Intro is preserved
        assert result.startswith("Gate on")

    def test_numbered_with_trailing_section(self):
        text = (
            "Do: (1) first thing; (2) second thing. "
            "Actionable findings: do X; do Y; do Z."
        )
        result = _format_prose(text)
        assert "1. first thing" in result
        assert "2. second thing" in result
        assert "**Actionable findings:**" in result
        assert "- do X" in result
        assert "- do Y" in result
        assert "- do Z" in result

    def test_plain_text_unchanged(self):
        text = "This is a simple recommendation."
        assert _format_prose(text) == text

    def test_no_false_positive_on_parenthesized_numbers(self):
        """Single parenthesized number in normal prose should still trigger."""
        text = "Consider (1) adding tests."
        result = _format_prose(text)
        assert "1. adding tests" in result

    def test_builder_recommendation_in_markdown(self):
        """Builder Recommendation section should use formatted lists."""
        builder = BuilderResult(
            summary="Fix things",
            recommendation="Fix these: (1) add auth; (2) add logging; (3) add tests.",
            assumptions=[],
            alternatives=[],
            risks=[],
            open_questions=[],
            confidence=Confidence.HIGH,
        )
        result = _build_final_result(
            trace=Trace(
                total_calls=2,
                total_tokens_actual=3000,
                providers_used=["anthropic"],
                builder_result=builder,
            ),
        )
        md = render_markdown(result)
        assert "1. add auth" in md
        assert "2. add logging" in md
        assert "3. add tests" in md
