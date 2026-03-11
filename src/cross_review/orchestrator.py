"""Core orchestrator tying together routing, provider calls, validation,
and reconciliation. Ref: design doc §8, §16.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Callable, Optional

from pydantic import BaseModel

from cross_review.budget import BudgetGuard
from cross_review.config import AppConfig, RoleConfig
from cross_review.prompts import (
    BUILDER_SYSTEM_PROMPT,
    build_builder_user_prompt,
    build_reviewer_user_prompt,
    get_reviewer_system_prompt,
)
from cross_review.providers.base import ProviderAdapter, create_provider
from cross_review.reconciliation import Reconciler
from cross_review.retry import with_retry
from cross_review.router import choose_mode
from cross_review.schemas import (
    BuilderResult,
    BudgetConfig,
    Confidence,
    FinalResult,
    Finding,
    Mode,
    ReviewerResult,
    ReviewerType,
    ReviewRequest,
    Trace,
)
from cross_review.tracing import RunTracer
from cross_review.validation import inject_finding_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Raw reviewer output schema (no system-injected fields)
# ---------------------------------------------------------------------------


class RawReviewerOutput(BaseModel):
    """Schema for raw reviewer output (no system-injected fields)."""

    overall_confidence: str
    findings: list[dict]


# ---------------------------------------------------------------------------
# Role-name to ReviewerType mapping
# ---------------------------------------------------------------------------

_ROLE_TO_REVIEWER_TYPE: dict[str, ReviewerType] = {
    "critic": ReviewerType.CRITIC,
    "advisor": ReviewerType.ADVISOR,
    "security_reviewer": ReviewerType.SECURITY,
    "ops_reviewer": ReviewerType.OPS,
    "cost_reviewer": ReviewerType.COST,
}

_REVIEWER_TYPE_KEY: dict[str, str] = {
    "critic": "critic",
    "advisor": "advisor",
    "security_reviewer": "security",
    "ops_reviewer": "ops",
    "cost_reviewer": "cost",
}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Ties together routing, provider calls, validation, and reconciliation.

    Parameters
    ----------
    config:
        Application configuration (roles, budget defaults, router settings).
    provider_factory:
        Callable ``(provider_name, model) -> ProviderAdapter``.
        Defaults to ``create_provider`` from ``providers.base`` bound to
        ``config.providers``.
    on_event:
        Optional callback for progress events.  Receives a single ``str``.
    """

    def __init__(
        self,
        config: AppConfig,
        provider_factory: Optional[Callable[[str, str | None], ProviderAdapter]] = None,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self._config = config
        self._provider_factory = provider_factory or partial(
            create_provider,
            providers=self._config.providers,
        )
        self._on_event = on_event

    # -- public entry point ---------------------------------------------------

    async def run(self, request: ReviewRequest) -> FinalResult:  # pylint: disable=too-many-locals
        """Execute the full orchestration pipeline.

        1. Route to determine mode
        2. Call builder (with retry)
        3. If fast mode: return builder result wrapped in FinalResult
        4. Determine reviewer roles
        5. Run reviewers (parallel for arbitration via asyncio.gather)
        6. Handle partial failures
        7. Reconcile results
        8. Return FinalResult with trace data
        """
        tracer = RunTracer(request.request_id, on_event=self._on_event)
        budget_guard = BudgetGuard(
            BudgetConfig(
                max_total_calls=request.budget.max_total_calls,
                max_reviewers=request.budget.max_reviewers,
                soft_token_limit=request.budget.soft_token_limit,
                hard_token_limit=request.budget.hard_token_limit,
                orchestration_timeout_seconds=request.budget.orchestration_timeout_seconds,
            )
        )

        # -- Step 1: route ---------------------------------------------------
        mode = choose_mode(request, self._config.router)
        tracer.emit(f"routing complete → {mode.value}")

        # -- Step 2: call builder (with retry) --------------------------------
        builder_role: RoleConfig = self._config.roles["builder"]
        builder_provider = self._provider_factory(builder_role.provider, builder_role.model)
        tracer.emit("builder running")

        builder_result, builder_usage = await with_retry(
            lambda: builder_provider.call(
                system_prompt=BUILDER_SYSTEM_PROMPT,
                user_prompt=build_builder_user_prompt(request),
                response_schema=BuilderResult,
            )
        )
        # Ensure we have a BuilderResult (the mock may already return one)
        if not isinstance(builder_result, BuilderResult):
            builder_result = BuilderResult.model_validate(
                builder_result.model_dump()
                if isinstance(builder_result, BaseModel)
                else builder_result
            )

        budget_guard.record_call(builder_usage)
        tracer.record_call(builder_provider.name(), builder_usage)
        tracer.record_builder_result(builder_result)
        tracer.emit("builder complete")

        builder_model_name = builder_provider.name()

        # -- Step 3: fast mode early return -----------------------------------
        if mode == Mode.FAST:
            trace = tracer.to_trace()
            return FinalResult(
                request_id=request.request_id,
                mode=mode,
                selected_roles=[],
                consensus_findings=[],
                conflicting_findings=[],
                likely_shortcuts=[],
                final_recommendation=builder_result.recommendation,
                decision_points=[],
                trace=trace,
                confidence=builder_result.confidence,
                builder_model=builder_model_name,
            )

        # -- Step 4: determine reviewer roles ---------------------------------
        reviewer_roles = self._select_reviewer_roles(mode, request.budget)
        tracer.emit(f"reviewers selected: {[r for r, _ in reviewer_roles]}")

        # -- Step 5 & 6: run reviewers ----------------------------------------
        reviewer_results = await self._run_reviewers(
            reviewer_roles=reviewer_roles,
            request=request,
            builder_result=builder_result,
            budget_guard=budget_guard,
            tracer=tracer,
        )

        # -- Step 7: reconcile ------------------------------------------------
        reconciler = Reconciler()
        final_result = reconciler.reconcile(
            builder_result=builder_result,
            reviewer_results=reviewer_results,
            mode=mode,
            request_id=request.request_id,
            builder_model=builder_model_name,
        )

        # -- Step 8: merge trace data -----------------------------------------
        orchestrator_trace = tracer.to_trace()
        merged_trace = Trace(
            total_calls=orchestrator_trace.total_calls,
            total_tokens_actual=orchestrator_trace.total_tokens_actual,
            providers_used=orchestrator_trace.providers_used,
            builder_result=orchestrator_trace.builder_result,
            warnings=list(
                dict.fromkeys(orchestrator_trace.warnings + final_result.trace.warnings)
            ),
        )
        final_result = final_result.model_copy(update={"trace": merged_trace})

        tracer.emit("orchestration complete")
        return final_result

    # -- private helpers ------------------------------------------------------

    def _select_reviewer_roles(
        self,
        mode: Mode,
        budget: BudgetConfig,
    ) -> list[tuple[str, RoleConfig]]:
        """Return the list of (role_name, role_config) pairs for the given mode.

        - REVIEW: ["critic"] only.
        - ARBITRATION: all configured reviewer roles, up to max_reviewers.
        """
        if mode == Mode.REVIEW:
            role_name = "critic"
            role_cfg = self._config.roles.get(role_name)
            if role_cfg is None:
                raise ValueError("No critic role configured")
            return [(role_name, role_cfg)]

        # ARBITRATION: collect all reviewer roles (those in the type map), limited by budget
        reviewer_roles: list[tuple[str, RoleConfig]] = []
        for name, cfg in self._config.roles.items():
            if name in _ROLE_TO_REVIEWER_TYPE:
                reviewer_roles.append((name, cfg))
                if len(reviewer_roles) >= budget.max_reviewers:
                    break
        return reviewer_roles

    async def _run_reviewers(  # pylint: disable=too-many-positional-arguments,too-many-locals
        self,
        reviewer_roles: list[tuple[str, RoleConfig]],
        request: ReviewRequest,
        builder_result: BuilderResult,
        budget_guard: BudgetGuard,
        tracer: RunTracer,
    ) -> list[ReviewerResult]:
        """Run reviewer calls, in parallel for arbitration mode.

        Handles partial failures: if one reviewer fails, continue with
        degraded output (§16.3).
        """
        tracer.emit("reviewers running")

        async def _call_reviewer(
            role_name: str,
            role_cfg: RoleConfig,
        ) -> ReviewerResult:
            reviewer_type_key = _REVIEWER_TYPE_KEY.get(
                role_name, role_name.replace("_reviewer", "")
            )
            reviewer_type = _ROLE_TO_REVIEWER_TYPE.get(role_name, ReviewerType.CRITIC)
            provider = self._provider_factory(role_cfg.provider, role_cfg.model)
            source_model = provider.name()

            raw_output, usage = await with_retry(
                lambda: provider.call(
                    system_prompt=get_reviewer_system_prompt(reviewer_type_key),
                    user_prompt=build_reviewer_user_prompt(request, builder_result),
                    response_schema=RawReviewerOutput,
                )
            )

            budget_guard.record_call(usage)
            tracer.record_call(source_model, usage)

            # Parse raw output into validated ReviewerResult
            if isinstance(raw_output, RawReviewerOutput):
                raw_dict = raw_output.model_dump()
            elif isinstance(raw_output, BaseModel):
                raw_dict = raw_output.model_dump()
            else:
                raw_dict = raw_output  # type: ignore[assignment]

            # Re-build findings with proper metadata injection
            findings_raw: list[dict] = raw_dict.get("findings", [])
            injected_findings: list[Finding] = [
                inject_finding_metadata(
                    raw_finding=f,
                    source_model=source_model,
                    reviewer_type=reviewer_type_key,
                )
                for f in findings_raw
            ]

            return ReviewerResult(
                reviewer_type=reviewer_type,
                overall_confidence=Confidence(raw_dict.get("overall_confidence", "medium")),
                findings=injected_findings,
                source_model=source_model,
            )

        # Launch reviewer tasks
        if len(reviewer_roles) == 1:
            # Single reviewer, no need for gather
            role_name, role_cfg = reviewer_roles[0]
            result = await _call_reviewer(role_name, role_cfg)
            tracer.emit("reviewer complete")
            return [result]

        # Multiple reviewers: run in parallel with asyncio.gather
        tasks = [_call_reviewer(role_name, role_cfg) for role_name, role_cfg in reviewer_roles]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions (partial failure handling §16.3)
        reviewer_results: list[ReviewerResult] = []
        for i, res in enumerate(raw_results):
            if isinstance(res, BaseException):
                role_name = reviewer_roles[i][0]
                warning = f"Reviewer {role_name} failed: {type(res).__name__}: {res}"
                logger.warning(warning)
                tracer.record_warning(warning)
                tracer.mark_degraded()
            else:
                reviewer_results.append(res)

        tracer.emit("reviewers complete")
        return reviewer_results
