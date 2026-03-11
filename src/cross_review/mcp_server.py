"""MCP server exposing cross-review as a tool (design doc S7).

Starts an MCP server over stdio.  Claude Code configures it as::

    {"mcpServers": {"cross-review": {"command": "cross-review", "args": ["mcp"]}}}
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from cross_review.config import AppConfig, load_config
from cross_review.oca_discovery import (
    OCA_TOKEN_ENV,
    build_oca_config,
    can_resolve_credentials,
    find_oca_token_with_refresh,
)
from cross_review.orchestrator import Orchestrator
from cross_review.rendering import render
from cross_review.schemas import ContextPayload, Mode, ReviewRequest
from cross_review.sessions import RoundRecord, SessionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definition (MCP inputSchema)
# ---------------------------------------------------------------------------

TOOL_DEFINITION: dict[str, Any] = {
    "name": "cross_review",
    "description": (
        "Run structured multi-model technical review with Builder + Reviewer "
        "roles, local reconciliation, and decision-support output"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The technical question to review",
            },
            "mode": {
                "type": "string",
                "enum": ["fast", "review", "arbitration", "auto"],
                "default": "review",
                "description": "Execution mode",
            },
            "context": {
                "type": "string",
                "description": "Optional context (file contents, design doc, etc.)",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional constraints for the review",
            },
            "output_format": {
                "type": "string",
                "enum": ["markdown", "json", "summary"],
                "default": "markdown",
                "description": "Output format",
            },
            "session_id": {
                "type": "string",
                "description": "Explicit cross-review session id for continuity",
            },
            "new_session": {
                "type": "boolean",
                "default": False,
                "description": "Force creation of a new session",
            },
            "prior_context": {
                "type": "string",
                "description": (
                    "Summary of earlier host discussion, only needed on "
                    "the first cross-review call in an ongoing chat"
                ),
            },
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                "description": "File contents to include in the review",
            },
        },
        "required": ["question"],
    },
}


# ---------------------------------------------------------------------------
# OCA config helper
# ---------------------------------------------------------------------------


def _build_oca_config_from_env(token: str) -> AppConfig:
    """Build an OCA config, reading per-role model overrides from env vars."""
    models: dict[str, str] = {}
    env_map = {
        "builder": "OCA_MODEL_BUILDER",
        "skeptic_reviewer": "OCA_MODEL_SKEPTIC",
        "pragmatist_reviewer": "OCA_MODEL_PRAGMATIST",
    }
    for role, env_var in env_map.items():
        val = os.environ.get(env_var, "").strip()
        if val:
            models[role] = val
    # Global default override
    global_model = os.environ.get("OCA_MODEL", "").strip()
    if global_model:
        for role in env_map:
            models.setdefault(role, global_model)

    base_url = os.environ.get("OCA_BASE_URL", "").strip() or None
    return build_oca_config(token, base_url=base_url, models=models or None)


# ---------------------------------------------------------------------------
# Tool handler (called by MCP server or directly in tests)
# ---------------------------------------------------------------------------


async def handle_cross_review(  # pylint: disable=too-many-locals
    arguments: dict[str, Any],
    server: Any = None,
    session_store: SessionStore | None = None,
) -> dict[str, Any]:
    """Handle a cross_review tool call.

    Args:
        arguments: Tool arguments matching TOOL_DEFINITION inputSchema.
        server: Optional MCP Server instance for host-managed auth.
        session_store: Optional SessionStore override (for testing).

    Returns:
        Dict with ``text`` (rendered result) and session metadata.
    """
    question: str = arguments["question"]
    mode_str: str = arguments.get("mode", "review")
    context_str: str | None = arguments.get("context")
    constraints: list[str] = arguments.get("constraints", [])
    output_format: str = arguments.get("output_format", "markdown")
    session_id: str | None = arguments.get("session_id")
    new_session: bool = arguments.get("new_session", False)
    prior_context: str | None = arguments.get("prior_context")
    files: list[dict[str, str]] = arguments.get("files", [])

    store = session_store or SessionStore()

    # --- Session resolution ---
    session_status = "none"
    memory_used = False

    if new_session or session_id is None:
        meta = store.create(workspace=arguments.get("_workspace", ""))
        session_id = meta.session_id
        session_status = "created"
    else:
        try:
            meta, _ = store.load(session_id)
            session_status = "resumed"
        except FileNotFoundError:
            meta = store.create(workspace=arguments.get("_workspace", ""))
            session_id = meta.session_id
            session_status = "created"

    # --- Build context payload ---
    context_parts: list[str] = []

    # Inject session memory summary for resumed sessions
    if session_status == "resumed":
        summary = store.build_context_summary(session_id)
        if summary:
            context_parts.append(f"Session memory:\n{summary}")
            memory_used = True

    # Inject prior_context on first call
    if prior_context:
        context_parts.append(f"Prior discussion:\n{prior_context}")

    # Inject file contents
    for f in files:
        context_parts.append(f"File: {f['path']}\n```\n{f['content']}\n```")

    # Original context
    if context_str:
        context_parts.append(context_str)

    context = None
    if context_parts:
        context = ContextPayload(text="\n\n".join(context_parts))

    # Build request
    request = ReviewRequest(
        question=question,
        mode=Mode(mode_str),
        context=context,
        constraints=constraints,
    )

    # --- Resolve config: explicit config → OCA auto-discovery → error ---
    config = load_config()
    oca_token: str | None = None

    if can_resolve_credentials(config, mode_str):
        # Explicit config has working credentials — use it as-is.
        pass
    else:
        # Fall back to OCA auto-discovery.
        oca_token = find_oca_token_with_refresh()
        if oca_token is not None:
            config = _build_oca_config_from_env(oca_token)
        else:
            return {
                "text": (
                    "Error: No provider credentials found.\n\n"
                    "Either:\n"
                    "- Set API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)\n"
                    "- Log into OCA via Cline\n"
                    "- Set OCA_TOKEN environment variable\n"
                    "- Write a token to ~/.oca/token"
                ),
                "session_id": session_id,
                "session_status": session_status,
                "memory_used": memory_used,
            }

    api_key_vars = tuple(
        entry.api_key_env for entry in config.providers.values()
        if entry.api_key_env is not None
    )

    # Resolve auth mode
    provider_factory = None
    host_warning: str | None = None

    if server is not None:
        # pylint: disable=import-outside-toplevel
        from cross_review.auth import host_managed_warning, resolve_auth_mode

        # Locate create_message: directly on server (legacy mcp) or on the
        # request-scoped session (mcp >=1.26 where Server lost create_message).
        sampling_target = None
        if "create_message" in dir(type(server)) or "create_message" in getattr(
            server, "__dict__", {}
        ):
            sampling_target = server
        else:
            try:
                session = server.request_context.session
                if hasattr(session, "create_message"):
                    sampling_target = session
            except (LookupError, AttributeError):
                pass
        has_sampling = sampling_target is not None
        try:
            auth_mode = resolve_auth_mode(
                auth_mode=request.host.auth_mode,  # pylint: disable=no-member
                has_sampling=has_sampling,
                api_key_vars=api_key_vars,
            )
        except RuntimeError as exc:
            return {"text": f"Error: {exc}", "session_id": session_id,
                    "session_status": session_status, "memory_used": memory_used}

        if auth_mode == "host_managed":
            from cross_review.providers.sampling import (
                SamplingAdapter,
            )

            host_provider = "claude"
            model_hint = "claude-sonnet-4-20250514"

            def sampling_factory(
                provider_name: str, model: str | None  # pylint: disable=unused-argument
            ) -> SamplingAdapter:
                return SamplingAdapter(
                    server=sampling_target,
                    host_provider=host_provider,
                    model_hint=model or model_hint,
                )

            provider_factory = sampling_factory
            host_warning = host_managed_warning(api_key_vars)

            # Cap reviewers to 1 in host-managed mode
            # pylint: disable=assigning-non-slot,no-member
            request.budget.max_reviewers = min(request.budget.max_reviewers, 1)

    # --- Inject OCA token into env for provider resolution ---
    if oca_token is not None:
        os.environ[OCA_TOKEN_ENV] = oca_token

    orchestrator = Orchestrator(config, provider_factory=provider_factory)

    try:
        result = await orchestrator.run(request)
    except (ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
        return {"text": f"Error running cross-review: {exc}", "session_id": session_id,
                "session_status": session_status, "memory_used": memory_used}
    finally:
        # Clear token from environment immediately after use
        os.environ.pop(OCA_TOKEN_ENV, None)

    # Inject host-managed warning
    if host_warning is not None:
        result.trace.warnings.append(host_warning)

    rendered = render(result, output_format=output_format)

    # --- Persist round and update memory ---
    round_num = store.next_round_number(session_id)
    store.append_round(
        session_id,
        RoundRecord(
            round_number=round_num,
            request_payload={"question": question, "mode": mode_str,
                             "files": [f["path"] for f in files]},
            result_payload={"rendered_length": len(rendered),
                            "confidence": result.confidence.value},
        ),
    )
    store.update_memory(session_id, result)

    return {
        "text": rendered,
        "session_id": session_id,
        "session_status": session_status,
        "memory_used": memory_used,
    }


# ---------------------------------------------------------------------------
# MCP server (requires `mcp` package)
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Start the MCP server over stdio.

    Requires the ``mcp`` package.
    """
    try:
        # pylint: disable=import-outside-toplevel
        from mcp.server import Server  # type: ignore[import-untyped]
        from mcp.server.stdio import stdio_server  # type: ignore[import-untyped]
        from mcp.types import TextContent, Tool  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SystemExit(
            "MCP server requires the 'mcp' package. "
            + 'Install with: pip install "cross-review[mcp] @ '
            + 'git+https://github.com/yimwoo/cross-review.git"'
        ) from exc

    server = Server("cross-review")

    @server.list_tools()  # type: ignore[misc]
    async def list_tools() -> list[Tool]:
        """Return the list of tools exposed by this server."""
        return [
            Tool(
                name=TOOL_DEFINITION["name"],
                description=TOOL_DEFINITION["description"],
                inputSchema=TOOL_DEFINITION["inputSchema"],
            )
        ]

    @server.call_tool()  # type: ignore[misc]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle a tool call."""
        if name != "cross_review":
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        result = await handle_cross_review(arguments, server=server)
        import json as _json  # pylint: disable=import-outside-toplevel

        session_meta = _json.dumps({
            "session_id": result["session_id"],
            "session_status": result["session_status"],
            "memory_used": result["memory_used"],
        })
        return [
            TextContent(type="text", text=result["text"]),
            TextContent(type="text", text=session_meta),
        ]

    init_options = server.create_initialization_options()

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)

    asyncio.run(_run())
