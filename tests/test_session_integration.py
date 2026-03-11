"""Integration tests for session continuity through the MCP handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cross_review.mcp_server import handle_cross_review
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
        final_recommendation="Use option A.",
        decision_points=["Chose option A over B"],
        trace=Trace(total_calls=1, total_tokens_actual=100, providers_used=["mock"]),
        confidence=Confidence.HIGH,
    )
    defaults.update(overrides)
    return FinalResult(**defaults)


def _mock_orchestrator(result=None):
    orch = MagicMock()
    orch.run = AsyncMock(return_value=result or _make_final_result())
    return orch


class TestFirstCallCreatesSession:
    @pytest.mark.asyncio
    async def test_returns_session_id(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            result = await handle_cross_review(
                {"question": "Design a cache"}, session_store=store
            )

        assert result["session_id"].startswith("crs_")
        assert result["session_status"] == "created"
        assert result["memory_used"] is False

    @pytest.mark.asyncio
    async def test_persists_round_on_disk(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            result = await handle_cross_review(
                {"question": "Design a cache"}, session_store=store
            )

        rounds = store.load_rounds(result["session_id"])
        assert len(rounds) == 1
        assert rounds[0].round_number == 1
        assert rounds[0].request_payload["question"] == "Design a cache"


class TestFollowUpLoadsMemory:
    @pytest.mark.asyncio
    async def test_second_call_resumes_session(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            first = await handle_cross_review(
                {"question": "Design a cache"}, session_store=store
            )

        sid = first["session_id"]

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            second = await handle_cross_review(
                {"question": "What about Redis?", "session_id": sid},
                session_store=store,
            )

        assert second["session_id"] == sid
        assert second["session_status"] == "resumed"
        assert second["memory_used"] is True

    @pytest.mark.asyncio
    async def test_session_memory_injected_into_context(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        result1 = _make_final_result(decision_points=["Use PostgreSQL"])
        orch1 = _mock_orchestrator(result1)

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch1):
            first = await handle_cross_review(
                {"question": "Pick a database"}, session_store=store
            )

        sid = first["session_id"]
        orch2 = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch2):
            await handle_cross_review(
                {"question": "Scaling strategy?", "session_id": sid},
                session_store=store,
            )

        # Check that the second call's request context includes memory
        call_args = orch2.run.call_args[0][0]
        assert call_args.context is not None
        assert "Use PostgreSQL" in call_args.context.text

    @pytest.mark.asyncio
    async def test_accumulates_rounds(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            first = await handle_cross_review(
                {"question": "Q1"}, session_store=store
            )

        sid = first["session_id"]

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            await handle_cross_review(
                {"question": "Q2", "session_id": sid}, session_store=store
            )

        rounds = store.load_rounds(sid)
        assert len(rounds) == 2
        assert rounds[0].request_payload["question"] == "Q1"
        assert rounds[1].request_payload["question"] == "Q2"


class TestNewSessionBranching:
    @pytest.mark.asyncio
    async def test_new_session_creates_separate(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            first = await handle_cross_review(
                {"question": "Q1"}, session_store=store
            )

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            second = await handle_cross_review(
                {"question": "Q2", "new_session": True}, session_store=store
            )

        assert first["session_id"] != second["session_id"]
        assert second["session_status"] == "created"

    @pytest.mark.asyncio
    async def test_new_session_ignores_provided_session_id(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            first = await handle_cross_review(
                {"question": "Q1"}, session_store=store
            )

        sid = first["session_id"]

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            second = await handle_cross_review(
                {"question": "Q2", "session_id": sid, "new_session": True},
                session_store=store,
            )

        assert second["session_id"] != sid
        assert second["session_status"] == "created"


class TestFileAttachments:
    @pytest.mark.asyncio
    async def test_files_included_in_context(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            await handle_cross_review(
                {
                    "question": "Review this",
                    "files": [
                        {"path": "src/main.py", "content": "print('hello')"},
                    ],
                },
                session_store=store,
            )

        call_args = orch.run.call_args[0][0]
        assert call_args.context is not None
        assert "src/main.py" in call_args.context.text
        assert "print('hello')" in call_args.context.text

    @pytest.mark.asyncio
    async def test_file_paths_recorded_in_round(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            result = await handle_cross_review(
                {
                    "question": "Review",
                    "files": [{"path": "a.py", "content": "x=1"}],
                },
                session_store=store,
            )

        rounds = store.load_rounds(result["session_id"])
        assert rounds[0].request_payload["files"] == ["a.py"]


class TestPriorContext:
    @pytest.mark.asyncio
    async def test_prior_context_injected(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        orch = _mock_orchestrator()

        with patch("cross_review.mcp_server.Orchestrator", return_value=orch):
            await handle_cross_review(
                {
                    "question": "Continue",
                    "prior_context": "We decided to use Rust",
                },
                session_store=store,
            )

        call_args = orch.run.call_args[0][0]
        assert "We decided to use Rust" in call_args.context.text
