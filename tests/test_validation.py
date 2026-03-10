"""Tests for response validation and metadata injection (design doc §12)."""

import pytest

from cross_review.schemas import (
    BuilderResult,
    Confidence,
    FindingCategory,
    ReviewerType,
    Severity,
)
from cross_review.validation import (
    ValidationError,
    inject_finding_metadata,
    validate_builder_result,
    validate_reviewer_result,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _builder_result(**overrides) -> BuilderResult:
    """Create a valid BuilderResult with sensible defaults."""
    defaults = dict(
        summary="Analysis complete",
        recommendation="Ship with monitoring",
        assumptions=["Tests pass"],
        alternatives=["Rewrite"],
        risks=["Memory leak"],
        open_questions=["Edge case?"],
        confidence=Confidence.MEDIUM,
    )
    defaults.update(overrides)
    return BuilderResult(**defaults)


def _raw_finding(**overrides) -> dict:
    """Create a valid raw finding dict with sensible defaults."""
    defaults = dict(
        category="correctness",
        severity="high",
        target="src/main.py:42",
        summary="Null pointer dereference risk",
        quote="x = obj.value  # obj may be None",
        shortcut_risk=True,
        rationale="obj is not validated before access",
        recommendation="Add null check before accessing obj.value",
        confidence="high",
    )
    defaults.update(overrides)
    return defaults


def _raw_reviewer_result(**overrides) -> dict:
    """Create a valid raw reviewer result dict."""
    defaults = dict(
        overall_confidence="medium",
        findings=[_raw_finding()],
    )
    defaults.update(overrides)
    return defaults


# ── validate_builder_result ───────────────────────────────────────────


class TestValidateBuilderResult:
    """validate_builder_result pass-through validation."""

    def test_valid_builder_result_passes(self):
        """A valid BuilderResult passes through unchanged."""
        result = _builder_result()
        validated = validate_builder_result(result)
        assert validated is result

    def test_empty_recommendation_rejected_by_pydantic(self):
        """Pydantic rejects empty recommendation via min_length=1."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            _builder_result(recommendation="")


# ── validate_reviewer_result ──────────────────────────────────────────


class TestValidateReviewerResult:
    """validate_reviewer_result checks for empty/trivial findings."""

    def test_empty_findings_high_risk_raises(self):
        """Empty findings on a high-risk request must raise ValidationError."""
        raw = _raw_reviewer_result(findings=[])
        with pytest.raises(ValidationError, match="empty findings"):
            validate_reviewer_result(raw, is_high_risk=True)

    def test_empty_findings_low_risk_accepted(self):
        """Empty findings on a low-risk request are acceptable."""
        raw = _raw_reviewer_result(findings=[])
        result = validate_reviewer_result(raw, is_high_risk=False)
        assert result.findings == []

    def test_trivial_summary_high_risk_raises(self):
        """Trivial 'no issues found' summary on high-risk raises ValidationError."""
        raw = _raw_reviewer_result(
            findings=[_raw_finding(summary="No issues found", rationale="All good")]
        )
        with pytest.raises(ValidationError, match="trivial"):
            validate_reviewer_result(raw, is_high_risk=True)

    def test_trivial_looks_good_high_risk_raises(self):
        """Trivial 'looks good' summary on high-risk raises ValidationError."""
        raw = _raw_reviewer_result(findings=[_raw_finding(summary="Looks good", rationale="Fine")])
        with pytest.raises(ValidationError, match="trivial"):
            validate_reviewer_result(raw, is_high_risk=True)

    def test_trivial_no_concerns_high_risk_raises(self):
        """Trivial 'no concerns' summary on high-risk raises ValidationError."""
        raw = _raw_reviewer_result(
            findings=[_raw_finding(summary="No concerns here", rationale="OK")]
        )
        with pytest.raises(ValidationError, match="trivial"):
            validate_reviewer_result(raw, is_high_risk=True)

    def test_trivial_no_problems_high_risk_raises(self):
        """Trivial 'no problems' summary on high-risk raises ValidationError."""
        raw = _raw_reviewer_result(
            findings=[_raw_finding(summary="No problems detected", rationale="")]
        )
        with pytest.raises(ValidationError, match="trivial"):
            validate_reviewer_result(raw, is_high_risk=True)

    def test_trivial_short_summary_empty_rationale_high_risk_raises(self):
        """Short summary with empty rationale on high-risk is trivial."""
        raw = _raw_reviewer_result(findings=[_raw_finding(summary="OK", rationale="")])
        with pytest.raises(ValidationError, match="trivial"):
            validate_reviewer_result(raw, is_high_risk=True)

    def test_trivial_low_risk_accepted(self):
        """Trivial findings on low-risk are accepted without error."""
        raw = _raw_reviewer_result(
            findings=[_raw_finding(summary="No issues found", rationale="All good")]
        )
        result = validate_reviewer_result(raw, is_high_risk=False)
        assert len(result.findings) == 1

    def test_valid_findings_high_risk_passes(self):
        """Substantive findings on high-risk pass validation."""
        raw = _raw_reviewer_result(
            findings=[
                _raw_finding(
                    summary="Null pointer dereference risk in auth handler",
                    rationale="The obj variable is used without null check after fetch",
                )
            ]
        )
        result = validate_reviewer_result(raw, is_high_risk=True)
        assert len(result.findings) == 1

    def test_returns_reviewer_result_with_placeholder_type(self):
        """Returned ReviewerResult has a placeholder reviewer_type."""
        raw = _raw_reviewer_result()
        result = validate_reviewer_result(raw, is_high_risk=False)
        # Should be a valid ReviewerType (placeholder, overwritten by orchestrator)
        assert result.reviewer_type in list(ReviewerType)


# ── inject_finding_metadata ───────────────────────────────────────────


class TestInjectFindingMetadata:
    """inject_finding_metadata sets source_model, reviewer_type, and id."""

    def test_sets_source_model_and_reviewer_type(self):
        """source_model and reviewer_type come from function params."""
        raw = _raw_finding()
        finding = inject_finding_metadata(
            raw, source_model="claude-sonnet", reviewer_type="skeptic"
        )
        assert finding.source_model == "claude-sonnet"
        assert finding.reviewer_type == ReviewerType.SKEPTIC

    def test_generates_id_with_correct_prefix(self):
        """Generated id starts with '{reviewer_type}-{category}-'."""
        raw = _raw_finding(category="security")
        finding = inject_finding_metadata(raw, source_model="gpt-4o", reviewer_type="pragmatist")
        assert finding.id.startswith("pragmatist-security-")

    def test_id_has_8_char_hex_suffix(self):
        """Generated id ends with 8 hex characters."""
        raw = _raw_finding()
        finding = inject_finding_metadata(
            raw, source_model="claude-sonnet", reviewer_type="skeptic"
        )
        suffix = finding.id.split("-", 2)[2]
        assert len(suffix) == 8
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_parses_all_fields_from_raw(self):
        """All finding fields are correctly parsed from the raw dict."""
        raw = _raw_finding(
            category="security",
            severity="critical",
            target="auth.py:10",
            summary="Hardcoded secret in source",
            quote="API_KEY = 'sk-123'",
            shortcut_risk=False,
            rationale="API key visible in plaintext",
            recommendation="Move to environment variable",
            confidence="high",
        )
        finding = inject_finding_metadata(raw, source_model="gpt-4o", reviewer_type="security")
        assert finding.category == FindingCategory.SECURITY
        assert finding.severity == Severity.CRITICAL
        assert finding.target == "auth.py:10"
        assert finding.summary == "Hardcoded secret in source"
        assert finding.quote == "API_KEY = 'sk-123'"
        assert finding.shortcut_risk is False
        assert finding.rationale == "API key visible in plaintext"
        assert finding.recommendation == "Move to environment variable"
        assert finding.confidence == Confidence.HIGH

    def test_optional_quote_defaults_to_none(self):
        """When quote is not in raw_finding, it defaults to None."""
        raw = _raw_finding()
        del raw["quote"]
        finding = inject_finding_metadata(raw, source_model="claude-sonnet", reviewer_type="ops")
        assert finding.quote is None
