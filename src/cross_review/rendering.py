"""Output renderers for markdown, JSON, and summary formats (design doc \u00a712.2)."""

from __future__ import annotations

from cross_review.schemas import FinalResult, Finding, ReconciledCluster


def render_json(result: FinalResult) -> str:
    """Render the final result as indented JSON."""
    return result.model_dump_json(indent=2)


def _render_markdown_header(result: FinalResult) -> list[str]:
    """Render the header section of the Markdown output.

    Args:
        result: The final review result.

    Returns:
        Lines for the header section.
    """
    roles = ", ".join(r.value for r in result.selected_roles)
    return [
        "# Cross-Review Result",
        "",
        f"**Mode:** {result.mode.value}  ",
        f"**Confidence:** {result.confidence.value}  ",
        f"**Roles:** {roles}",
        "",
    ]


def _render_markdown_builder(result: FinalResult) -> list[str]:
    """Render the Builder Recommendation section of the Markdown output.

    Args:
        result: The final review result.

    Returns:
        Lines for the builder section (empty list if no builder result).
    """
    builder = result.trace.builder_result
    if builder is None:
        return []

    lines: list[str] = ["## Builder Recommendation", "", builder.recommendation, ""]
    if builder.assumptions:
        lines.append("**Assumptions:**")
        lines.append("")
        for assumption in builder.assumptions:
            lines.append(f"- {assumption}")
        lines.append("")
    if builder.alternatives:
        lines.append("**Alternatives:**")
        lines.append("")
        for alt in builder.alternatives:
            lines.append(f"- {alt}")
        lines.append("")
    return lines


def _render_markdown_findings(result: FinalResult) -> list[str]:
    """Render findings, conflicts, shortcuts, and decision points.

    Args:
        result: The final review result.

    Returns:
        Lines for the findings sections.
    """
    lines: list[str] = ["## Findings", ""]
    if result.consensus_findings:
        for cluster in result.consensus_findings:
            lines.append(_format_cluster(cluster))
    else:
        lines.append("No consensus findings.")
    lines.append("")

    if result.conflicting_findings:
        lines.append("## Conflicts")
        lines.append("")
        for cluster in result.conflicting_findings:
            lines.append(_format_cluster(cluster))
        lines.append("")

    if result.likely_shortcuts:
        lines.append("## Likely Shortcuts")
        lines.append("")
        for finding in result.likely_shortcuts:
            lines.append(_format_finding(finding))
        lines.append("")

    if result.decision_points:
        lines.append("## Decision Points")
        lines.append("")
        for point in result.decision_points:
            lines.append(f"- {point}")
        lines.append("")

    return lines


def _render_markdown_footer(result: FinalResult) -> list[str]:
    """Render the summary and trace footer of the Markdown output.

    Args:
        result: The final review result.

    Returns:
        Lines for the summary and footer section.
    """
    trace = result.trace
    providers = ", ".join(trace.providers_used) if trace.providers_used else "none"
    warning_suffix = f", warnings: {'; '.join(trace.warnings)}" if trace.warnings else ""
    return [
        "## Summary",
        "",
        result.final_recommendation,
        "",
        "---",
        "",
        (
            f"*Trace: {trace.total_calls} calls, "
            f"{trace.total_tokens_actual} tokens, "
            f"providers: {providers}{warning_suffix}*"
        ),
        "",
    ]


def render_markdown(result: FinalResult) -> str:
    """Render the final result as a human-readable Markdown document."""
    lines: list[str] = []
    lines.extend(_render_markdown_header(result))
    lines.extend(_render_markdown_builder(result))
    lines.extend(_render_markdown_findings(result))
    lines.extend(_render_markdown_footer(result))
    return "\n".join(lines)


def render_summary(result: FinalResult) -> str:
    """Render a compact single-line summary of the result."""
    finding_count = len(result.consensus_findings)
    conflict_count = len(result.conflicting_findings)
    shortcut_count = len(result.likely_shortcuts)

    return (
        f"[{result.mode.value}] confidence={result.confidence.value} "
        f"findings={finding_count} conflicts={conflict_count} "
        f"shortcuts={shortcut_count} \u2014 {result.final_recommendation}"
    )


def render(result: FinalResult, output_format: str = "markdown") -> str:
    """Dispatch to the appropriate renderer based on *output_format*.

    Supported formats: ``"json"``, ``"markdown"``, ``"summary"``.

    Args:
        result: The final review result to render.
        output_format: One of ``"json"``, ``"markdown"``, ``"summary"``.

    Returns:
        The rendered output string.

    Raises:
        ValueError: If *output_format* is not recognised.
    """
    dispatchers = {
        "json": render_json,
        "markdown": render_markdown,
        "summary": render_summary,
    }
    renderer = dispatchers.get(output_format)
    if renderer is None:
        raise ValueError(
            f"Unknown output format {output_format!r}. "
            f"Choose from: {', '.join(sorted(dispatchers))}"
        )
    return renderer(result)


# -- Private helpers ----------------------------------------------------------


def _format_cluster(cluster: ReconciledCluster) -> str:
    """Format a reconciled cluster as a Markdown bullet.

    Args:
        cluster: The reconciled finding cluster.

    Returns:
        A single Markdown bullet string.
    """
    sources = ", ".join(cluster.supporting_sources) if cluster.supporting_sources else "none"
    return (
        f"- **[{cluster.severity.value.upper()}]** "
        f"{cluster.category.value} \u2192 {cluster.target}: "
        f"{'; '.join(f.summary for f in cluster.findings)} "
        f"(sources: {sources})"
    )


def _format_finding(finding: Finding) -> str:
    """Format a single finding as a Markdown bullet.

    Args:
        finding: The individual finding.

    Returns:
        A single Markdown bullet string.
    """
    return (
        f"- **[{finding.severity.value.upper()}]** "
        f"{finding.category.value} \u2192 {finding.target}: "
        f"{finding.summary} "
        f"({finding.reviewer_type.value}, {finding.confidence.value})"
    )
