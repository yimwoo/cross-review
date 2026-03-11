"""OCA (Oracle Code Assist) auto-discovery for MCP server use.

Provides helpers to locate OCA tokens from Cline's local state and build
ephemeral AppConfig objects for the cross-review orchestrator.  All token
handling is in-memory only — nothing is persisted to disk.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

from cross_review.config import AppConfig, ProviderEntry, RoleConfig

logger = logging.getLogger(__name__)

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
# Token expiry check & refresh
# ---------------------------------------------------------------------------

# Refresh buffer — treat token as expired 60s before real expiry so we
# never send a request with a token that expires mid-flight.
_EXPIRY_BUFFER_SECONDS = 60


def _is_token_expired(token: str) -> bool:
    """Check if a JWT access token is expired (or nearly expired)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return False  # not a JWT, can't check
        # Pad base64
        payload_b64 = parts[1] + "=="
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if exp is None:
            return False
        import time  # noqa: PLC0415

        return time.time() > (exp - _EXPIRY_BUFFER_SECONDS)
    except Exception:  # noqa: BLE001
        return False  # if we can't parse, assume valid and let the API decide


def _extract_client_id(token: str) -> str | None:
    """Extract the client_id (or azp) from a JWT access token."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        return payload.get("client_id") or payload.get("azp")
    except Exception:  # noqa: BLE001
        return None


def _extract_idcs_domain(token: str) -> str | None:
    """Extract the IDCS domain URL from the JWT audience claim."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        aud = payload.get("aud")
        if isinstance(aud, str) and "identity.oraclecloud.com" in aud:
            return aud.rstrip("/")
        if isinstance(aud, list):
            for a in aud:
                if "identity.oraclecloud.com" in a:
                    return a.rstrip("/")
        return None
    except Exception:  # noqa: BLE001
        return None


def refresh_oca_token(
    current_token: str | None = None,
) -> str | None:
    """Attempt to refresh the OCA access token using the stored refresh token.

    Reads the refresh token from ``~/.cline/data/secrets.json``, exchanges it
    at the IDCS token endpoint, and updates ``secrets.json`` with the new
    access token.  Returns the new token or *None* on failure.

    The IDCS domain and client_id are extracted from the current (possibly
    expired) access token's JWT claims.
    """
    cline_secrets = Path.home() / ".cline" / "data" / "secrets.json"
    if not cline_secrets.is_file():
        return None

    try:
        data = json.loads(cline_secrets.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    refresh_token = data.get("ocaRefreshToken", "").strip()
    if not refresh_token:
        return None

    # We need an existing token (even expired) to extract IDCS domain + client_id
    old_token = current_token or data.get("ocaApiKey", "").strip()
    if not old_token:
        return None

    idcs_domain = _extract_idcs_domain(old_token)
    client_id = _extract_client_id(old_token)
    if not idcs_domain or not client_id:
        logger.debug("Cannot refresh: missing IDCS domain or client_id in token")
        return None

    token_url = f"{idcs_domain}/oauth2/v1/token"
    post_data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode()

    req = urllib.request.Request(
        token_url,
        data=post_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except Exception:  # noqa: BLE001
        logger.debug("OCA token refresh request failed", exc_info=True)
        return None

    new_token = result.get("access_token", "").strip()
    if not new_token:
        return None

    # Persist the refreshed token back to secrets.json so Cline stays in sync
    data["ocaApiKey"] = new_token
    new_refresh = result.get("refresh_token", "").strip()
    if new_refresh:
        data["ocaRefreshToken"] = new_refresh

    try:
        cline_secrets.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # token still usable even if we can't persist

    logger.info("OCA token refreshed successfully")
    return new_token


def find_oca_token_with_refresh() -> str | None:
    """Like :func:`find_oca_token` but auto-refreshes expired tokens.

    If the discovered token is an expired JWT and a refresh token is
    available, attempts a silent refresh before returning.
    """
    token = find_oca_token()
    if token is None:
        return None

    if _is_token_expired(token):
        logger.info("OCA token expired, attempting refresh")
        refreshed = refresh_oca_token(current_token=token)
        if refreshed:
            return refreshed
        logger.warning("OCA token refresh failed; returning expired token")

    return token


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
