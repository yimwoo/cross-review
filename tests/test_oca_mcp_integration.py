"""Integration tests for OCA auto-discovery through the MCP handler."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cross_review.mcp_server import handle_cross_review
from cross_review.oca_discovery import OCA_TOKEN_ENV
from cross_review.schemas import Confidence, FinalResult, Mode, Trace
from cross_review.sessions import SessionStore


def _make_final_result(**overrides):
    """Build a minimal FinalResult for testing."""
    defaults = dict(
        request_id="test-id",
        mode=Mode.REVIEW,
        selected_roles=[],
        consensus_findings=[],
        conflicting_findings=[],
        likely_shortcuts=[],
        final_recommendation="OCA review result.",
        decision_points=["Chose A over B"],
        trace=Trace(total_calls=2, total_tokens_actual=500, providers_used=["oca"]),
        confidence=Confidence.HIGH,
    )
    defaults.update(overrides)
    return FinalResult(**defaults)


@pytest.fixture()
def mock_orchestrator():
    """Mock Orchestrator that returns a minimal FinalResult."""
    orch = MagicMock()
    orch.run = AsyncMock(return_value=_make_final_result())
    return orch


@pytest.fixture()
def _clear_api_keys(monkeypatch):
    """Remove all default provider API keys so explicit config fails."""
    for key in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
        "OCA_TOKEN", OCA_TOKEN_ENV,
        "OCA_MODEL", "OCA_MODEL_BUILDER", "OCA_MODEL_SKEPTIC",
        "OCA_MODEL_PRAGMATIST", "OCA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# OCA fallback
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_clear_api_keys")
class TestOcaFallback:
    """MCP handler falls back to OCA when no explicit credentials."""

    async def test_uses_oca_when_no_config_credentials(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """Handler should discover OCA token and build ephemeral config."""
        monkeypatch.setenv("OCA_TOKEN", "test-oca-token")
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ) as mock_cls:
            result = await handle_cross_review(
                {"question": "Design a cache"}, session_store=store,
            )

        assert "OCA review result" in result["text"]
        # Verify OCA config was used
        config_arg = mock_cls.call_args[0][0]
        assert "oca" in config_arg.providers
        assert config_arg.providers["oca"].type == "openai_compatible"

    async def test_oca_token_cleaned_from_env_after_call(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """OCA token env var should be cleared after orchestrator completes."""
        monkeypatch.setenv("OCA_TOKEN", "test-oca-token")
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ):
            await handle_cross_review(
                {"question": "Design a cache"}, session_store=store,
            )

        assert os.environ.get(OCA_TOKEN_ENV) is None

    async def test_oca_token_cleaned_on_error(
        self, monkeypatch, tmp_path,
    ):
        """OCA token env var should be cleared even if orchestrator fails."""
        monkeypatch.setenv("OCA_TOKEN", "test-oca-token")
        store = SessionStore(base_dir=tmp_path)

        failing_orch = MagicMock()
        failing_orch.run = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=failing_orch,
        ):
            result = await handle_cross_review(
                {"question": "Test"}, session_store=store,
            )

        assert "Error" in result["text"]
        assert os.environ.get(OCA_TOKEN_ENV) is None

    async def test_returns_error_when_no_credentials_at_all(
        self, tmp_path, monkeypatch,
    ):
        """Handler should return an actionable error when nothing works."""
        # Ensure no Cline secrets or token files exist
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        store = SessionStore(base_dir=tmp_path)

        result = await handle_cross_review(
            {"question": "Test"}, session_store=store,
        )

        assert "No provider credentials found" in result["text"]
        assert "OCA_TOKEN" in result["text"]

    async def test_oca_from_cline_secrets(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """Handler should discover token from Cline's secrets.json."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        secrets_dir = tmp_path / ".cline" / "data"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "secrets.json").write_text(
            json.dumps({"ocaApiKey": "cline-oca-token"})
        )
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ) as mock_cls:
            result = await handle_cross_review(
                {"question": "Test"}, session_store=store,
            )

        assert "OCA review result" in result["text"]
        config_arg = mock_cls.call_args[0][0]
        assert "oca" in config_arg.providers


# ---------------------------------------------------------------------------
# Explicit config takes precedence
# ---------------------------------------------------------------------------


class TestExplicitConfigPrecedence:
    """Explicit config credentials should win over OCA discovery."""

    async def test_uses_explicit_config_when_keys_present(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """Handler should use normal config when API keys are set."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_API_KEY", "ok-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
        monkeypatch.setenv("OCA_TOKEN", "should-not-be-used")
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ) as mock_cls:
            result = await handle_cross_review(
                {"question": "Test"}, session_store=store,
            )

        assert "OCA review result" in result["text"]
        config_arg = mock_cls.call_args[0][0]
        # Should have default providers, not OCA
        assert "claude" in config_arg.providers
        assert "oca" not in config_arg.providers


# ---------------------------------------------------------------------------
# Per-role model env vars
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_clear_api_keys")
class TestPerRoleModelEnvVars:
    """OCA per-role model env vars should be respected."""

    async def test_per_role_models_from_env(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """Handler should pass per-role model overrides to build_oca_config."""
        monkeypatch.setenv("OCA_TOKEN", "test-token")
        monkeypatch.setenv("OCA_MODEL_BUILDER", "oca/gpt-5.3-codex")
        monkeypatch.setenv("OCA_MODEL_SKEPTIC", "oca/gpt-oss-120b")
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ) as mock_cls:
            await handle_cross_review(
                {"question": "Test"}, session_store=store,
            )

        config_arg = mock_cls.call_args[0][0]
        assert config_arg.roles["builder"].model == "oca/gpt-5.3-codex"
        assert config_arg.roles["skeptic_reviewer"].model == "oca/gpt-oss-120b"
        # pragmatist should keep default
        assert config_arg.roles["pragmatist_reviewer"].model == "oca/llama4"

    async def test_global_oca_model_as_fallback(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """OCA_MODEL should set all roles when per-role vars are absent."""
        monkeypatch.setenv("OCA_TOKEN", "test-token")
        monkeypatch.setenv("OCA_MODEL", "oca/custom-global")
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ) as mock_cls:
            await handle_cross_review(
                {"question": "Test"}, session_store=store,
            )

        config_arg = mock_cls.call_args[0][0]
        for role_name in ("builder", "skeptic_reviewer", "pragmatist_reviewer"):
            assert config_arg.roles[role_name].model == "oca/custom-global"

    async def test_custom_base_url(
        self, monkeypatch, mock_orchestrator, tmp_path,
    ):
        """OCA_BASE_URL should override the default endpoint."""
        monkeypatch.setenv("OCA_TOKEN", "test-token")
        monkeypatch.setenv("OCA_BASE_URL", "https://custom.example.com/v1")
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator,
        ) as mock_cls:
            await handle_cross_review(
                {"question": "Test"}, session_store=store,
            )

        config_arg = mock_cls.call_args[0][0]
        assert config_arg.providers["oca"].base_url == "https://custom.example.com/v1"
