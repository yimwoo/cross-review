"""Gemini (Google) provider adapter. Ref: design doc \u00a719.2."""

from __future__ import annotations

import json
import os
from typing import Optional, Type

from pydantic import BaseModel

from cross_review.schemas import TokenUsage

_EXTRACT_JSON_INSTRUCTION = (
    "You MUST respond with valid JSON matching the required schema. "
    "Do NOT include any text outside the JSON object."
)


class GeminiAdapter:
    """Adapter that calls the Google Generative AI (Gemini) API.

    The client is lazily initialised on the first ``call()`` so that
    importing this module does not require the API key to be set.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        """Initialize the Gemini adapter.

        Args:
            model: The Gemini model identifier (e.g. ``"gemini-2.5-pro"``).
        """
        self._model = model
        self._client: Optional[object] = None
        self._api_key = api_key

    def _ensure_client(self) -> object:
        """Lazily create the google.genai client on first use.

        Returns:
            The initialized google.genai Client object.
        """
        if self._client is None:
            # pylint: disable-next=import-outside-toplevel
            from google import genai  # type: ignore[attr-defined]

            api_key = self._api_key or os.environ.get("GEMINI_API_KEY", "")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def name(self) -> str:
        """Return the provider name.

        Returns:
            The string ``"gemini"``.
        """
        return "gemini"

    async def call(  # pylint: disable=too-many-positional-arguments,too-many-locals
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        max_tokens: int = 4096,
        timeout: float = 30.0,  # pylint: disable=unused-argument
    ) -> tuple[BaseModel, TokenUsage]:
        """Send a prompt to Gemini and return a validated Pydantic model.

        Args:
            system_prompt: The system-level instruction.
            user_prompt: The user message content.
            response_schema: Pydantic model class to validate the response.
            max_tokens: Maximum tokens for the response.
            timeout: Request timeout in seconds (reserved for future use).

        Returns:
            A tuple of (validated model instance, token usage).
        """
        # pylint: disable-next=import-outside-toplevel,import-error
        from google.genai import types  # type: ignore[attr-defined]

        client = self._ensure_client()

        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"{_EXTRACT_JSON_INSTRUCTION}\n\n"
            f"Required JSON schema:\n{schema_json}"
        )

        config = types.GenerateContentConfig(
            system_instruction=full_system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )

        response = await client.aio.models.generate_content(  # type: ignore[attr-defined]
            model=self._model,
            contents=user_prompt,
            config=config,
        )

        raw_text = response.text
        parsed = json.loads(raw_text)
        result = response_schema.model_validate(parsed)

        meta = response.usage_metadata
        usage = TokenUsage(
            input_tokens=meta.prompt_token_count,
            output_tokens=meta.candidates_token_count,
            total_tokens=meta.total_token_count,
        )
        return result, usage
