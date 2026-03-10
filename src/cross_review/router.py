"""Layered routing strategy for cross-review (design doc §9).

Determines the operating mode for a review request using a four-layer
decision cascade:

    Layer 1 -- Explicit mode pass-through  (§9.1)
    Layer 2 -- Minimum-complexity gate     (§9.2)
    Layer 3 -- Heuristic keyword signals   (§9.3)
    Layer 4 -- Default fallback            (§9.4)
"""

from __future__ import annotations

from cross_review.config import RouterConfig
from cross_review.schemas import Mode, ReviewRequest

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

HIGH_RISK_TERMS: list[str] = [
    "auth",
    "authorization",
    "authentication",
    "security",
    "secret",
    "credential",
    "production",
    "migration",
    "rollback",
    "infra",
    "infrastructure",
    "platform",
]

MEDIUM_RISK_TERMS: list[str] = [
    "api",
    "schema",
    "database",
    "deploy",
    "deployment",
    "cache",
    "caching",
    "backend",
    "architecture",
]

TECHNICAL_KEYWORDS: set[str] = set(HIGH_RISK_TERMS + MEDIUM_RISK_TERMS)

MIN_COMPLEXITY_WORD_THRESHOLD: int = 15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_context(request: ReviewRequest) -> bool:
    """Return True if the request carries any non-empty context."""
    ctx = request.context
    if ctx is None:
        return False
    if ctx.text:
        return True
    if ctx.files:
        return True
    if ctx.diff:
        return True
    if ctx.project_summary:
        return True
    return False


def _prompt_words(request: ReviewRequest) -> list[str]:
    """Return the lowercased words of the question prompt."""
    return request.question.lower().split()


def _contains_high_risk(words: list[str]) -> bool:
    """Return True if any word is a high-risk keyword."""
    high_set = set(HIGH_RISK_TERMS)
    return bool(high_set.intersection(words))


def _contains_medium_risk(words: list[str]) -> bool:
    """Return True if any word is a medium-risk keyword."""
    medium_set = set(MEDIUM_RISK_TERMS)
    return bool(medium_set.intersection(words))


def _contains_technical_keyword(words: list[str]) -> bool:
    """Return True if any word is a technical keyword."""
    return bool(TECHNICAL_KEYWORDS.intersection(words))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def choose_mode(
    request: ReviewRequest,
    router_config: RouterConfig,  # pylint: disable=unused-argument
) -> Mode:
    """Select the operating mode via the layered routing strategy.

    Parameters
    ----------
    request:
        The incoming review request.
    router_config:
        Router-specific configuration (currently reserved for future
        classifier-based routing).

    Returns
    -------
    Mode
        The resolved operating mode after applying all routing layers.

    Routing layers (evaluated top-to-bottom, first match wins):

    1. **Explicit mode**: if the caller chose FAST, REVIEW, or ARBITRATION
       (anything other than AUTO), honour that choice immediately.
    2. **Minimum-complexity gate**: if the prompt is shorter than
       ``MIN_COMPLEXITY_WORD_THRESHOLD`` words *and* carries no context
       *and* contains no technical keywords, route to FAST.
    3. **Heuristic signals**: scan the prompt for high-risk terms
       (-> ARBITRATION) then medium-risk terms (-> REVIEW).
    4. **Default**: fall back to REVIEW.
    """
    # Layer 1: explicit mode pass-through
    if request.mode is not Mode.AUTO:
        return request.mode

    # Precompute prompt words for the remaining layers
    words = _prompt_words(request)

    # Layer 2: minimum-complexity gate
    if (
        len(words) < MIN_COMPLEXITY_WORD_THRESHOLD
        and not _has_context(request)
        and not _contains_technical_keyword(words)
    ):
        return Mode.FAST

    # Layer 3: heuristic keyword signals
    if _contains_high_risk(words):
        return Mode.ARBITRATION

    if _contains_medium_risk(words):
        return Mode.REVIEW

    # Layer 4: default fallback
    return Mode.REVIEW
