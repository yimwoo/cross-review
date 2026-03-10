"""Tests for cross-review data contract schemas (design doc $11)."""

import uuid

import pytest
from pydantic import ValidationError

from cross_review.schemas import (
    BuilderResult,
    BudgetConfig,
    Confidence,
    ContextPayload,
    FileContext,
    Finding,
    FindingCategory,
    FinalResult,
    HostInfo,
    Mode,
    Preferences,
    ReconciledCluster,
    ReviewerResult,
    ReviewerType,
    ReviewRequest,
    Severity,
    TokenUsage,
    Trace,
    generate_finding_id,
)


# ── ReviewRequest ──────────────────────────────────────────────────────


class TestReviewRequest:
    """ReviewRequest minimal and full construction."""

    def test_minimal_construction(self):
        """Minimal ReviewRequest only needs a question."""
        req = ReviewRequest(question="Is this code safe?")

        # auto-generated uuid
        assert isinstance(uuid.UUID(req.request_id), uuid.UUID)
        assert req.question == "Is this code safe?"
        assert req.mode == Mode.REVIEW
        assert req.constraints == []
        assert req.context is None
        # defaults
        assert req.preferences is not None
        assert req.budget is not None
        assert req.host is not None

    def test_full_construction(self):
        """Full ReviewRequest with every field specified."""
        ctx = ContextPayload(
            text="some context",
            files=[FileContext(path="main.py", content="print('hi')")],
            diff="--- a/main.py\n+++ b/main.py",
            project_summary="A small CLI tool",
        )
        budget = BudgetConfig(
            max_total_calls=6,
            max_reviewers=3,
            soft_token_limit=25000,
            hard_token_limit=40000,
            orchestration_timeout_seconds=120,
        )
        prefs = Preferences(prioritize_speed=True, prioritize_depth=False)
        host = HostInfo(name="vscode", auth_mode="extension_managed")

        req = ReviewRequest(
            request_id="custom-id-123",
            question="Review this PR",
            context=ctx,
            constraints=["focus on security"],
            mode=Mode.FAST,
            preferences=prefs,
            budget=budget,
            host=host,
        )

        assert req.request_id == "custom-id-123"
        assert req.question == "Review this PR"
        assert req.mode == Mode.FAST
        assert req.constraints == ["focus on security"]
        assert req.context.text == "some context"
        assert len(req.context.files) == 1
        assert req.budget.max_total_calls == 6
        assert req.preferences.prioritize_speed is True
        assert req.host.name == "vscode"


# ── BuilderResult ──────────────────────────────────────────────────────


class TestBuilderResult:
    """BuilderResult with all fields + rejection of empty recommendation."""

    def test_all_fields(self):
        result = BuilderResult(
            summary="Looks good overall",
            recommendation="Ship it with minor fixes",
            assumptions=["Tests pass", "No breaking changes"],
            alternatives=["Rewrite module X"],
            risks=["Potential memory leak"],
            open_questions=["What about edge case Y?"],
            confidence=Confidence.HIGH,
        )

        assert result.summary == "Looks good overall"
        assert result.recommendation == "Ship it with minor fixes"
        assert len(result.assumptions) == 2
        assert result.confidence == Confidence.HIGH

    def test_rejects_empty_recommendation(self):
        """recommendation must have min_length=1."""
        with pytest.raises(ValidationError):
            BuilderResult(
                summary="Summary",
                recommendation="",
                assumptions=[],
                alternatives=[],
                risks=[],
                open_questions=[],
                confidence=Confidence.MEDIUM,
            )


# ── Finding ────────────────────────────────────────────────────────────


class TestFinding:
    """Finding with all fields + optional quote."""

    def test_all_fields(self):
        finding = Finding(
            id="skeptic-correctness-abcd1234",
            source_model="claude-sonnet",
            reviewer_type=ReviewerType.SKEPTIC,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.HIGH,
            target="src/main.py:42",
            summary="Null pointer dereference",
            quote="x = obj.value  # obj may be None",
            shortcut_risk=True,
            rationale="obj is not validated before access",
            recommendation="Add null check",
            confidence=Confidence.HIGH,
        )

        assert finding.id == "skeptic-correctness-abcd1234"
        assert finding.reviewer_type == ReviewerType.SKEPTIC
        assert finding.category == FindingCategory.CORRECTNESS
        assert finding.severity == Severity.HIGH
        assert finding.quote == "x = obj.value  # obj may be None"
        assert finding.shortcut_risk is True

    def test_optional_quote(self):
        """quote is optional and defaults to None."""
        finding = Finding(
            id="pragmatist-security-ef567890",
            source_model="gpt-4o",
            reviewer_type=ReviewerType.PRAGMATIST,
            category=FindingCategory.SECURITY,
            severity=Severity.CRITICAL,
            target="src/auth.py",
            summary="Hardcoded secret",
            rationale="API key in source",
            recommendation="Use env var",
            confidence=Confidence.HIGH,
        )

        assert finding.quote is None
        assert finding.shortcut_risk is False


# ── FinalResult with nested Trace ──────────────────────────────────────


class TestFinalResult:
    """FinalResult structure with nested Trace."""

    def test_structure(self):
        trace = Trace(
            total_calls=3,
            total_tokens_actual=15000,
            providers_used=["anthropic", "openai"],
            builder_result=BuilderResult(
                summary="Analysis complete",
                recommendation="Approve with caveats",
                assumptions=[],
                alternatives=[],
                risks=[],
                open_questions=[],
                confidence=Confidence.MEDIUM,
            ),
            warnings=["Approaching token limit"],
        )

        result = FinalResult(
            request_id="req-001",
            mode=Mode.REVIEW,
            selected_roles=[ReviewerType.SKEPTIC, ReviewerType.PRAGMATIST],
            consensus_findings=[],
            conflicting_findings=[],
            likely_shortcuts=[],
            final_recommendation="Ship with monitoring",
            decision_points=["Added extra logging"],
            trace=trace,
            confidence=Confidence.MEDIUM,
        )

        assert result.request_id == "req-001"
        assert result.mode == Mode.REVIEW
        assert len(result.selected_roles) == 2
        assert result.trace.total_calls == 3
        assert result.trace.total_tokens_actual == 15000
        assert result.trace.builder_result.summary == "Analysis complete"
        assert result.trace.warnings == ["Approaching token limit"]
        assert result.confidence == Confidence.MEDIUM

    def test_defaults(self):
        """FinalResult with minimal fields uses sensible defaults."""
        trace = Trace()
        result = FinalResult(
            request_id="req-002",
            mode=Mode.AUTO,
            selected_roles=[],
            consensus_findings=[],
            conflicting_findings=[],
            likely_shortcuts=[],
            decision_points=[],
            trace=trace,
        )

        assert result.final_recommendation == ""
        assert result.confidence == Confidence.MEDIUM
        assert result.trace.total_calls == 0
        assert result.trace.total_tokens_actual == 0
        assert result.trace.providers_used == []
        assert result.trace.builder_result is None
        assert result.trace.warnings == []


# ── ReconciledCluster ──────────────────────────────────────────────────


class TestReconciledCluster:
    """ReconciledCluster structure."""

    def test_structure(self):
        finding = Finding(
            id="ops-operability-aabb1122",
            source_model="gemini-pro",
            reviewer_type=ReviewerType.OPS,
            category=FindingCategory.OPERABILITY,
            severity=Severity.MEDIUM,
            target="deploy.yaml",
            summary="Missing health check",
            rationale="No liveness probe configured",
            recommendation="Add liveness probe",
            confidence=Confidence.MEDIUM,
        )

        cluster = ReconciledCluster(
            category=FindingCategory.OPERABILITY,
            severity=Severity.MEDIUM,
            target="deploy.yaml",
            findings=[finding],
            supporting_sources=["gemini-pro"],
            consensus_strength=0.8,
            conflicting_recommendations=[],
        )

        assert cluster.category == FindingCategory.OPERABILITY
        assert cluster.severity == Severity.MEDIUM
        assert len(cluster.findings) == 1
        assert cluster.consensus_strength == 0.8
        assert cluster.supporting_sources == ["gemini-pro"]

    def test_defaults(self):
        cluster = ReconciledCluster(
            category=FindingCategory.COST,
            severity=Severity.LOW,
            target="infra/",
            findings=[],
            supporting_sources=[],
            conflicting_recommendations=[],
        )
        assert cluster.consensus_strength == 1


# ── Helper: generate_finding_id ────────────────────────────────────────


class TestGenerateFindingId:
    """generate_finding_id helper function."""

    def test_format(self):
        fid = generate_finding_id(
            reviewer_type="skeptic",
            category="correctness",
            source_model="claude-sonnet",
            target="main.py:10",
            summary="Bug found",
        )
        assert fid.startswith("skeptic-correctness-")
        # 8-char hex hash suffix
        suffix = fid.split("-", 2)[2]
        assert len(suffix) == 8
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_deterministic(self):
        """Same inputs produce the same id."""
        args = dict(
            reviewer_type="pragmatist",
            category="security",
            source_model="gpt-4o",
            target="auth.py",
            summary="Leaked secret",
        )
        assert generate_finding_id(**args) == generate_finding_id(**args)

    def test_different_inputs_differ(self):
        base = dict(
            reviewer_type="skeptic",
            category="correctness",
            source_model="claude-sonnet",
            target="main.py",
            summary="Issue A",
        )
        id_a = generate_finding_id(**base)
        id_b = generate_finding_id(**{**base, "summary": "Issue B"})
        assert id_a != id_b


# ── TokenUsage ─────────────────────────────────────────────────────────


class TestTokenUsage:
    def test_construction(self):
        usage = TokenUsage(input_tokens=100, output_tokens=200, total_tokens=300)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200
        assert usage.total_tokens == 300


# ── ReviewerResult ─────────────────────────────────────────────────────


class TestReviewerResult:
    def test_construction(self):
        result = ReviewerResult(
            reviewer_type=ReviewerType.SECURITY,
            overall_confidence=Confidence.HIGH,
            findings=[],
        )
        assert result.reviewer_type == ReviewerType.SECURITY
        assert result.overall_confidence == Confidence.HIGH
        assert result.findings == []


# ── HostInfo ──────────────────────────────────────────────────────────


class TestHostInfo:
    """Tests for HostInfo defaults."""

    def test_default_auth_mode_is_auto(self):
        """Default auth_mode should be auto for auto-detection."""
        info = HostInfo()
        assert info.auth_mode == "auto"

    def test_explicit_auth_mode_respected(self):
        """Explicit auth_mode should be preserved."""
        info = HostInfo(auth_mode="provider_managed")
        assert info.auth_mode == "provider_managed"
