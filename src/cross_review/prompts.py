# src/cross_review/prompts.py
"""Prompt templates for Builder and Reviewer roles. Ref: design doc §21.4."""
from __future__ import annotations

import json

from cross_review.schemas import BuilderResult, ReviewRequest


BUILDER_SYSTEM_PROMPT = """You are the Builder for cross-review.

Your task is to produce a compact technical proposal.

Requirements:
- Be implementation-aware
- Make assumptions explicit
- Include alternatives
- Identify meaningful risks
- Be honest about missing context

Return ONLY valid JSON matching this schema:
{
  "summary": "string",
  "recommendation": "string",
  "assumptions": ["string"],
  "alternatives": ["string"],
  "risks": ["string"],
  "open_questions": ["string"],
  "confidence": "low|medium|high"
}"""

REVIEWER_SYSTEM_PROMPT_TEMPLATE = """You are a {reviewer_description} for cross-review.

Your task is to critique the Builder result, not rewrite the whole solution.

Requirements:
- Identify concrete weaknesses
- Challenge incorrect assumptions
- Flag production, security, scalability, or complexity issues when present
- Set shortcut_risk to true when the proposal appears to take a risky shortcut
- Suggest a better alternative only when clearly justified

Return ONLY valid JSON matching this schema:
{{
  "overall_confidence": "low|medium|high",
  "findings": [
    {{
      "category": "correctness|security|scalability|operability|cost|complexity",
      "severity": "low|medium|high|critical",
      "target": "string",
      "summary": "string",
      "quote": "string or null",
      "shortcut_risk": true or false,
      "rationale": "string",
      "recommendation": "string",
      "confidence": "low|medium|high"
    }}
  ]
}}"""

REVIEWER_DESCRIPTIONS = {
    "skeptic": (
        "skeptic reviewer — focus on logical gaps, weak assumptions, "
        "hidden complexity, and production risks"
    ),
    "pragmatist": (
        "pragmatist reviewer — focus on over-engineering, team fit, "
        "implementation burden, and whether a simpler alternative would be better"
    ),
    "security": (
        "security reviewer — focus on authentication, authorization, "
        "secret handling, data exposure, and abuse vectors"
    ),
    "ops": (
        "ops reviewer — focus on deployment, observability, rollback, "
        "failure handling, and runtime operability"
    ),
    "cost": (
        "cost reviewer — focus on infrastructure cost, token cost, "
        "service complexity, and long-term maintenance cost"
    ),
}


def get_reviewer_system_prompt(reviewer_type: str) -> str:
    """Return the system prompt for a given reviewer persona.

    Args:
        reviewer_type: Key into REVIEWER_DESCRIPTIONS (e.g. ``"skeptic"``).

    Returns:
        Formatted system prompt string.
    """
    description = REVIEWER_DESCRIPTIONS.get(reviewer_type, f"{reviewer_type} reviewer")
    return REVIEWER_SYSTEM_PROMPT_TEMPLATE.format(reviewer_description=description)


def build_builder_user_prompt(request: ReviewRequest) -> str:
    """Assemble the user prompt sent to the Builder model.

    Args:
        request: The incoming review request.

    Returns:
        Multi-part user prompt string.
    """
    parts = [f"Question: {request.question}"]
    if request.constraints:
        parts.append(f"Constraints: {', '.join(request.constraints)}")
    if request.context:
        if request.context.text:
            parts.append(f"Context: {request.context.text}")
        if request.context.diff:
            parts.append(f"Diff:\n{request.context.diff}")
        for fc in request.context.files:
            header = f"File: {fc.path}"
            if fc.selection:
                header += f" (selected: {fc.selection})"
            parts.append(f"{header}\n{fc.content}")
    return "\n\n".join(parts)


def build_reviewer_user_prompt(request: ReviewRequest, builder_result: BuilderResult) -> str:
    """Assemble the user prompt sent to a Reviewer model.

    Args:
        request: The incoming review request.
        builder_result: The Builder's output to be critiqued.

    Returns:
        Multi-part user prompt string.
    """
    parts = [
        f"Original question: {request.question}",
    ]
    if request.constraints:
        parts.append(f"Constraints: {', '.join(request.constraints)}")
    parts.append(f"Builder proposal:\n{json.dumps(builder_result.model_dump(), indent=2)}")
    return "\n\n".join(parts)
