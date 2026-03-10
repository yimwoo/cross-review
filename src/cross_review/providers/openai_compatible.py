"""Generic adapter for OpenAI-compatible chat completion APIs."""

from __future__ import annotations

import json
import re
from typing import Type

import openai
from pydantic import BaseModel

from cross_review.schemas import TokenUsage

_EXTRACT_JSON_INSTRUCTION = (
    "You MUST respond with valid JSON matching the required schema. "
    "Do NOT include any text outside the JSON object."
)


def _extract_json(raw_text: str) -> str:
    """Extract a JSON object from plain text or fenced Markdown."""
    stripped = raw_text.strip()
    if stripped.startswith("{"):
        return stripped

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if match:
        return match.group(1)

    return stripped


class OpenAICompatibleAdapter:
    """Adapter that targets OpenAI-compatible chat completion endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        provider_name: str,
    ) -> None:
        """Initialize an OpenAI-compatible adapter."""
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "no-key-required",
        )
        self._model = model
        self._provider_name = provider_name

    def name(self) -> str:
        """Return the configured provider name."""
        return self._provider_name

    @staticmethod
    def _build_token_usage(usage: object | None) -> TokenUsage:
        """Normalize provider usage metadata into TokenUsage."""
        if usage is None:
            return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or 0
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        return TokenUsage(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    async def call(  # pylint: disable=too-many-positional-arguments
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        max_tokens: int = 4096,
        timeout: float = 30.0,
    ) -> tuple[BaseModel, TokenUsage]:
        """Send a prompt and validate a JSON response."""
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"{_EXTRACT_JSON_INSTRUCTION}\n\n"
            f"Required JSON schema:\n{schema_json}"
        )

        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_prompt},
            ],
            timeout=timeout,
        )

        content = response.choices[0].message.content
        if content is None:
            raise ValueError(f"Empty response from {self._provider_name}")

        parsed = json.loads(_extract_json(content))
        result = response_schema.model_validate(parsed)
        return result, self._build_token_usage(response.usage)
