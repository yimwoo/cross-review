"""OpenAI provider adapter. Ref: design doc \u00a719.2.

Named ``openai_adapter`` (not ``openai``) to avoid shadowing the
``openai`` package.
"""

from __future__ import annotations

import json
import os
from typing import Type

import openai
from pydantic import BaseModel

from cross_review.schemas import TokenUsage

_EXTRACT_JSON_INSTRUCTION = (
    "You MUST respond with valid JSON matching the required schema. "
    "Do NOT include any text outside the JSON object."
)


class OpenAIAdapter:
    """Adapter that calls the OpenAI Chat Completions API."""

    def __init__(self, model: str) -> None:
        """Initialize the OpenAI adapter.

        Args:
            model: The OpenAI model identifier (e.g. ``"gpt-4.1"``).
        """
        api_key = os.environ.get("OPENAI_API_KEY", "")
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    def name(self) -> str:
        """Return the provider name.

        Returns:
            The string ``"openai"``.
        """
        return "openai"

    async def call(  # pylint: disable=too-many-positional-arguments
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        max_tokens: int = 4096,
        timeout: float = 30.0,
    ) -> tuple[BaseModel, TokenUsage]:
        """Send a prompt to OpenAI and return a validated Pydantic model.

        Args:
            system_prompt: The system-level instruction.
            user_prompt: The user message content.
            response_schema: Pydantic model class to validate the response.
            max_tokens: Maximum tokens for the response.
            timeout: Request timeout in seconds.

        Returns:
            A tuple of (validated model instance, token usage).

        Raises:
            ValueError: If the response content or usage data is missing.
        """
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"{_EXTRACT_JSON_INSTRUCTION}\n\n"
            f"Required JSON schema:\n{schema_json}"
        )

        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_prompt},
            ],
            timeout=timeout,
        )

        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Empty response from OpenAI")
        parsed = json.loads(content)
        result = response_schema.model_validate(parsed)

        usage = response.usage
        if usage is None:
            raise ValueError("No usage data from OpenAI")
        token_usage = TokenUsage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )
        return result, token_usage
