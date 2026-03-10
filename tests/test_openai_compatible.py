"""Tests for the generic OpenAI-compatible adapter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from cross_review.schemas import TokenUsage


class SampleResponse(BaseModel):
    """Simple schema for adapter validation tests."""

    answer: str


class TestExtractJson:
    """Tests for JSON extraction helpers."""

    def test_extract_json_returns_object_text_directly(self):
        from cross_review.providers.openai_compatible import _extract_json

        assert _extract_json('{"answer":"ok"}') == '{"answer":"ok"}'

    def test_extract_json_unwraps_markdown_fence(self):
        from cross_review.providers.openai_compatible import _extract_json

        raw = '```json\n{"answer":"ok"}\n```'
        assert _extract_json(raw) == '{"answer":"ok"}'

    def test_extract_json_reassembles_sse_completion_chunks(self):
        from cross_review.providers.openai_compatible import _extract_json

        raw = """data: {"choices":[{"delta":{"content":"{\\"answer\\":\\""}}]}

data: {"choices":[{"delta":{"content":"ok"}}]}

data: {"choices":[{"delta":{"content":"\\"}"}}]}

data: [DONE]
"""

        assert _extract_json(raw) == '{"answer":"ok"}'


class TestOpenAICompatibleAdapter:
    """Tests for OpenAICompatibleAdapter."""

    def test_name_returns_provider_name(self):
        from cross_review.providers.openai_compatible import OpenAICompatibleAdapter

        adapter = OpenAICompatibleAdapter(
            base_url="http://localhost:11434/v1",
            api_key=None,
            model="llama3.2",
            provider_name="ollama",
        )
        assert adapter.name() == "ollama"

    def test_uses_supplied_api_key(self):
        from cross_review.providers.openai_compatible import OpenAICompatibleAdapter

        with patch("cross_review.providers.openai_compatible.openai.AsyncOpenAI") as mock_client:
            OpenAICompatibleAdapter(
                base_url="https://oca.example.com/v1",
                api_key="file-token",
                model="oca/gpt-5.4",
                provider_name="oca",
            )

        assert mock_client.call_args.kwargs["api_key"] == "file-token"

    @pytest.mark.asyncio
    async def test_call_validates_schema(self):
        from cross_review.providers.openai_compatible import OpenAICompatibleAdapter

        adapter = OpenAICompatibleAdapter(
            base_url="http://localhost:11434/v1",
            api_key=None,
            model="llama3.2",
            provider_name="ollama",
        )
        adapter._client = SimpleNamespace(  # pylint: disable=protected-access
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=self._create_async_result(
                        content='{"answer":"ok"}',
                        usage=SimpleNamespace(
                            prompt_tokens=10,
                            completion_tokens=5,
                            total_tokens=15,
                        ),
                    )
                )
            )
        )

        result, usage = await adapter.call(
            system_prompt="Return JSON",
            user_prompt="Say ok",
            response_schema=SampleResponse,
        )

        assert result.answer == "ok"
        assert usage == TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)

    @pytest.mark.asyncio
    async def test_call_extracts_json_from_markdown_fence(self):
        from cross_review.providers.openai_compatible import OpenAICompatibleAdapter

        adapter = OpenAICompatibleAdapter(
            base_url="http://localhost:11434/v1",
            api_key=None,
            model="llama3.2",
            provider_name="ollama",
        )
        adapter._client = SimpleNamespace(  # pylint: disable=protected-access
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=self._create_async_result(
                        content='```json\n{"answer":"ok"}\n```',
                        usage=None,
                    )
                )
            )
        )

        result, usage = await adapter.call(
            system_prompt="Return JSON",
            user_prompt="Say ok",
            response_schema=SampleResponse,
        )

        assert result.answer == "ok"
        assert usage == TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

    @pytest.mark.asyncio
    async def test_call_accepts_sse_string_responses(self):
        from cross_review.providers.openai_compatible import OpenAICompatibleAdapter

        adapter = OpenAICompatibleAdapter(
            base_url="https://oca.example.com/v1",
            api_key="token",
            model="oca/gpt-5.4",
            provider_name="oca",
        )
        adapter._client = SimpleNamespace(  # pylint: disable=protected-access
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=self._create_async_raw_result(
                        """data: {"choices":[{"delta":{"content":"{\\"answer\\":\\""}}]}

data: {"choices":[{"delta":{"content":"ok"}}]}

data: {"choices":[{"delta":{"content":"\\"}"}}]}

data: [DONE]
"""
                    )
                )
            )
        )

        result, usage = await adapter.call(
            system_prompt="Return JSON",
            user_prompt="Say ok",
            response_schema=SampleResponse,
        )

        assert result.answer == "ok"
        assert usage == TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

    @staticmethod
    def _create_async_result(content: str, usage: object):
        async def _create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=usage,
            )

        return _create

    @staticmethod
    def _create_async_raw_result(content: str):
        async def _create(**kwargs):
            return content

        return _create
