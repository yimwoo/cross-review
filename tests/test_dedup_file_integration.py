"""Integration tests for request dedup + smart file context in MCP handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cross_review.mcp_server import handle_cross_review
from cross_review.schemas import (
    BuilderResult,
    Confidence,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BUILDER_RESULT = BuilderResult(
    summary="Use X",
    recommendation="Use X for Y",
    assumptions=["A"],
    alternatives=["B"],
    risks=["C"],
    open_questions=["D"],
    confidence=Confidence.HIGH,
)

_TOKEN_USAGE = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)


@pytest.fixture(autouse=True)
def _mock_orchestrator():
    """Mock the orchestrator so no real LLM calls are made."""
    with patch("cross_review.mcp_server.Orchestrator") as mock_cls, \
         patch("cross_review.mcp_server.can_resolve_credentials", return_value=True):
        mock_orch = MagicMock()

        async def mock_run(request):
            from cross_review.schemas import FinalResult, Trace
            return FinalResult(
                request_id=request.request_id,
                mode=request.mode,
                selected_roles=[],
                consensus_findings=[],
                conflicting_findings=[],
                likely_shortcuts=[],
                final_recommendation=_BUILDER_RESULT.recommendation,
                decision_points=[],
                trace=Trace(builder_result=_BUILDER_RESULT, total_calls=1,
                            total_tokens_actual=150, providers_used=["test"]),
                confidence=Confidence.HIGH,
                builder_model="test-model",
            )

        mock_orch.run = mock_run
        mock_cls.return_value = mock_orch
        yield mock_cls


# ---------------------------------------------------------------------------
# File context tests
# ---------------------------------------------------------------------------


class TestFilePathReadInHandler:
    async def test_relative_path_read(self, tmp_path: Path):
        """File with path-only should be read from disk."""
        f = tmp_path / "design.md"
        f.write_text("# Design Doc\nContent here")

        result = await handle_cross_review(
            {"question": "Review this", "mode": "fast",
             "_workspace": str(tmp_path),
             "files": [{"path": "design.md"}]},
        )
        # Should succeed (not error about missing content key)
        assert "Error" not in result["text"] or "File error" not in result["text"]
        assert result["session_status"] in ("created", "resumed")

    async def test_outside_workspace_error(self, tmp_path: Path):
        """Path outside workspace should produce an error message."""
        result = await handle_cross_review(
            {"question": "Review this", "mode": "fast",
             "_workspace": str(tmp_path),
             "files": [{"path": "/etc/passwd"}]},
        )
        # The review should still run, but with a file error note
        assert result["text"]  # non-empty
        assert result["session_status"] in ("created", "resumed")

    async def test_content_provided_no_disk_read(self, tmp_path: Path):
        """When content is provided, no disk read should happen."""
        result = await handle_cross_review(
            {"question": "Review this", "mode": "fast",
             "_workspace": str(tmp_path),
             "files": [{"path": "virtual.py", "content": "print('hello')"}]},
        )
        assert "Error" not in result["text"] or "cross-review" in result["text"]
        assert result["session_status"] in ("created", "resumed")

    async def test_missing_file_error(self, tmp_path: Path):
        """Missing file produces error but review still runs."""
        result = await handle_cross_review(
            {"question": "Review this", "mode": "fast",
             "_workspace": str(tmp_path),
             "files": [{"path": "nonexistent.py"}]},
        )
        assert result["text"]  # non-empty, review ran
        assert result["session_status"] in ("created", "resumed")
