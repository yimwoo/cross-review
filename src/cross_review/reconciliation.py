"""Local reconciliation engine. Ref: design doc \u00a713."""

from __future__ import annotations

from collections import defaultdict

from cross_review.schemas import (
    BuilderResult,
    Confidence,
    FinalResult,
    Finding,
    Mode,
    ReconciledCluster,
    ReviewerResult,
    ReviewerSummary,
    ReviewerType,
    Severity,
    Trace,
)

# Severity ranking: lower index = higher priority (critical first).
_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]

# Confidence ranking: lower index = lower confidence.
_CONFIDENCE_ORDER = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]


def _severity_rank(severity: Severity) -> int:
    """Return the sort rank for a severity value (lower = more severe).

    Args:
        severity: The severity enum value.

    Returns:
        Integer index into the severity ordering.
    """
    return _SEVERITY_ORDER.index(severity)


def _confidence_rank(confidence: Confidence) -> int:
    """Return the sort rank for a confidence value (lower = less confident).

    Args:
        confidence: The confidence enum value.

    Returns:
        Integer index into the confidence ordering.
    """
    return _CONFIDENCE_ORDER.index(confidence)


class Reconciler:
    """Groups, clusters, and maps findings to final output. Ref: \u00a713.2-13.6."""

    def cluster_findings(self, findings: list[Finding]) -> list[ReconciledCluster]:
        """Group findings by (target, category) and detect conflicts. Ref: \u00a713.3."""
        groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
        for f in findings:
            key = (f.target.lower().strip(), f.category.value)
            groups[key].append(f)

        clusters: list[ReconciledCluster] = []
        for (_target, _category), group_findings in groups.items():
            # Unique "model:type" source strings
            sources = list({f"{f.source_model}:{f.reviewer_type.value}" for f in group_findings})
            # Consensus strength = count of unique source models
            unique_models = {f.source_model for f in group_findings}

            # Detect conflicting recommendations
            recommendations = [f.recommendation for f in group_findings]
            conflicts = recommendations if len(set(recommendations)) > 1 else []

            # Use highest severity in group (critical > high > medium > low)
            max_severity = min(
                (f.severity for f in group_findings),
                key=_severity_rank,
            )

            clusters.append(
                ReconciledCluster(
                    category=group_findings[0].category,
                    severity=max_severity,
                    target=group_findings[0].target,
                    findings=group_findings,
                    supporting_sources=sources,
                    consensus_strength=len(unique_models),
                    conflicting_recommendations=conflicts,
                )
            )

        # Sort by severity (critical first), then consensus strength descending
        clusters.sort(
            key=lambda c: (
                _severity_rank(c.severity),
                -c.consensus_strength,
            )
        )
        return clusters

    def reconcile(  # pylint: disable=too-many-locals
        self,
        builder_result: BuilderResult,
        reviewer_results: list[ReviewerResult],
        mode: Mode,
        request_id: str,
        builder_model: str = "",
    ) -> FinalResult:
        """Produce final decision-support artifact. Ref: \u00a713.5-13.6."""
        all_findings: list[Finding] = []
        warnings: list[str] = []
        selected_roles: list[ReviewerType] = []

        for rr in reviewer_results:
            selected_roles.append(rr.reviewer_type)
            all_findings.extend(rr.findings)

        if not all_findings:
            warnings.append("All reviewers returned no findings")

        clusters = self.cluster_findings(all_findings)

        # Map clusters to final output fields (\u00a713.5)
        consensus_findings, conflicting_findings, likely_shortcuts = self._classify_clusters(
            clusters, mode
        )

        # Deterministic final recommendation (\u00a713.6)
        final_recommendation = self._build_recommendation(
            builder_result, consensus_findings, conflicting_findings, likely_shortcuts
        )

        # Decision points from high-severity or conflicting clusters (\u00a713.6)
        decision_points = self._extract_decision_points(clusters)

        # Confidence: use lowest reviewer confidence
        if reviewer_results:
            overall_confidence = min(
                (rr.overall_confidence for rr in reviewer_results),
                key=_confidence_rank,
            )
        else:
            overall_confidence = builder_result.confidence

        # Build per-reviewer summaries for perspectives table
        reviewer_summaries: list[ReviewerSummary] = []
        for rr in reviewer_results:
            # Pick highest-severity finding as key concern
            if rr.findings:
                top_finding = min(rr.findings, key=lambda f: _severity_rank(f.severity))
                key_concern = top_finding.summary
            else:
                key_concern = "No issues found"

            # Build a one-line verdict from finding count and severity
            severity_counts: dict[str, int] = {}
            for f in rr.findings:
                severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1
            if severity_counts:
                parts = [f"{v} {k}" for k, v in severity_counts.items()]
                verdict = f"{len(rr.findings)} findings ({', '.join(parts)})"
            else:
                verdict = "No findings"

            reviewer_summaries.append(ReviewerSummary(
                reviewer_type=rr.reviewer_type,
                model=rr.source_model or "unknown",
                verdict=verdict,
                confidence=rr.overall_confidence,
                key_concern=key_concern,
            ))

        return FinalResult(
            request_id=request_id,
            mode=mode,
            selected_roles=selected_roles,
            consensus_findings=consensus_findings,
            conflicting_findings=conflicting_findings,
            likely_shortcuts=likely_shortcuts,
            final_recommendation=final_recommendation,
            decision_points=decision_points,
            trace=Trace(
                builder_result=builder_result,
                warnings=warnings,
            ),
            confidence=overall_confidence,
            builder_model=builder_model,
            reviewer_summaries=reviewer_summaries,
        )

    @staticmethod
    def _classify_clusters(
        clusters: list[ReconciledCluster],
        mode: Mode,
    ) -> tuple[list[ReconciledCluster], list[ReconciledCluster], list[Finding]]:
        """Classify clusters into consensus, conflicting, and shortcut buckets.

        Args:
            clusters: Reconciled finding clusters.
            mode: The operating mode (affects consensus rules).

        Returns:
            A tuple of (consensus_findings, conflicting_findings, likely_shortcuts).
        """
        consensus_findings: list[ReconciledCluster] = []
        conflicting_findings: list[ReconciledCluster] = []
        likely_shortcuts: list[Finding] = []

        for cluster in clusters:
            if mode == Mode.REVIEW:
                consensus_findings.append(cluster)
            elif cluster.consensus_strength >= 2:
                consensus_findings.append(cluster)

            if cluster.conflicting_recommendations:
                conflicting_findings.append(cluster)

            for f in cluster.findings:
                if f.shortcut_risk:
                    likely_shortcuts.append(f)

        return consensus_findings, conflicting_findings, likely_shortcuts

    @staticmethod
    def _build_recommendation(
        builder_result: BuilderResult,
        consensus_findings: list[ReconciledCluster],
        conflicting_findings: list[ReconciledCluster],
        likely_shortcuts: list[Finding],
    ) -> str:
        """Build the final recommendation as prose + bullet stats.

        Args:
            builder_result: The Builder's output.
            consensus_findings: Consensus finding clusters.
            conflicting_findings: Conflicting finding clusters.
            likely_shortcuts: Findings flagged as shortcut risks.

        Returns:
            A multi-line recommendation with prose on the first line
            followed by bullet-point statistics.
        """
        return (
            f"{builder_result.recommendation}\n"
            f"\n"
            f"- {len(consensus_findings)} supporting findings\n"
            f"- {len(conflicting_findings)} conflicting findings\n"
            f"- {len(likely_shortcuts)} shortcut warnings"
        )

    @staticmethod
    def _extract_decision_points(clusters: list[ReconciledCluster]) -> list[str]:
        """Extract decision points from high-severity or conflicting clusters.

        Args:
            clusters: All reconciled finding clusters.

        Returns:
            Deduplicated list of decision-point strings.
        """
        decision_points: list[str] = []
        for cluster in clusters:
            if cluster.severity in (Severity.HIGH, Severity.CRITICAL):
                summary = cluster.findings[0].summary if cluster.findings else "N/A"
                decision_points.append(
                    f"{cluster.target}: {summary} " f"(severity: {cluster.severity.value})"
                )
            if cluster.conflicting_recommendations:
                decision_points.append(f"{cluster.target}: reviewers disagree on recommendation")
        # Deduplicate while preserving order
        return list(dict.fromkeys(decision_points))
