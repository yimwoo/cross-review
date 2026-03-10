"""Tests for layered routing strategy (design doc §9)."""

from cross_review.config import RouterConfig
from cross_review.router import (
    HIGH_RISK_TERMS,
    MEDIUM_RISK_TERMS,
    MIN_COMPLEXITY_WORD_THRESHOLD,
    TECHNICAL_KEYWORDS,
    choose_mode,
)
from cross_review.schemas import ContextPayload, Mode, ReviewRequest


# ── Helpers ───────────────────────────────────────────────────────────


def _request(
    question: str, mode: Mode = Mode.AUTO, context: ContextPayload | None = None
) -> ReviewRequest:
    """Create a ReviewRequest with sensible defaults."""
    return ReviewRequest(question=question, mode=mode, context=context)


def _router_config() -> RouterConfig:
    """Return a default RouterConfig."""
    return RouterConfig()


# ── Layer 1: Explicit mode pass-through ──────────────────────────────


class TestExplicitMode:
    """When the user picks a non-AUTO mode, that mode is returned as-is."""

    def test_explicit_fast_respected(self):
        result = choose_mode(_request("anything", mode=Mode.FAST), _router_config())
        assert result is Mode.FAST

    def test_explicit_review_respected(self):
        result = choose_mode(_request("anything", mode=Mode.REVIEW), _router_config())
        assert result is Mode.REVIEW

    def test_explicit_arbitration_respected(self):
        result = choose_mode(_request("anything", mode=Mode.ARBITRATION), _router_config())
        assert result is Mode.ARBITRATION


# ── Layer 2: Minimum-complexity gate (AUTO mode) ─────────────────────


class TestMinimumComplexityGate:
    """Short prompts with no context and no technical keywords go FAST."""

    def test_short_prompt_no_context_goes_fast(self):
        result = choose_mode(_request("how does this work"), _router_config())
        assert result is Mode.FAST

    def test_short_prompt_with_context_goes_review(self):
        """Even a short prompt should NOT get FAST if context is present."""
        ctx = ContextPayload(text="some additional context here")
        result = choose_mode(_request("how does this work", context=ctx), _router_config())
        assert result is Mode.REVIEW

    def test_short_prompt_with_tech_keyword_goes_arbitration(self):
        """Short prompt containing a high-risk keyword should escalate."""
        result = choose_mode(_request("fix the auth bug"), _router_config())
        assert result is Mode.ARBITRATION


# ── Layer 3: Heuristic signals ───────────────────────────────────────


class TestHeuristicSignals:
    """Technical keywords in the prompt steer mode selection."""

    def test_auth_keyword_triggers_arbitration(self):
        result = choose_mode(
            _request("Review the authentication changes in this PR"),
            _router_config(),
        )
        assert result is Mode.ARBITRATION

    def test_migration_keyword_triggers_arbitration(self):
        result = choose_mode(
            _request("Check this database migration script for safety issues"),
            _router_config(),
        )
        assert result is Mode.ARBITRATION

    def test_api_design_triggers_review(self):
        result = choose_mode(
            _request("Review the new api endpoint design for the user profile service"),
            _router_config(),
        )
        assert result is Mode.REVIEW

    def test_database_schema_triggers_review(self):
        result = choose_mode(
            _request("Review the database schema changes for the orders table"),
            _router_config(),
        )
        assert result is Mode.REVIEW


# ── Layer 4: Default fallback ────────────────────────────────────────


class TestDefaultFallback:
    """When no heuristic matches, AUTO defaults to REVIEW."""

    def test_generic_question_defaults_to_review(self):
        result = choose_mode(
            _request(
                "What do you think about the overall approach we are taking"
                " here for this particular feature branch in the repository"
            ),
            _router_config(),
        )
        assert result is Mode.REVIEW


# ── Module-level constants sanity checks ─────────────────────────────


class TestConstants:
    """Verify module-level constants are properly defined."""

    def test_high_risk_terms_present(self):
        assert "auth" in HIGH_RISK_TERMS
        assert "security" in HIGH_RISK_TERMS
        assert "migration" in HIGH_RISK_TERMS
        assert "production" in HIGH_RISK_TERMS

    def test_medium_risk_terms_present(self):
        assert "api" in MEDIUM_RISK_TERMS
        assert "database" in MEDIUM_RISK_TERMS
        assert "schema" in MEDIUM_RISK_TERMS

    def test_technical_keywords_is_superset(self):
        assert TECHNICAL_KEYWORDS == set(HIGH_RISK_TERMS + MEDIUM_RISK_TERMS)

    def test_min_complexity_threshold(self):
        assert MIN_COMPLEXITY_WORD_THRESHOLD == 15
