"""Tests for SamplingAdapter (MCP sampling-based provider)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from cross_review.providers.sampling import SamplingAdapter
from cross_review.schemas import TokenUsage


class SampleResponse(BaseModel):
    """Simple test response schema."""

    answer: str
    confidence: str


class TestSamplingAdapterName:
    """Tests for SamplingAdapter.name()."""

    def test_name_includes_via_host(self):
        """Name should indicate host-managed delegation."""
        adapter = SamplingAdapter(
            server=MagicMock(), host_provider="claude", model_hint="claude-sonnet-4-5"
        )
        assert adapter.name() == "claude-via-host"

    def test_name_with_openai_host(self):
        """Name should reflect the host provider."""
        adapter = SamplingAdapter(server=MagicMock(), host_provider="openai", model_hint="gpt-4.1")
        assert adapter.name() == "openai-via-host"


class TestSamplingAdapterCall:
    """Tests for SamplingAdapter.call()."""

    @pytest.fixture()
    def mock_server(self):
        """Create a mock MCP server with create_message support."""
        server = MagicMock()
        return server

    @pytest.fixture()
    def sample_response_text(self):
        """Return valid JSON matching SampleResponse schema."""
        return json.dumps({"answer": "Use Redis", "confidence": "high"})

    async def test_call_returns_validated_model(self, mock_server, sample_response_text):
        """call() should return a validated Pydantic model."""
        mock_result = MagicMock()
        mock_result.content = MagicMock()
        mock_result.content.text = sample_response_text
        mock_result.model = "claude-sonnet-4-5"
        mock_server.create_message = AsyncMock(return_value=mock_result)

        adapter = SamplingAdapter(
            server=mock_server, host_provider="claude", model_hint="claude-sonnet-4-5"
        )
        result, usage = await adapter.call(
            system_prompt="You are a builder.",
            user_prompt="Design a cache",
            response_schema=SampleResponse,
        )

        assert isinstance(result, SampleResponse)
        assert result.answer == "Use Redis"
        assert result.confidence == "high"

    async def test_call_sends_correct_messages(self, mock_server, sample_response_text):
        """call() should send system prompt and user message to host."""
        mock_result = MagicMock()
        mock_result.content = MagicMock()
        mock_result.content.text = sample_response_text
        mock_result.model = "claude-sonnet-4-5"
        mock_server.create_message = AsyncMock(return_value=mock_result)

        adapter = SamplingAdapter(
            server=mock_server, host_provider="claude", model_hint="claude-sonnet-4-5"
        )
        await adapter.call(
            system_prompt="You are a builder.",
            user_prompt="Design a cache",
            response_schema=SampleResponse,
        )

        mock_server.create_message.assert_called_once()
        call_kwargs = mock_server.create_message.call_args[1]
        assert "Design a cache" in str(call_kwargs.get("messages", []))

    async def test_call_returns_token_usage(self, mock_server, sample_response_text):
        """call() should return TokenUsage (zeroed when host doesn't report)."""
        mock_result = MagicMock()
        mock_result.content = MagicMock()
        mock_result.content.text = sample_response_text
        mock_result.model = "claude-sonnet-4-5"
        mock_server.create_message = AsyncMock(return_value=mock_result)

        adapter = SamplingAdapter(
            server=mock_server, host_provider="claude", model_hint="claude-sonnet-4-5"
        )
        _, usage = await adapter.call(
            system_prompt="Test",
            user_prompt="Test",
            response_schema=SampleResponse,
        )

        assert isinstance(usage, TokenUsage)

    async def test_call_raises_on_invalid_json(self, mock_server):
        """call() should raise ValueError on non-JSON response."""
        mock_result = MagicMock()
        mock_result.content = MagicMock()
        mock_result.content.text = "This is not JSON"
        mock_result.model = "claude-sonnet-4-5"
        mock_server.create_message = AsyncMock(return_value=mock_result)

        adapter = SamplingAdapter(
            server=mock_server, host_provider="claude", model_hint="claude-sonnet-4-5"
        )
        with pytest.raises((json.JSONDecodeError, ValueError)):
            await adapter.call(
                system_prompt="Test",
                user_prompt="Test",
                response_schema=SampleResponse,
            )
