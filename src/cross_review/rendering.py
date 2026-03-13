"""Output renderers for markdown, JSON, and summary formats (design doc \u00a712.2)."""

from __future__ import annotations

import re

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


def _format_prose(text: str) -> str:
    """Reformat inline numbered items and semicolon-lists into markdown lists.

    Handles two common LLM output patterns:

    1. Inline numbered items::

        ...intro: (1) first; (2) second; (3) third.

       Becomes a numbered markdown list.

    2. Semicolon-separated items after a heading phrase like
       ``Actionable findings:``::

        Actionable findings: do X; do Y; do Z.

       Becomes a bulleted markdown list under a bold heading.
    """
    # --- Phase 1: numbered items "(1) ... ; (2) ... ; (3) ..." ---
    # Split on (N) markers while keeping the number.
    parts = re.split(r"\((\d+)\)\s*", text)

    if len(parts) >= 3:  # at least one numbered item found
        intro = parts[0].rstrip(":; \t")
        items: list[str] = []
        for i in range(1, len(parts), 2):
            num = parts[i]
            body = parts[i + 1].strip().rstrip(";") if i + 1 < len(parts) else ""
            if body:
                items.append((num, body))

        # Check if the last item contains a trailing "Heading: a; b; c"
        # section (e.g. "Actionable findings: ...").
        trailing_section = ""
        if items:
            last_num, last_body = items[-1]
            # Look for a sentence break followed by a capitalised heading + colon
            heading_match = re.search(
                r"(?<=[.!?])\s+([A-Z][^:]{2,50}):\s*(.+)", last_body
            )
            if heading_match:
                items[-1] = (last_num, last_body[: heading_match.start()].rstrip(". "))
                heading = heading_match.group(1).strip()
                semi_items = heading_match.group(2).strip()
                trailing_section = _format_semicolon_list(heading, semi_items)

        # Build formatted output
        out_lines = [intro, ""]
        for num, body in items:
            # Clean trailing period/semicolon from each item
            body = body.rstrip(";. ")
            out_lines.append(f"{num}. {body}")
        out_lines.append("")

        if trailing_section:
            out_lines.append(trailing_section)
            out_lines.append("")

        return "\n".join(out_lines)

    # --- Phase 2: standalone semicolon-list  "Heading: a; b; c" ---
    # Only if no numbered items were found, check for a heading pattern.
    heading_match = re.match(r"^([A-Z][^:]{2,60}):\s*(.+)", text, re.DOTALL)
    if heading_match:
        content = heading_match.group(2).strip()
        if content.count(";") >= 2:
            return _format_semicolon_list(heading_match.group(1), content)

    return text


def _format_semicolon_list(heading: str, items_text: str) -> str:
    """Convert ``heading: a; b; c`` into a bulleted markdown list."""
    items = [s.strip().rstrip(".") for s in items_text.split(";") if s.strip()]
    lines = [f"**{heading}:**", ""]
    for item in items:
        lines.append(f"- {item}")
    return "\n".join(lines)


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

    formatted_rec = _format_prose(builder.recommendation)
    lines: list[str] = ["## Builder Recommendation", "", formatted_rec, ""]
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


def _render_markdown_perspectives(result: FinalResult) -> list[str]:
    """Render the per-role perspectives comparison table.

    Args:
        result: The final review result.

    Returns:
        Lines for the perspectives table (empty if no summaries).
    """
    if not result.reviewer_summaries:
        return []

    lines: list[str] = [
        "## Perspectives",
        "",
        "| Role | Model | Verdict | Confidence | Key Concern |",
        "|------|-------|---------|------------|-------------|",
    ]

    # Builder row
    builder = result.trace.builder_result
    if builder:
        builder_model = result.builder_model or "unknown"
        lines.append(
            f"| Builder | {builder_model} | {builder.recommendation[:60]} "
            f"| {builder.confidence.value} | — |"
        )

    # Reviewer rows
    for rs in result.reviewer_summaries:
        role_label = rs.reviewer_type.value.capitalize()
        lines.append(
            f"| {role_label} | {rs.model} | {rs.verdict} "
            f"| {rs.confidence.value} | {rs.key_concern} |"
        )

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


def _render_markdown_footer(result: FinalResult, verbose: bool = False) -> list[str]:
    """Render the summary and trace footer of the Markdown output.

    Args:
        result: The final review result.
        verbose: If True, include the trace diagnostics line.

    Returns:
        Lines for the summary and footer section.
    """
    formatted_rec = _format_prose(
        result.final_recommendation.split("\n", 1)[0]  # prose line only
    )
    # Re-append the bullet stats (lines after the first)
    rest = result.final_recommendation.split("\n", 1)
    if len(rest) > 1:
        formatted_rec += "\n" + rest[1]

    lines = [
        "## Summary",
        "",
        formatted_rec,
        "",
    ]
    if verbose:
        trace = result.trace
        providers = ", ".join(trace.providers_used) if trace.providers_used else "none"
        warning_suffix = f", warnings: {'; '.join(trace.warnings)}" if trace.warnings else ""
        lines.extend([
            "---",
            "",
            (
                f"*Trace: {trace.total_calls} calls, "
                f"{trace.total_tokens_actual} tokens, "
                f"providers: {providers}{warning_suffix}*"
            ),
            "",
        ])
    return lines


def render_markdown(result: FinalResult, verbose: bool = False) -> str:
    """Render the final result as a human-readable Markdown document."""
    lines: list[str] = []
    lines.extend(_render_markdown_header(result))
    lines.extend(_render_markdown_builder(result))
    lines.extend(_render_markdown_perspectives(result))
    lines.extend(_render_markdown_findings(result))
    lines.extend(_render_markdown_footer(result, verbose=verbose))
    return "\n".join(lines)


def render_summary(result: FinalResult) -> str:
    """Render a compact single-line summary of the result."""
    finding_count = len(result.consensus_findings)
    conflict_count = len(result.conflicting_findings)
    shortcut_count = len(result.likely_shortcuts)

    # Use only the first line (prose part) of the recommendation
    recommendation_line = result.final_recommendation.split("\n", 1)[0]

    return (
        f"[{result.mode.value}] confidence={result.confidence.value} "
        f"findings={finding_count} conflicts={conflict_count} "
        f"shortcuts={shortcut_count} \u2014 {recommendation_line}"
    )


def render(result: FinalResult, output_format: str = "markdown", verbose: bool = False) -> str:
    """Dispatch to the appropriate renderer based on *output_format*.

    Supported formats: ``"json"``, ``"markdown"``, ``"summary"``.

    Args:
        result: The final review result to render.
        output_format: One of ``"json"``, ``"markdown"``, ``"summary"``.
        verbose: If True, include trace diagnostics (markdown only).

    Returns:
        The rendered output string.

    Raises:
        ValueError: If *output_format* is not recognised.
    """
    if output_format == "markdown":
        return render_markdown(result, verbose=verbose)
    dispatchers = {
        "json": render_json,
        "summary": render_summary,
    }
    renderer = dispatchers.get(output_format)
    if renderer is None:
        raise ValueError(
            f"Unknown output format {output_format!r}. "
            f"Choose from: json, markdown, summary"
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
