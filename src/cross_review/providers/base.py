"""Provider adapter protocol and factory. Ref: design doc \u00a719.2."""

from __future__ import annotations

import os
from typing import Protocol, Type, runtime_checkable

from pydantic import BaseModel

from cross_review.schemas import TokenUsage

# Environment variable name for each provider's API key
_PROVIDER_KEY_ENVVAR: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def check_api_key(provider_name: str) -> None:
    """Raise a clear error if the API key for *provider_name* is not set.

    Args:
        provider_name: One of ``"claude"``, ``"openai"``, or ``"gemini"``.

    Raises:
        RuntimeError: If the required environment variable is missing or empty.
    """
    normalised = provider_name.lower().strip()
    env_var = _PROVIDER_KEY_ENVVAR.get(normalised)
    if env_var is None:
        return  # unknown provider; let create_provider handle it
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing API key for {provider_name}. "
            f"Set the {env_var} environment variable.\n"
            f"  export {env_var}=<your-key>"
        )


@runtime_checkable
class ProviderAdapter(Protocol):
    """Protocol that every LLM provider adapter must satisfy."""

    async def call(  # pylint: disable=too-many-positional-arguments
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        max_tokens: int = 4096,
        timeout: float = 30.0,
    ) -> tuple[BaseModel, TokenUsage]:
        """Send a prompt and return a validated response with token usage."""

    def name(self) -> str:
        """Return a human-readable provider name, e.g. 'claude'."""


def create_provider(provider_name: str, model: str) -> ProviderAdapter:
    """Instantiate a provider adapter by name.

    Args:
        provider_name: One of ``"claude"``, ``"openai"``, or ``"gemini"``.
        model: The model identifier to use (e.g. ``"claude-sonnet-4-5-20250514"``).

    Returns:
        A concrete ProviderAdapter instance.

    Raises:
        ValueError: If *provider_name* is not recognised.
    """
    normalised = provider_name.lower().strip()
    check_api_key(normalised)

    if normalised == "claude":
        # pylint: disable-next=import-outside-toplevel
        from cross_review.providers.claude import ClaudeAdapter

        return ClaudeAdapter(model=model)

    if normalised == "openai":
        # pylint: disable-next=import-outside-toplevel
        from cross_review.providers.openai_adapter import OpenAIAdapter

        return OpenAIAdapter(model=model)

    if normalised == "gemini":
        # pylint: disable-next=import-outside-toplevel
        from cross_review.providers.gemini import GeminiAdapter

        return GeminiAdapter(model=model)

    raise ValueError(f"Unknown provider {provider_name!r}. Supported: claude, openai, gemini.")
