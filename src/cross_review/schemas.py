"""Data contract schemas for cross-review (design doc $11)."""

from __future__ import annotations

import hashlib
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# -- Enums -------------------------------------------------------------------


class Mode(str, Enum):
    """Operating mode for the review engine."""

    FAST = "fast"
    REVIEW = "review"
    ARBITRATION = "arbitration"
    AUTO = "auto"


class Confidence(str, Enum):
    """Confidence level for findings and results."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Severity(str, Enum):
    """Severity level for findings."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(str, Enum):
    """Category of a review finding."""

    CORRECTNESS = "correctness"
    SECURITY = "security"
    SCALABILITY = "scalability"
    OPERABILITY = "operability"
    COST = "cost"
    COMPLEXITY = "complexity"


class ReviewerType(str, Enum):
    """Type of reviewer persona."""

    SKEPTIC = "skeptic"
    PRAGMATIST = "pragmatist"
    SECURITY = "security"
    OPS = "ops"
    COST = "cost"


# -- Context Models -----------------------------------------------------------


class FileContext(BaseModel):
    """A single file with its content and optional selection range."""

    path: str
    content: str
    selection: Optional[str] = None


class ContextPayload(BaseModel):
    """Contextual information attached to a review request."""

    text: Optional[str] = None
    files: list[FileContext] = Field(default_factory=list)
    diff: Optional[str] = None
    project_summary: Optional[str] = None


# -- Configuration Models -----------------------------------------------------


class BudgetConfig(BaseModel):
    """Token and call budget constraints."""

    max_total_calls: int = 4
    max_reviewers: int = 2
    soft_token_limit: int = 20000
    hard_token_limit: int = 30000
    orchestration_timeout_seconds: int = 60


class Preferences(BaseModel):
    """User preferences for the review."""

    prioritize_speed: bool = False
    prioritize_depth: bool = False


class HostInfo(BaseModel):
    """Information about the host environment."""

    name: str = "cli"
    auth_mode: str = "provider_managed"


# -- Request Model -------------------------------------------------------------


class ReviewRequest(BaseModel):
    """Top-level review request submitted by the user."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    context: Optional[ContextPayload] = None
    constraints: list[str] = Field(default_factory=list)
    mode: Mode = Mode.REVIEW
    preferences: Preferences = Field(default_factory=Preferences)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    host: HostInfo = Field(default_factory=HostInfo)


# -- Builder Result ------------------------------------------------------------


class BuilderResult(BaseModel):
    """Result from the context-builder phase."""

    summary: str
    recommendation: str = Field(min_length=1)
    assumptions: list[str]
    alternatives: list[str]
    risks: list[str]
    open_questions: list[str]
    confidence: Confidence


# -- Finding -------------------------------------------------------------------


class Finding(BaseModel):
    """A single review finding from one reviewer."""

    id: str
    source_model: str
    reviewer_type: ReviewerType
    category: FindingCategory
    severity: Severity
    target: str
    summary: str
    quote: Optional[str] = None
    shortcut_risk: bool = False
    rationale: str
    recommendation: str
    confidence: Confidence


# -- Reviewer Result -----------------------------------------------------------


class ReviewerResult(BaseModel):
    """Aggregated result from a single reviewer persona."""

    reviewer_type: ReviewerType
    overall_confidence: Confidence
    findings: list[Finding]


# -- Reconciliation ------------------------------------------------------------


class ReconciledCluster(BaseModel):
    """A cluster of related findings after reconciliation."""

    category: FindingCategory
    severity: Severity
    target: str
    findings: list[Finding]
    supporting_sources: list[str]
    consensus_strength: float = 1
    conflicting_recommendations: list[str]


# -- Trace ---------------------------------------------------------------------


class Trace(BaseModel):
    """Execution trace for observability."""

    total_calls: int = 0
    total_tokens_actual: int = 0
    providers_used: list[str] = Field(default_factory=list)
    builder_result: Optional[BuilderResult] = None
    warnings: list[str] = Field(default_factory=list)


# -- Final Result --------------------------------------------------------------


class FinalResult(BaseModel):
    """Top-level output of the review engine."""

    request_id: str
    mode: Mode
    selected_roles: list[ReviewerType]
    consensus_findings: list[ReconciledCluster]
    conflicting_findings: list[ReconciledCluster]
    likely_shortcuts: list[Finding]
    final_recommendation: str = ""
    decision_points: list[str]
    trace: Trace
    confidence: Confidence = Confidence.MEDIUM


# -- Token Usage ---------------------------------------------------------------


class TokenUsage(BaseModel):
    """Token usage statistics for a single LLM call."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


# -- Helpers -------------------------------------------------------------------


def generate_finding_id(
    reviewer_type: str,
    category: str,
    source_model: str,
    target: str,
    summary: str,
) -> str:
    """Generate a deterministic finding ID from its key attributes."""
    hash_input = f"{source_model}{target}{summary}"
    short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    return f"{reviewer_type}-{category}-{short_hash}"
