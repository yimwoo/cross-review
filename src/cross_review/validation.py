"""Response validation and metadata injection (design doc §12.3, §11.4).

Validates builder and reviewer outputs before they enter the reconciliation
pipeline.  Injects server-side metadata (id, source_model, reviewer_type)
into raw finding dicts so models never need to self-identify.
"""

from __future__ import annotations

import re

from cross_review.schemas import (
    BuilderResult,
    Confidence,
    Finding,
    FindingCategory,
    ReviewerResult,
    ReviewerType,
    Severity,
    generate_finding_id,
)


# ── Exception ─────────────────────────────────────────────────────────


class ValidationError(Exception):
    """Raised when a model response fails validation checks."""


# ── Trivial-finding detection ─────────────────────────────────────────

_TRIVIAL_PHRASES: re.Pattern[str] = re.compile(
    r"no issues found|looks good|no concerns|no problems",
    re.IGNORECASE,
)

_SHORT_SUMMARY_THRESHOLD = 30


def _is_trivial_finding(finding: dict) -> bool:
    """Return True if a finding is considered trivial.

    Trivial means:
    - summary matches known dismissive phrases, OR
    - rationale is empty and summary is shorter than 30 chars.
    """
    summary = finding.get("summary", "")
    rationale = finding.get("rationale", "")

    if _TRIVIAL_PHRASES.search(summary):
        return True

    if not rationale.strip() and len(summary) < _SHORT_SUMMARY_THRESHOLD:
        return True

    return False


# ── Builder validation ────────────────────────────────────────────────


def validate_builder_result(result: BuilderResult) -> BuilderResult:
    """Validate a BuilderResult.

    Pydantic handles structural checks (min_length, required fields).
    This is a pass-through for any future cross-field validation.
    """
    return result


# ── Reviewer validation ───────────────────────────────────────────────


def validate_reviewer_result(
    raw: dict,
    is_high_risk: bool = False,
) -> ReviewerResult:
    """Validate a raw reviewer result dict and return a ReviewerResult.

    Parameters
    ----------
    raw:
        Raw dict typically deserialized from the model's JSON response.
        Expected keys: ``overall_confidence``, ``findings`` (list of dicts).
    is_high_risk:
        When True, empty or trivial findings trigger a ValidationError
        so the orchestrator can retry with a different prompt/model.

    Returns
    -------
    ReviewerResult
        With a placeholder ``reviewer_type`` (overwritten by the orchestrator).

    Raises
    ------
    ValidationError
        If findings are empty or trivial on a high-risk request.
    """
    findings_raw: list[dict] = raw.get("findings", [])

    # ── High-risk guards ──────────────────────────────────────────────
    if is_high_risk:
        if not findings_raw:
            raise ValidationError("empty findings")

        if all(_is_trivial_finding(f) for f in findings_raw):
            raise ValidationError("trivial")

    # ── Build Finding objects with placeholder metadata ───────────────
    placeholder_type = ReviewerType.SKEPTIC  # overwritten by orchestrator
    placeholder_model = "unknown"

    parsed_findings: list[Finding] = [
        inject_finding_metadata(
            f, source_model=placeholder_model, reviewer_type=placeholder_type.value
        )
        for f in findings_raw
    ]

    return ReviewerResult(
        reviewer_type=placeholder_type,
        overall_confidence=Confidence(raw.get("overall_confidence", "medium")),
        findings=parsed_findings,
    )


# ── Metadata injection ───────────────────────────────────────────────


def inject_finding_metadata(
    raw_finding: dict,
    source_model: str,
    reviewer_type: str,
) -> Finding:
    """Inject server-side metadata into a raw finding dict.

    The model is never asked to provide ``id``, ``source_model``, or
    ``reviewer_type`` — these are set by the validation layer per the
    design doc (§11.4).

    Parameters
    ----------
    raw_finding:
        Dict with keys matching Finding fields (minus id, source_model,
        reviewer_type).
    source_model:
        The model identifier (e.g. ``"claude-sonnet"``).
    reviewer_type:
        The reviewer persona (e.g. ``"skeptic"``).

    Returns
    -------
    Finding
        Fully-populated Pydantic model.
    """
    category = raw_finding.get("category", "correctness")
    target = raw_finding.get("target", "")
    summary = raw_finding.get("summary", "")

    finding_id = generate_finding_id(
        reviewer_type=reviewer_type,
        category=category,
        source_model=source_model,
        target=target,
        summary=summary,
    )

    return Finding(
        id=finding_id,
        source_model=source_model,
        reviewer_type=ReviewerType(reviewer_type),
        category=FindingCategory(category),
        severity=Severity(raw_finding.get("severity", "medium")),
        target=target,
        summary=summary,
        quote=raw_finding.get("quote"),
        shortcut_risk=raw_finding.get("shortcut_risk", False),
        rationale=raw_finding.get("rationale", ""),
        recommendation=raw_finding.get("recommendation", ""),
        confidence=Confidence(raw_finding.get("confidence", "medium")),
    )
