"""Auth mode resolution for cross-review (design doc: host-managed auth).

Resolves which authentication mode to use:
- ``provider_managed``: direct API calls with user-provided keys
- ``host_managed``: MCP sampling delegation to the host
- ``auto``: detect based on available keys and host capabilities
"""

from __future__ import annotations

import os

_DEFAULT_API_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")


def host_managed_warning(api_key_vars: tuple[str, ...]) -> str:
    """Return a warning about host-managed single-provider execution."""
    if api_key_vars:
        keys_hint = " and ".join(api_key_vars[:3])
        return (
            "Single-provider review (host-managed). "
            f"For cross-model diversity, set {keys_hint}."
        )
    return (
        "Single-provider review (host-managed). "
        "Set provider API keys to enable cross-model diversity."
    )


def resolve_auth_mode(
    auth_mode: str,
    has_sampling: bool,
    api_key_vars: tuple[str, ...] = _DEFAULT_API_KEY_VARS,
) -> str:
    """Resolve the effective auth mode.

    Args:
        auth_mode: The configured auth mode (``"auto"``, ``"provider_managed"``,
            or ``"host_managed"``).
        has_sampling: Whether the MCP host supports sampling.
        api_key_vars: Environment variable names considered valid provider keys.

    Returns:
        The resolved auth mode string.

    Raises:
        RuntimeError: If the requested mode is not available.
    """
    if auth_mode == "provider_managed":
        return "provider_managed"

    if auth_mode == "host_managed":
        if not has_sampling:
            raise RuntimeError(
                "host_managed auth requires MCP sampling support, "
                "but the current host does not support it."
            )
        return "host_managed"

    # auto: prefer API keys, fall back to sampling
    has_keys = any(os.environ.get(var, "").strip() for var in api_key_vars)
    if has_keys:
        return "provider_managed"

    if has_sampling:
        return "host_managed"

    raise RuntimeError(
        "No API keys set and MCP sampling not available.\n"
        + "Either set API keys:\n"
        + "".join(f"  export {var}=<your-key>\n" for var in api_key_vars)
        + "Or run via an MCP host that supports sampling (e.g. Claude Code)."
    )
