"""End-to-end tests for host-managed auth flow.

Simulates cross-review running inside an MCP host (e.g. Claude Code) where
the host provides LLM calls via sampling. Uses a fake MCP server that returns
canned JSON responses, exercising the full pipeline:

  handle_cross_review(server=fake_server)
    → resolve_auth_mode → host_managed
    → Orchestrator(provider_factory=sampling_factory)
    → SamplingAdapter.call() → fake_server.create_message()
    → reconciliation → rendering
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cross_review.config import AppConfig
from cross_review.mcp_server import handle_cross_review
from cross_review.orchestrator import Orchestrator
from cross_review.providers.sampling import SamplingAdapter
from cross_review.schemas import (
    Confidence,
    FinalResult,
    Mode,
    ReviewRequest,
)


@pytest.fixture(autouse=True)
def _disable_oca_discovery():
    """Prevent OCA auto-discovery from interfering with host-managed tests."""
    with patch("cross_review.mcp_server.find_oca_token_with_refresh", return_value=None), \
         patch("cross_review.mcp_server.can_resolve_credentials", return_value=False):
        yield


# ---------------------------------------------------------------------------
# Canned responses (same structure as test_integration.py)
# ---------------------------------------------------------------------------

_BUILDER_JSON = json.dumps(
    {
        "summary": "Use Redis for caching with TTL-based eviction",
        "recommendation": "Deploy Redis cluster with 15-minute TTL",
        "assumptions": ["Single region deployment", "Read-heavy workload"],
        "alternatives": ["Memcached", "Application-level cache"],
        "risks": ["Cache stampede on cold start"],
        "open_questions": ["Expected cache hit ratio?"],
        "confidence": "high",
    }
)

_REVIEWER_JSON = json.dumps(
    {
        "overall_confidence": "medium",
        "findings": [
            {
                "category": "scalability",
                "severity": "medium",
                "target": "Cache layer",
                "summary": "No circuit breaker for Redis failures",
                "quote": None,
                "shortcut_risk": False,
                "rationale": "Redis downtime would cascade to all services",
                "recommendation": "Add circuit breaker with local fallback",
                "confidence": "medium",
            },
            {
                "category": "security",
                "severity": "high",
                "target": "Cache keys",
                "summary": "Tenant data leakage via shared cache namespace",
                "quote": None,
                "shortcut_risk": True,
                "rationale": "Cache keys lack tenant prefix isolation",
                "recommendation": "Prefix all keys with tenant ID",
                "confidence": "high",
            },
        ],
    }
)


def _make_fake_mcp_server(call_count: list[int] | None = None):
    """Create a fake MCP server that returns canned responses.

    First call returns builder JSON, subsequent calls return reviewer JSON.
    This mimics the host (e.g. Claude Code) making LLM calls on our behalf.
    """
    if call_count is None:
        call_count = [0]

    async def fake_create_message(messages, system_prompt, max_tokens, **kwargs):
        call_count[0] += 1
        result = MagicMock()
        # Determine response type from system prompt content
        if "BuilderResult" in system_prompt or call_count[0] == 1:
            result.content.text = _BUILDER_JSON
        else:
            result.content.text = _REVIEWER_JSON
        result.model = "claude-sonnet-4-20250514"
        return result

    server = MagicMock()
    server.create_message = AsyncMock(side_effect=fake_create_message)
    return server, call_count


# ---------------------------------------------------------------------------
# E2E: Full pipeline via MCP handler with host-managed auth
# ---------------------------------------------------------------------------


class TestE2EHostManagedReview:
    """End-to-end: handle_cross_review with a fake MCP server, no API keys."""

    async def test_review_mode_via_host_sampling(self, monkeypatch):
        """Full review via host sampling: builder + 1 reviewer, rendered output."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        server, call_count = _make_fake_mcp_server()

        result_text = (await handle_cross_review(
            {"question": "Design a caching layer for multi-tenant SaaS"},
            server=server,
        ))["text"]

        # Should have rendered markdown output
        assert "Cross-Review Result" in result_text
        assert "review" in result_text.lower()

        # Should include the builder recommendation
        assert "Redis" in result_text

        # Host-managed warning is in trace.warnings (visible via verbose or JSON)
        # Default markdown hides trace diagnostics

        # Should have made 2 sampling calls (builder + 1 reviewer)
        assert call_count[0] == 2

    async def test_fast_mode_via_host_sampling(self, monkeypatch):
        """Fast mode via host: builder only, 1 sampling call."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        server, call_count = _make_fake_mcp_server()

        result_text = (await handle_cross_review(
            {"question": "Name this service", "mode": "fast"},
            server=server,
        ))["text"]

        assert "Cross-Review Result" in result_text
        assert "fast" in result_text.lower()
        assert call_count[0] == 1
        # Host-managed warning is in trace.warnings (visible via verbose or JSON)

    async def test_json_output_via_host_sampling(self, monkeypatch):
        """JSON output format works with host-managed auth."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        server, _ = _make_fake_mcp_server()

        result_text = (await handle_cross_review(
            {
                "question": "Design a cache",
                "mode": "fast",
                "output_format": "json",
            },
            server=server,
        ))["text"]

        parsed = json.loads(result_text)
        assert "final_recommendation" in parsed
        assert parsed["mode"] == "fast"
        # Warning should appear in trace.warnings
        assert any("Single-provider" in w for w in parsed["trace"]["warnings"])

    async def test_reviewer_cap_in_deep_mode(self, monkeypatch):
        """Deep mode in host-managed should cap at 1 reviewer (not 2)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        server, call_count = _make_fake_mcp_server()

        result_text = (await handle_cross_review(
            {"question": "Design production auth flow", "mode": "deep"},
            server=server,
        ))["text"]

        # In host-managed mode, max_reviewers is capped to 1
        # So deep behaves like review: builder + 1 reviewer = 2 calls
        assert call_count[0] == 2
        # Host-managed warning is in trace.warnings (visible via verbose or JSON)


class TestE2EHostManagedFallback:
    """End-to-end: auto-detection and fallback scenarios."""

    async def test_auto_detects_host_managed_when_no_keys(self, monkeypatch):
        """With no API keys and a server with sampling, auto → host_managed."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        server, _ = _make_fake_mcp_server()

        result_text = (await handle_cross_review(
            {"question": "Test auto-detection", "mode": "fast"},
            server=server,
        ))["text"]

        # Should work (no error); host-managed warning is in trace.warnings
        assert "Error" not in result_text

    async def test_auto_prefers_provider_managed_with_keys(self, monkeypatch):
        """With API keys set, auto → provider_managed even with server present."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        server, _ = _make_fake_mcp_server()

        # Mock Orchestrator since provider_managed would try real API calls
        from cross_review.schemas import Trace

        mock_result = FinalResult(
            request_id="test",
            mode=Mode.FAST,
            selected_roles=[],
            consensus_findings=[],
            conflicting_findings=[],
            likely_shortcuts=[],
            final_recommendation="Provider-managed result.",
            decision_points=[],
            trace=Trace(
                total_calls=1,
                total_tokens_actual=100,
                providers_used=["claude"],
            ),
            confidence=Confidence.HIGH,
        )
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=mock_result)

        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orch):
            result_text = (await handle_cross_review(
                {"question": "Test", "mode": "fast"},
                server=server,
            ))["text"]

        # Should NOT have host-managed warning
        assert "Single-provider" not in result_text

    async def test_no_server_no_keys_cli_mode(self):
        """Without server (CLI mode) and no credentials, returns credential error."""
        result_text = (await handle_cross_review({"question": "Test"}))["text"]

        # Should get the credential error (no server for host-managed fallback)
        assert "Error" in result_text
        assert "No provider credentials" in result_text

    async def test_server_without_sampling_auto_falls_back(self, monkeypatch):
        """Server without create_message: auto mode can't use host_managed."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        # Server exists but doesn't have create_message
        server = MagicMock(spec=[])  # empty spec = no attributes

        result_text = (await handle_cross_review(
            {"question": "Test"},
            server=server,
        ))["text"]

        # Should get auth error (no keys, no sampling)
        assert "Error" in result_text
        assert "No API keys" in result_text


class TestE2ESamplingAdapterIntegration:
    """Test SamplingAdapter integration with the Orchestrator directly."""

    async def test_orchestrator_with_sampling_factory_review_mode(self):
        """Orchestrator with sampling factory runs full review pipeline."""
        server, call_count = _make_fake_mcp_server()

        def sampling_factory(provider_name: str, model: str):
            return SamplingAdapter(
                server=server,
                host_provider="claude",
                model_hint=model or "claude-sonnet-4-20250514",
            )

        config = AppConfig()
        orch = Orchestrator(config, provider_factory=sampling_factory)

        request = ReviewRequest(
            request_id="e2e-sampling-001",
            question="Design a caching layer",
            mode=Mode.REVIEW,
        )
        request.budget.max_reviewers = 1  # simulate host-managed cap

        result = await orch.run(request)

        assert isinstance(result, FinalResult)
        assert result.mode == Mode.REVIEW
        assert result.trace.total_calls == 2
        assert result.trace.builder_result is not None
        assert "Redis" in result.trace.builder_result.recommendation
        assert call_count[0] == 2

        # Provider names should reflect host delegation
        assert any("claude-via-host" in p for p in result.trace.providers_used)

    async def test_orchestrator_with_sampling_factory_fast_mode(self):
        """Orchestrator with sampling factory in fast mode — single call."""
        server, call_count = _make_fake_mcp_server()

        def sampling_factory(provider_name: str, model: str):
            return SamplingAdapter(
                server=server,
                host_provider="claude",
                model_hint=model,
            )

        config = AppConfig()
        orch = Orchestrator(config, provider_factory=sampling_factory)

        request = ReviewRequest(
            question="Name this service",
            mode=Mode.FAST,
        )
        result = await orch.run(request)

        assert result.trace.total_calls == 1
        assert call_count[0] == 1
        assert result.trace.builder_result is not None

    async def test_sampling_adapter_satisfies_provider_protocol(self):
        """SamplingAdapter should be recognized as a ProviderAdapter."""
        from cross_review.providers.base import ProviderAdapter

        adapter = SamplingAdapter(
            server=MagicMock(),
            host_provider="claude",
            model_hint="claude-sonnet-4-20250514",
        )
        assert isinstance(adapter, ProviderAdapter)
