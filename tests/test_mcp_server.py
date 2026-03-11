"""Tests for MCP server module."""

import builtins
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cross_review.mcp_server import TOOL_DEFINITION, handle_cross_review, run_server
from cross_review.schemas import Confidence, FinalResult, Mode, Trace
from cross_review.sessions import SessionStore


class TestToolDefinition:
    """Tests for the MCP tool definition."""

    def test_tool_name(self):
        """Tool name should be cross_review."""
        assert TOOL_DEFINITION["name"] == "cross_review"

    def test_required_fields(self):
        """Only question should be required."""
        assert TOOL_DEFINITION["inputSchema"]["required"] == ["question"]

    def test_mode_enum(self):
        """Mode should have correct enum values."""
        mode_prop = TOOL_DEFINITION["inputSchema"]["properties"]["mode"]
        assert set(mode_prop["enum"]) == {"fast", "review", "arbitration", "auto"}

    def test_output_format_enum(self):
        """Output format should have correct enum values."""
        fmt_prop = TOOL_DEFINITION["inputSchema"]["properties"]["output_format"]
        assert set(fmt_prop["enum"]) == {"markdown", "json", "summary"}


class TestHandleCrossReview:
    """Tests for the cross_review tool handler."""

    @pytest.fixture()
    def mock_orchestrator(self):
        """Create a mock orchestrator that returns a minimal FinalResult."""
        result = FinalResult(
            request_id="test-id",
            mode=Mode.REVIEW,
            selected_roles=[],
            consensus_findings=[],
            conflicting_findings=[],
            likely_shortcuts=[],
            final_recommendation="Test recommendation.",
            decision_points=[],
            trace=Trace(
                total_calls=1,
                total_tokens_actual=100,
                providers_used=["claude"],
            ),
            confidence=Confidence.HIGH,
        )
        orch = MagicMock()
        orch.run = AsyncMock(return_value=result)
        return orch

    async def test_minimal_arguments(self, mock_orchestrator, tmp_path):
        """Handler should work with just a question."""
        store = SessionStore(base_dir=tmp_path)
        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review({"question": "Design a cache"}, session_store=store)

        assert "Test recommendation" in result["text"]
        assert result["session_id"].startswith("crs_")
        assert result["session_status"] == "created"
        mock_orchestrator.run.assert_called_once()

    async def test_with_mode(self, mock_orchestrator, tmp_path):
        """Handler should pass mode to ReviewRequest."""
        store = SessionStore(base_dir=tmp_path)
        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review(
                {"question": "Design a cache", "mode": "arbitration"}, session_store=store
            )

        assert "Test recommendation" in result["text"]
        call_args = mock_orchestrator.run.call_args
        request = call_args[0][0]
        assert request.mode.value == "arbitration"

    async def test_with_context(self, mock_orchestrator, tmp_path):
        """Handler should pass context to ReviewRequest."""
        store = SessionStore(base_dir=tmp_path)
        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review(
                {"question": "Review this", "context": "Some file content"}, session_store=store
            )

        assert "Test recommendation" in result["text"]
        call_args = mock_orchestrator.run.call_args
        request = call_args[0][0]
        assert request.context is not None
        assert "Some file content" in request.context.text

    async def test_with_constraints(self, mock_orchestrator, tmp_path):
        """Handler should pass constraints to ReviewRequest."""
        store = SessionStore(base_dir=tmp_path)
        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review(
                {
                    "question": "Review this",
                    "constraints": ["Must use PostgreSQL", "No ORMs"],
                }, session_store=store
            )

        assert "Test recommendation" in result["text"]
        call_args = mock_orchestrator.run.call_args
        request = call_args[0][0]
        assert request.constraints == ["Must use PostgreSQL", "No ORMs"]

    async def test_json_output_format(self, mock_orchestrator, tmp_path):
        """Handler should respect output_format parameter."""
        store = SessionStore(base_dir=tmp_path)
        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review(
                {"question": "Review this", "output_format": "json"}, session_store=store
            )

        # JSON output should be parseable
        parsed = json.loads(result["text"])
        assert "final_recommendation" in parsed

    async def test_default_mode_is_review(self, mock_orchestrator, tmp_path):
        """Default mode should be review when not specified."""
        store = SessionStore(base_dir=tmp_path)
        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            await handle_cross_review({"question": "Test"}, session_store=store)

        call_args = mock_orchestrator.run.call_args
        request = call_args[0][0]
        assert request.mode.value == "review"

    async def test_error_handling(self, tmp_path):
        """Handler should return error message on failure."""
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(side_effect=RuntimeError("Provider unavailable"))
        store = SessionStore(base_dir=tmp_path)

        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orch):
            result = await handle_cross_review({"question": "Test"}, session_store=store)

        assert "Error" in result["text"]
        assert "Provider unavailable" in result["text"]


class TestRunServer:
    """Tests for MCP server startup."""

    def test_missing_mcp_dependency_shows_source_install_command(self):
        """Missing mcp dependency should recommend source install instructions."""
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("mcp"):
                raise ImportError("No module named 'mcp'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(SystemExit) as exc_info:
                run_server()

        assert (
            'Install with: pip install "cross-review[mcp] @ '
            'git+https://github.com/yimwoo/cross-review.git"'
        ) in str(exc_info.value)


class TestHostManagedAuth:
    """Tests for host-managed auth in MCP handler."""

    @pytest.fixture()
    def mock_orchestrator(self):
        """Create a mock orchestrator that returns a minimal FinalResult."""
        result = FinalResult(
            request_id="test-id",
            mode=Mode.REVIEW,
            selected_roles=[],
            consensus_findings=[],
            conflicting_findings=[],
            likely_shortcuts=[],
            final_recommendation="Test recommendation.",
            decision_points=[],
            trace=Trace(
                total_calls=1,
                total_tokens_actual=100,
                providers_used=["claude-via-host"],
            ),
            confidence=Confidence.HIGH,
        )
        orch = MagicMock()
        orch.run = AsyncMock(return_value=result)
        return orch

    async def test_host_managed_uses_sampling_factory(
        self, mock_orchestrator, monkeypatch, tmp_path,
    ):
        """When host-managed, Orchestrator should receive a custom provider_factory."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        mock_server = MagicMock()
        mock_server.create_message = AsyncMock()
        store = SessionStore(base_dir=tmp_path)

        with patch(
            "cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator
        ) as mock_orch_cls:
            await handle_cross_review(
                {"question": "Test"},
                server=mock_server,
                session_store=store,
            )

        # Orchestrator should have been called with a provider_factory
        call_kwargs = mock_orch_cls.call_args
        assert call_kwargs is not None
        # Check that provider_factory was passed (either as kwarg or in the call)
        if call_kwargs.kwargs:
            assert "provider_factory" in call_kwargs.kwargs
        else:
            # Positional args: config, provider_factory
            assert len(call_kwargs.args) >= 2 or "provider_factory" in (call_kwargs.kwargs or {})

    async def test_host_managed_warning_in_output(self, mock_orchestrator, monkeypatch, tmp_path):
        """Host-managed mode should include a warning in the output."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        mock_server = MagicMock()
        mock_server.create_message = AsyncMock()
        store = SessionStore(base_dir=tmp_path)

        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review(
                {"question": "Test"},
                server=mock_server,
                session_store=store,
            )

        text = result["text"]
        assert "Single-provider" in text or "single-provider" in text

    async def test_provider_managed_when_keys_set(self, mock_orchestrator, monkeypatch, tmp_path):
        """When API keys are set, should use provider_managed even with server."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        mock_server = MagicMock()
        mock_server.create_message = AsyncMock()
        store = SessionStore(base_dir=tmp_path)

        with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
            result = await handle_cross_review(
                {"question": "Test"},
                server=mock_server,
                session_store=store,
            )

        # Should not have single-provider warning
        assert "Single-provider" not in result["text"]

    async def test_provider_managed_with_custom_provider_key(
        self, mock_orchestrator, monkeypatch, tmp_path,
    ):
        """Custom provider keys from config should trigger provider-managed auth."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

        mock_server = MagicMock()
        mock_server.create_message = AsyncMock()
        store = SessionStore(base_dir=tmp_path)

        with patch("cross_review.mcp_server.load_config") as mock_load_config:
            cfg = MagicMock()
            cfg.providers = {
                "deepseek": MagicMock(api_key_env="DEEPSEEK_API_KEY"),
            }
            mock_load_config.return_value = cfg

            with patch("cross_review.mcp_server.Orchestrator", return_value=mock_orchestrator):
                result = await handle_cross_review(
                    {"question": "Test"},
                    server=mock_server,
                    session_store=store,
                )

        assert "Single-provider" not in result["text"]
