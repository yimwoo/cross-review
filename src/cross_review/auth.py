"""Auth mode resolution for cross-review (design doc: host-managed auth).

Resolves which authentication mode to use:
- ``provider_managed``: direct API calls with user-provided keys
- ``host_managed``: MCP sampling delegation to the host
- ``auto``: detect based on available keys and host capabilities
"""

from __future__ import annotations

import os

_API_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")

HOST_MANAGED_WARNING = (
    "Single-provider review (host-managed). "
    "For cross-model diversity, set OPENAI_API_KEY and GEMINI_API_KEY."
)


def resolve_auth_mode(auth_mode: str, has_sampling: bool) -> str:
    """Resolve the effective auth mode.

    Args:
        auth_mode: The configured auth mode (``"auto"``, ``"provider_managed"``,
            or ``"host_managed"``).
        has_sampling: Whether the MCP host supports sampling.

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
    has_keys = any(os.environ.get(var, "").strip() for var in _API_KEY_VARS)
    if has_keys:
        return "provider_managed"

    if has_sampling:
        return "host_managed"

    raise RuntimeError(
        "No API keys set and MCP sampling not available.\n"
        "Either set API keys:\n"
        "  export ANTHROPIC_API_KEY=<your-key>\n"
        "  export OPENAI_API_KEY=<your-key>\n"
        "  export GEMINI_API_KEY=<your-key>\n"
        "Or run via an MCP host that supports sampling (e.g. Claude Code)."
    )
