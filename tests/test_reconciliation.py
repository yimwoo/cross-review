"""Tests for local reconciliation engine. Ref: design doc §13, §19.3."""

from cross_review.reconciliation import Reconciler
from cross_review.schemas import (
    BuilderResult,
    Confidence,
    Finding,
    FindingCategory,
    Mode,
    ReviewerResult,
    ReviewerType,
    Severity,
)


def _make_finding(
    reviewer_type: ReviewerType = ReviewerType.CRITIC,
    category: FindingCategory = FindingCategory.SECURITY,
    severity: Severity = Severity.HIGH,
    target: str = "auth layer",
    summary: str = "Missing rate limit",
    source_model: str = "gpt-5",
    shortcut_risk: bool = False,
) -> Finding:
    return Finding(
        id=f"{reviewer_type.value}-{category.value}-abc12345",
        source_model=source_model,
        reviewer_type=reviewer_type,
        category=category,
        severity=severity,
        target=target,
        summary=summary,
        quote=None,
        shortcut_risk=shortcut_risk,
        rationale="test rationale",
        recommendation="test recommendation",
        confidence=Confidence.HIGH,
    )


def _make_builder_result() -> BuilderResult:
    return BuilderResult(
        summary="Use modular backend",
        recommendation="Start with FastAPI monolith",
        assumptions=["Small team"],
        alternatives=["Microservices"],
        risks=["Scaling"],
        open_questions=["Team size?"],
        confidence=Confidence.MEDIUM,
    )


class TestGrouping:
    def test_same_target_category_grouped(self):
        """Two findings with same target+category are grouped. Ref: §19.3."""
        f1 = _make_finding(reviewer_type=ReviewerType.CRITIC, source_model="gpt-5")
        f2 = _make_finding(reviewer_type=ReviewerType.ADVISOR, source_model="gemini-2.5-pro")
        reconciler = Reconciler()
        clusters = reconciler.cluster_findings([f1, f2])
        assert len(clusters) == 1
        assert clusters[0].consensus_strength == 2

    def test_different_categories_not_merged(self):
        """Findings in different categories are not merged. Ref: §19.3."""
        f1 = _make_finding(category=FindingCategory.SECURITY)
        f2 = _make_finding(category=FindingCategory.OPERABILITY)
        reconciler = Reconciler()
        clusters = reconciler.cluster_findings([f1, f2])
        assert len(clusters) == 2


class TestConflictDetection:
    def test_conflicting_recommendations_marked(self):
        """Conflicting recommendations on same target are marked. Ref: §19.3."""
        f1 = _make_finding(
            reviewer_type=ReviewerType.CRITIC,
            source_model="gpt-5",
        )
        f1 = f1.model_copy(update={"recommendation": "Add caching"})
        f2 = _make_finding(
            reviewer_type=ReviewerType.ADVISOR,
            source_model="gemini-2.5-pro",
        )
        f2 = f2.model_copy(update={"recommendation": "Remove caching layer entirely"})
        reconciler = Reconciler()
        clusters = reconciler.cluster_findings([f1, f2])
        assert len(clusters) == 1
        assert len(clusters[0].conflicting_recommendations) == 2


class TestDegradedOutput:
    def test_empty_findings_produces_warning(self):
        """Empty findings from all reviewers produce warning. Ref: §19.3."""
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-123",
        )
        assert any("no findings" in w.lower() for w in result.trace.warnings)


class TestFinalOutputMapping:
    def test_consensus_findings_in_deep(self):
        """Consensus findings = clusters with strength >= 2 in deep."""
        f1 = _make_finding(
            reviewer_type=ReviewerType.CRITIC,
            source_model="gpt-5",
            summary="Same issue",
        )
        f2 = _make_finding(
            reviewer_type=ReviewerType.ADVISOR,
            source_model="gemini-2.5-pro",
            summary="Same issue",
        )
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
                ReviewerResult(
                    reviewer_type=ReviewerType.ADVISOR,
                    overall_confidence=Confidence.MEDIUM,
                    findings=[f2],
                ),
            ],
            mode=Mode.DEEP,
            request_id="test-456",
        )
        assert len(result.consensus_findings) >= 1

    def test_deep_mode_excludes_low_consensus(self):
        """In deep mode, clusters with strength < 2 are excluded."""
        f1 = _make_finding(
            reviewer_type=ReviewerType.CRITIC,
            source_model="gpt-5",
        )
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
            ],
            mode=Mode.DEEP,
            request_id="test-arb-low",
        )
        assert len(result.consensus_findings) == 0

    def test_review_mode_all_findings_primary(self):
        """In review mode, all findings treated as primary output. Ref: §13.5."""
        f1 = _make_finding()
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-789",
        )
        assert len(result.consensus_findings) >= 1

    def test_shortcut_risk_findings_in_likely_shortcuts(self):
        """Findings with shortcut_risk=true appear in likely_shortcuts."""
        f1 = _make_finding(shortcut_risk=True, summary="Monitoring skipped")
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-abc",
        )
        assert len(result.likely_shortcuts) >= 1

    def test_deterministic_final_recommendation(self):
        """final_recommendation uses Builder recommendation + template. Ref: §13.6."""
        f1 = _make_finding()
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-def",
        )
        assert "Start with FastAPI monolith" in result.final_recommendation

    def test_decision_points_from_high_severity(self):
        """decision_points derived from high/critical severity clusters. Ref: §13.6."""
        f1 = _make_finding(severity=Severity.CRITICAL, summary="Auth bypass possible")
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.LOW,
                    findings=[f1],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-ghi",
        )
        assert len(result.decision_points) >= 1

    def test_decision_points_from_conflicting_recommendations(self):
        """decision_points also derived from clusters with conflicts."""
        f1 = _make_finding(
            reviewer_type=ReviewerType.CRITIC,
            source_model="gpt-5",
            severity=Severity.LOW,
        )
        f1 = f1.model_copy(update={"recommendation": "Do X"})
        f2 = _make_finding(
            reviewer_type=ReviewerType.ADVISOR,
            source_model="gemini-2.5-pro",
            severity=Severity.LOW,
        )
        f2 = f2.model_copy(update={"recommendation": "Do Y"})
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
                ReviewerResult(
                    reviewer_type=ReviewerType.ADVISOR,
                    overall_confidence=Confidence.HIGH,
                    findings=[f2],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-conflict-dp",
        )
        assert len(result.decision_points) >= 1

    def test_recommendation_format_multiline(self):
        """Final recommendation should be prose + bullet stats."""
        finding = _make_finding()
        reviewer_result = ReviewerResult(
            reviewer_type=ReviewerType.CRITIC,
            overall_confidence=Confidence.HIGH,
            findings=[finding],
            source_model="gpt-5",
        )
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[reviewer_result],
            mode=Mode.REVIEW,
            request_id="test-123",
        )
        rec = result.final_recommendation
        lines = rec.strip().split("\n")
        # First line is the recommendation prose
        assert lines[0] == "Start with FastAPI monolith"
        # Should contain bullet stats
        assert "- 1 supporting findings" in rec
        assert "- 0 conflicting findings" in rec
        assert "- 0 shortcut warnings" in rec

    def test_confidence_is_minimum_of_reviewers(self):
        """Confidence should be the minimum of all reviewer overall_confidence values."""
        f1 = _make_finding(reviewer_type=ReviewerType.CRITIC, source_model="gpt-5")
        f2 = _make_finding(reviewer_type=ReviewerType.ADVISOR, source_model="gemini-2.5-pro")
        reconciler = Reconciler()
        result = reconciler.reconcile(
            builder_result=_make_builder_result(),
            reviewer_results=[
                ReviewerResult(
                    reviewer_type=ReviewerType.CRITIC,
                    overall_confidence=Confidence.HIGH,
                    findings=[f1],
                ),
                ReviewerResult(
                    reviewer_type=ReviewerType.ADVISOR,
                    overall_confidence=Confidence.LOW,
                    findings=[f2],
                ),
            ],
            mode=Mode.REVIEW,
            request_id="test-conf",
        )
        assert result.confidence == Confidence.LOW
