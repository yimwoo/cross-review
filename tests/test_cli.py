"""Tests for CLI group structure and subcommands."""

import click
from click.testing import CliRunner

from cross_review.cli import main


class TestCLIGroup:
    """Tests for CLI group structure."""

    def test_main_is_click_group(self):
        """Main CLI entry point should be a Click group."""
        assert isinstance(main, click.Group)

    def test_help_shows_run_and_mcp_commands(self):
        """CLI --help should list run and mcp subcommands."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "mcp" in result.output

    def test_run_subcommand_requires_question(self):
        """run subcommand without question should fail."""
        runner = CliRunner()
        result = runner.invoke(main, ["run"])
        assert result.exit_code != 0

    def test_run_help_shows_options(self):
        """run --help should show mode, output, context-file, config options."""
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--mode" in result.output
        assert "--output" in result.output
        assert "--context-file" in result.output
        assert "--config" in result.output
        assert "--verbose" in result.output

    def test_run_accepts_question_argument(self):
        """run subcommand should accept a positional question argument."""
        runner = CliRunner()
        result = runner.invoke(main, ["run", "test question"])
        # Will fail on provider connection, but should pass CLI parsing
        assert "Missing argument" not in (result.output or "")
        assert "Error: No such command" not in (result.output or "")

    def test_mcp_subcommand_calls_run_server(self, monkeypatch):
        """mcp subcommand should call run_server from mcp_server module."""
        called = []

        def mock_run_server():
            called.append(True)

        monkeypatch.setattr("cross_review.mcp_server.run_server", mock_run_server)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp"])
        assert result.exit_code == 0
        assert called
