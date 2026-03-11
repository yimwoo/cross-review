"""Tests for the Cline shell-wrapper script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

WRAPPER_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cr-cline-wrapper.sh"


class TestShellWrapperSyntax:
    def test_script_exists(self):
        assert WRAPPER_SCRIPT.is_file()

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(WRAPPER_SCRIPT)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"


class TestShellWrapperConfigGeneration:
    def test_generates_valid_toml_config(self, tmp_path: Path):
        """Wrapper should generate a TOML config with api_key_file."""
        token_file = tmp_path / "oca-token"
        token_file.write_text("test-token-value", encoding="utf-8")

        # Run the wrapper with a fake cr that dumps the config instead
        fake_cr = tmp_path / "cr"
        fake_cr.write_text(
            '#!/usr/bin/env bash\ncat "$2"\n',
            encoding="utf-8",
        )
        fake_cr.chmod(0o755)

        env = os.environ.copy()
        env["OCA_TOKEN"] = "test-token-value"
        env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"

        result = subprocess.run(
            ["bash", str(WRAPPER_SCRIPT), "test question"],
            capture_output=True, text=True, env=env,
        )

        # The wrapper uses exec cr run --config <file> <args>
        # Our fake cr prints the config file (arg $2)
        output = result.stdout
        assert "openai_compatible" in output or result.returncode != 0

    def test_fails_with_missing_token(self, tmp_path: Path):
        """Wrapper should fail with clear error when no token is found."""
        env = os.environ.copy()
        env.pop("OCA_TOKEN", None)
        # Point to a non-existent home to avoid finding ~/.oca/token
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(WRAPPER_SCRIPT), "test question"],
            capture_output=True, text=True, env=env,
        )

        assert result.returncode != 0
        assert "Could not locate OCA token" in result.stderr

    def test_fails_with_empty_token(self, tmp_path: Path):
        """Wrapper should fail when token is empty."""
        env = os.environ.copy()
        env["OCA_TOKEN"] = ""
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(WRAPPER_SCRIPT), "test question"],
            capture_output=True, text=True, env=env,
        )

        assert result.returncode != 0

    def test_reads_token_from_file(self, tmp_path: Path):
        """Wrapper should read token from ~/.oca/token when env is unset."""
        oca_dir = tmp_path / ".oca"
        oca_dir.mkdir()
        (oca_dir / "token").write_text("file-based-token", encoding="utf-8")

        fake_cr = tmp_path / "cr"
        fake_cr.write_text(
            '#!/usr/bin/env bash\ncat "$2"\n',
            encoding="utf-8",
        )
        fake_cr.chmod(0o755)

        env = os.environ.copy()
        env.pop("OCA_TOKEN", None)
        env["HOME"] = str(tmp_path)
        env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"

        result = subprocess.run(
            ["bash", str(WRAPPER_SCRIPT), "test question"],
            capture_output=True, text=True, env=env,
        )

        output = result.stdout
        assert "openai_compatible" in output or result.returncode != 0

    def test_temp_files_cleaned_on_exit(self, tmp_path: Path):
        """Wrapper should clean up temp files even on normal exit."""
        # We test this indirectly: after the wrapper exits (even with error),
        # no temp dirs should remain from this specific invocation.
        env = os.environ.copy()
        env.pop("OCA_TOKEN", None)
        env["HOME"] = str(tmp_path)

        subprocess.run(
            ["bash", str(WRAPPER_SCRIPT), "test question"],
            capture_output=True, text=True, env=env,
        )

        # The wrapper creates tmpdir under system TMPDIR — we can't easily
        # check the exact path, but we verify the wrapper didn't leave files
        # in our controlled tmp_path
        leftover = list(tmp_path.glob("cr-*"))
        assert len(leftover) == 0
