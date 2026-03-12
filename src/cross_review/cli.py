"""Click-based CLI entry point for cross-review (design doc §4, §7)."""

import asyncio
import sys
from pathlib import Path

import click

from cross_review.config import load_config
from cross_review.orchestrator import Orchestrator
from cross_review.rendering import render
from cross_review.schemas import ContextPayload, FileContext, Mode, ReviewRequest


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Cross-review: multi-model structured technical review engine."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@click.argument("question")
@click.option(
    "--mode",
    type=click.Choice(["fast", "review", "arbitration", "auto"], case_sensitive=False),
    default=None,
    help="Execution mode. Default: review.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["markdown", "json", "summary"], case_sensitive=False),
    default="markdown",
    help="Output format. Default: markdown.",
)
@click.option(
    "--context-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to a file to include as context.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to config.toml.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show trace diagnostics.")
def run(
    question: str,
    mode: str | None,
    output_format: str,
    context_file: Path | None,
    config_path: Path | None,
    verbose: bool,
) -> None:
    """Run a cross-review on a technical question."""
    config = load_config(config_path=config_path)

    # Build context from --context-file
    context = None
    if context_file:
        content = context_file.read_text()
        context = ContextPayload(files=[FileContext(path=str(context_file), content=content)])

    # Build request (default mode is REVIEW, not AUTO)
    request = ReviewRequest(
        question=question,
        mode=Mode(mode) if mode else Mode.REVIEW,
        context=context,
    )

    def on_event(event: str) -> None:
        """Emit a progress event to stderr."""
        click.echo(f"[cross-review] {event}", err=True)

    orchestrator = Orchestrator(config, on_event=on_event)

    try:
        result = asyncio.run(orchestrator.run(request))
    except KeyboardInterrupt:
        click.echo("\nAborted.", err=True)
        sys.exit(1)
    except (ConnectionError, TimeoutError, ValueError, RuntimeError, TypeError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(render(result, output_format=output_format, verbose=verbose))


@main.command()
def mcp() -> None:
    """Start the MCP server for host integration."""
    from cross_review.mcp_server import run_server  # pylint: disable=import-outside-toplevel

    run_server()


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
