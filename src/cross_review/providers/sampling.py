"""MCP sampling-based provider adapter (design doc: host-managed auth).

Delegates LLM calls to the MCP host via ``sampling/createMessage``.
The host makes the actual API call using its own credentials.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Type

from pydantic import BaseModel

from cross_review.schemas import TokenUsage

logger = logging.getLogger(__name__)

_EXTRACT_JSON_INSTRUCTION = (
    "You MUST respond with valid JSON matching the required schema. "
    "Do NOT include any text outside the JSON object."
)


def _build_messages(user_prompt: str) -> list[Any]:
    """Build the MCP sampling messages list for a user prompt.

    Uses ``mcp.types`` classes when available, falling back to plain dicts.

    Args:
        user_prompt: The user message content.

    Returns:
        A list containing a single user-role message.
    """
    try:
        from mcp.types import (  # pylint: disable=import-outside-toplevel
            SamplingMessage,
            TextContent,
        )
    except ImportError:
        return [{"role": "user", "content": {"type": "text", "text": user_prompt}}]

    return [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=user_prompt),
        )
    ]


def _build_model_preferences(model_hint: str) -> Any:
    """Build MCP model preferences, using typed classes when available.

    Falls back to a plain dict for older ``mcp`` versions.
    """
    try:
        from mcp.types import (  # pylint: disable=import-outside-toplevel
            ModelHint,
            ModelPreferences,
        )

        return ModelPreferences(hints=[ModelHint(name=model_hint)])
    except ImportError:
        return {"hints": [{"name": model_hint}]}


class SamplingAdapter:
    """Provider adapter that delegates LLM calls to the MCP host via sampling.

    Instead of calling a provider API directly, this adapter asks the MCP host
    (e.g. Claude Code, Codex) to make the LLM call using its own credentials.

    Args:
        server: The MCP ``Server`` instance (must support ``create_message``).
        host_provider: The provider name for the host (e.g. ``"claude"``).
        model_hint: Model identifier hint for the host (host may override).
    """

    def __init__(self, server: Any, host_provider: str, model_hint: str) -> None:
        self._server = server
        self._host_provider = host_provider
        self._model_hint = model_hint

    def name(self) -> str:
        """Return the provider name indicating host delegation.

        Returns:
            A string like ``"claude-via-host"``.
        """
        return f"{self._host_provider}-via-host"

    def model_id(self) -> str:
        """Return compound identifier."""
        return "host/sampling"

    async def call(  # pylint: disable=too-many-positional-arguments
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        max_tokens: int = 4096,
        timeout: float = 90.0,  # pylint: disable=unused-argument
    ) -> tuple[BaseModel, TokenUsage]:
        """Send a prompt via MCP sampling and return a validated response.

        Args:
            system_prompt: The system-level instruction.
            user_prompt: The user message content.
            response_schema: Pydantic model class to validate the response.
            max_tokens: Maximum tokens for the response.
            timeout: Request timeout in seconds (not used; host controls timeout).

        Returns:
            A tuple of (validated model instance, token usage).

        Raises:
            json.JSONDecodeError: If the response is not valid JSON.
            ValueError: If the response doesn't match the schema.
        """
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"{_EXTRACT_JSON_INSTRUCTION}\n\n"
            f"Required JSON schema:\n{schema_json}"
        )

        model_prefs = _build_model_preferences(self._model_hint)
        result = await self._server.create_message(
            messages=_build_messages(user_prompt),
            system_prompt=full_system,
            max_tokens=max_tokens,
            model_preferences=model_prefs,
        )

        raw_text = result.content.text
        validated = response_schema.model_validate(json.loads(raw_text))
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        return validated, usage
