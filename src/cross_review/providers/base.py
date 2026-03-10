"""Provider adapter protocol and factory. Ref: design doc §19.2."""

from __future__ import annotations

import os
from typing import Protocol, Type, runtime_checkable

from pydantic import BaseModel

from cross_review.config import ProviderEntry, _default_providers_factory
from cross_review.schemas import TokenUsage

_SUPPORTED_PROVIDER_TYPES = ("anthropic", "google", "openai_compatible")


def _normalise_registry(
    providers: dict[str, ProviderEntry] | None = None,
) -> dict[str, ProviderEntry]:
    """Return the provider registry with lowercased keys."""
    source = providers if providers is not None else _default_providers_factory()
    return {name.lower(): entry for name, entry in source.items()}


def check_api_key(
    provider_name: str,
    providers: dict[str, ProviderEntry] | None = None,
) -> None:
    """Raise a clear error if the API key for *provider_name* is not set."""
    normalised = provider_name.lower().strip()
    registry = _normalise_registry(providers)
    entry = registry.get(normalised)
    if entry is None or entry.api_key_env is None:
        return  # unknown provider; let create_provider handle it

    value = os.environ.get(entry.api_key_env, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing API key for {provider_name}. "
            f"Set the {entry.api_key_env} environment variable.\n"
            f"  export {entry.api_key_env}=<your-key>"
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


def create_provider(
    provider_name: str,
    model: str | None,
    providers: dict[str, ProviderEntry] | None = None,
) -> ProviderAdapter:
    """Instantiate a provider adapter from the merged registry."""
    normalised = provider_name.lower().strip()
    registry = _normalise_registry(providers)
    entry = registry.get(normalised)
    if entry is None:
        supported = ", ".join(sorted(registry))
        raise ValueError(f"Unknown provider {provider_name!r}. Supported: {supported}.")

    resolved_model = model or entry.default_model
    if resolved_model is None:
        raise RuntimeError(
            f"No model specified for provider '{provider_name}'. "
            "Pass a model explicitly or set default_model in the provider registry."
        )

    check_api_key(normalised, registry)

    if entry.type == "anthropic":
        # pylint: disable-next=import-outside-toplevel
        from cross_review.providers.claude import ClaudeAdapter

        return ClaudeAdapter(model=resolved_model)

    if entry.type == "google":
        # pylint: disable-next=import-outside-toplevel
        from cross_review.providers.gemini import GeminiAdapter

        return GeminiAdapter(model=resolved_model)

    if entry.type == "openai_compatible":
        if entry.base_url is None:
            raise RuntimeError(
                f"Provider '{provider_name}' is openai_compatible but has no base_url configured."
            )

        if normalised == "openai":
            # pylint: disable-next=import-outside-toplevel
            from cross_review.providers.openai_adapter import OpenAIAdapter

            return OpenAIAdapter(model=resolved_model)

        # pylint: disable-next=import-outside-toplevel
        from cross_review.providers.openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            model=resolved_model,
            provider_name=normalised,
        )

    supported_types = ", ".join(_SUPPORTED_PROVIDER_TYPES)
    raise ValueError(
        f"Unknown provider type {entry.type!r} for provider {provider_name!r}. "
        f"Supported types: {supported_types}."
    )
