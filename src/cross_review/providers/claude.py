"""Claude (Anthropic) provider adapter. Ref: design doc \u00a719.2."""

from __future__ import annotations

import json
import os
from typing import Type

import anthropic
from anthropic.types import TextBlock
from pydantic import BaseModel

from cross_review.schemas import TokenUsage

_EXTRACT_JSON_INSTRUCTION = (
    "You MUST respond with valid JSON matching the required schema. "
    "Do NOT include any text outside the JSON object."
)


class ClaudeAdapter:
    """Adapter that calls the Anthropic Messages API."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        """Initialize the Claude adapter.

        Args:
            model: The Anthropic model identifier (e.g. ``"claude-sonnet-4-20250514"``).
        """
        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = anthropic.AsyncAnthropic(api_key=resolved_api_key)
        self._model = model

    def name(self) -> str:
        """Return the provider name.

        Returns:
            The string ``"claude"``.
        """
        return "claude"

    async def call(  # pylint: disable=too-many-positional-arguments
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        max_tokens: int = 4096,
        timeout: float = 30.0,
    ) -> tuple[BaseModel, TokenUsage]:
        """Send a prompt to Claude and return a validated Pydantic model.

        Args:
            system_prompt: The system-level instruction.
            user_prompt: The user message content.
            response_schema: Pydantic model class to validate the response.
            max_tokens: Maximum tokens for the response.
            timeout: Request timeout in seconds.

        Returns:
            A tuple of (validated model instance, token usage).

        Raises:
            ValueError: If the first content block is not a TextBlock.
        """
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"{_EXTRACT_JSON_INSTRUCTION}\n\n"
            f"Required JSON schema:\n{schema_json}"
        )

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=full_system,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=timeout,
        )

        content_block = response.content[0]
        if not isinstance(content_block, TextBlock):
            raise ValueError("Expected text response from Claude")
        raw_text = content_block.text
        parsed = json.loads(raw_text)
        result = response_schema.model_validate(parsed)

        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
        return result, usage
