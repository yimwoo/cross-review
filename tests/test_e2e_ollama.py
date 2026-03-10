"""Opt-in smoke tests against a local Ollama OpenAI-compatible endpoint."""

from __future__ import annotations

import os

import pytest

from cross_review.config import load_config_from_toml_string
from cross_review.orchestrator import Orchestrator
from cross_review.rendering import render_json
from cross_review.schemas import Mode, ReviewRequest


OLLAMA_SMOKE_ENABLED = os.environ.get("OLLAMA_SMOKE") == "1"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:1b")


@pytest.mark.skipif(
    not OLLAMA_SMOKE_ENABLED, reason="Set OLLAMA_SMOKE=1 to run Ollama smoke tests"
)
class TestE2EOllamaSmoke:
    """Smoke coverage for a local Ollama-backed provider."""

    async def test_fast_mode_returns_structured_json(self):
        cfg = load_config_from_toml_string(
            f"""\
[roles.builder]
provider = "ollama"
model = "{OLLAMA_MODEL}"
"""
        )
        orch = Orchestrator(cfg)

        result = await orch.run(
            ReviewRequest(
                question="Design a small cache invalidation strategy for a CRUD app",
                mode=Mode.FAST,
            )
        )

        payload = render_json(result)
        assert '"mode": "fast"' in payload
        assert '"final_recommendation"' in payload
