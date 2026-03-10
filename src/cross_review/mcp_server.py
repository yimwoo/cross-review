"""MCP server exposing cross-review as a tool (design doc S7).

Starts an MCP server over stdio.  Claude Code configures it as::

    {"mcpServers": {"cross-review": {"command": "cross-review", "args": ["mcp"]}}}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cross_review.config import load_config
from cross_review.orchestrator import Orchestrator
from cross_review.rendering import render
from cross_review.schemas import ContextPayload, Mode, ReviewRequest

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
        },
        "required": ["question"],
    },
}


# ---------------------------------------------------------------------------
# Tool handler (called by MCP server or directly in tests)
# ---------------------------------------------------------------------------


async def handle_cross_review(arguments: dict[str, Any]) -> str:
    """Handle a cross_review tool call.

    Args:
        arguments: Tool arguments matching TOOL_DEFINITION inputSchema.

    Returns:
        Rendered review result as a string.
    """
    question: str = arguments["question"]
    mode_str: str = arguments.get("mode", "review")
    context_str: str | None = arguments.get("context")
    constraints: list[str] = arguments.get("constraints", [])
    output_format: str = arguments.get("output_format", "markdown")

    # Build context payload
    context = None
    if context_str:
        context = ContextPayload(text=context_str)

    # Build request
    request = ReviewRequest(
        question=question,
        mode=Mode(mode_str),
        context=context,
        constraints=constraints,
    )

    config = load_config()
    orchestrator = Orchestrator(config)

    try:
        result = await orchestrator.run(request)
    except (ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
        return f"Error running cross-review: {exc}"

    return render(result, output_format=output_format)


# ---------------------------------------------------------------------------
# MCP server (requires `mcp` package)
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Start the MCP server over stdio.

    Requires the ``mcp`` package (``pip install cross-review[mcp]``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from mcp.server import Server  # type: ignore[import-untyped]
        from mcp.server.stdio import stdio_server  # type: ignore[import-untyped]
        from mcp.types import TextContent, Tool  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SystemExit(
            "MCP server requires the 'mcp' package. "
            + "Install with: pip install cross-review[mcp]"
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

        result_text = await handle_cross_review(arguments)
        return [TextContent(type="text", text=result_text)]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream)

    asyncio.run(_run())
