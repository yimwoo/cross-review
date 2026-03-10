"""OpenAI adapter compatibility wrapper over the generic OpenAI-compatible adapter."""

from __future__ import annotations

from cross_review.providers.openai_compatible import OpenAICompatibleAdapter


class OpenAIAdapter(OpenAICompatibleAdapter):
    """Built-in OpenAI adapter kept for backward compatibility."""

    def __init__(self, model: str) -> None:
        """Initialize the OpenAI wrapper with the official API defaults."""
        super().__init__(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            model=model,
            provider_name="openai",
        )
