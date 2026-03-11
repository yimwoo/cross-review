"""OCA (Oracle Code Assist) auto-discovery for MCP server use.

Provides helpers to locate OCA tokens from Cline's local state and build
ephemeral AppConfig objects for the cross-review orchestrator.  All token
handling is in-memory only — nothing is persisted to disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cross_review.config import AppConfig, ProviderEntry, RoleConfig

# ---------------------------------------------------------------------------
# Default OCA endpoint and per-role model defaults
# ---------------------------------------------------------------------------

OCA_DEFAULT_BASE_URL = (
    "https://code-internal.aiservice.us-chicago-1.oci.oraclecloud.com"
    "/20250206/app/litellm/v1"
)

OCA_DEFAULT_MODELS: dict[str, str] = {
    "builder": "oca/gpt-5.4",
    "skeptic_reviewer": "oca/gpt-5.2",
    "pragmatist_reviewer": "oca/gpt-5.2-codex",
}

# ---------------------------------------------------------------------------
# Token discovery
# ---------------------------------------------------------------------------


def find_oca_token() -> str | None:
    """Locate an OCA access token.  Returns *None* if unavailable.

    Precedence (highest → lowest):
        1. ``OCA_TOKEN`` environment variable
        2. ``~/.cline/data/secrets.json`` → ``ocaApiKey``
        3. ``~/.oca/token`` file
    """
    # 1. Environment variable
    env_val = os.environ.get("OCA_TOKEN", "").strip()
    if env_val:
        return env_val

    # 2. Cline's file-backed secrets
    cline_secrets = Path.home() / ".cline" / "data" / "secrets.json"
    if cline_secrets.is_file():
        try:
            data = json.loads(cline_secrets.read_text(encoding="utf-8"))
            token = data.get("ocaApiKey", "").strip()
            if token:
                return token
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Well-known token file
    token_file = Path.home() / ".oca" / "token"
    if token_file.is_file():
        try:
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                return token
        except OSError:
            pass

    return None


# ---------------------------------------------------------------------------
# Ephemeral config builder
# ---------------------------------------------------------------------------

# Env var used to pass the OCA token to the provider layer in-memory.
# The caller sets this before creating the Orchestrator and clears it after.
OCA_TOKEN_ENV = "_CR_OCA_TOKEN"


def build_oca_config(
    token: str,
    base_url: str | None = None,
    models: dict[str, str] | None = None,
) -> AppConfig:
    """Build an ephemeral :class:`AppConfig` backed by a single OCA provider.

    The token is plumbed through the env-var mechanism (``_CR_OCA_TOKEN``)
    so that the existing ``resolve_api_key`` path works without changes.
    The caller **must** set ``os.environ[OCA_TOKEN_ENV] = token`` before
    creating the Orchestrator and clear it afterwards.

    Args:
        token: OCA bearer token (used only to validate non-empty).
        base_url: Override the default OCA endpoint.
        models: Per-role model overrides, e.g.
            ``{"builder": "oca/gpt-5.4", "skeptic_reviewer": "oca/gpt-5.2"}``.
            Missing keys fall back to :data:`OCA_DEFAULT_MODELS`.

    Returns:
        A fully populated :class:`AppConfig`.
    """
    if not token:
        raise ValueError("OCA token must not be empty")

    resolved_models = dict(OCA_DEFAULT_MODELS)
    if models:
        resolved_models.update(models)

    url = base_url or OCA_DEFAULT_BASE_URL

    provider = ProviderEntry(
        type="openai_compatible",
        base_url=url,
        api_key_env=OCA_TOKEN_ENV,
    )

    roles = {
        name: RoleConfig(provider="oca", model=resolved_models.get(name))
        for name in ("builder", "skeptic_reviewer", "pragmatist_reviewer")
    }

    return AppConfig(
        providers={"oca": provider},
        roles=roles,
    )


# ---------------------------------------------------------------------------
# Credential preflight
# ---------------------------------------------------------------------------

# Roles required by each mode.
_MODE_ROLES: dict[str, tuple[str, ...]] = {
    "fast": ("builder",),
    "review": ("builder", "skeptic_reviewer", "pragmatist_reviewer"),
    "arbitration": ("builder", "skeptic_reviewer", "pragmatist_reviewer"),
    "auto": ("builder", "skeptic_reviewer", "pragmatist_reviewer"),
}


def can_resolve_credentials(config: AppConfig, mode: str) -> bool:
    """Check whether *config* has resolvable credentials for *mode*.

    Determines which roles the mode needs, then verifies that each role's
    provider can resolve an API key (via env var or token file) without
    raising.  Returns ``True`` only when **all** needed providers pass.
    """
    from cross_review.providers.base import resolve_api_key  # noqa: PLC0415

    roles_needed = _MODE_ROLES.get(mode, _MODE_ROLES["review"])

    for role_name in roles_needed:
        role = config.roles.get(role_name)
        if role is None:
            return False
        provider = config.providers.get(role.provider)
        if provider is None:
            return False
        try:
            key = resolve_api_key(role.provider, config.providers)
            if key is None and (provider.api_key_env or provider.api_key_file):
                return False
        except RuntimeError:
            return False

    return True
