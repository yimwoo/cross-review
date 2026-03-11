"""Tests for smart file context resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from cross_review._file_context import (
    resolve_file,
    resolve_files,
    truncate_for_builder,
)


# ---------------------------------------------------------------------------
# truncate_for_builder
# ---------------------------------------------------------------------------


class TestTruncateForBuilder:
    def test_small_file_full_content(self):
        content = "x" * 100
        result, notice = truncate_for_builder(content, "small.py")
        assert result == content
        assert notice == ""

    def test_medium_file_truncated(self):
        # 100KB of ASCII
        content = "a" * (100 * 1024)
        result, notice = truncate_for_builder(content, "medium.py")
        assert notice == "truncated"
        assert "[Truncated:" in result
        assert "showing first 50KB" in result
        # Should be roughly 50KB of content + notice
        assert len(result) < 55 * 1024

    def test_large_file_metadata_only(self):
        # 300KB file with many lines
        lines = [f"line {i}: " + "x" * 50 for i in range(6000)]
        content = "\n".join(lines)
        result, notice = truncate_for_builder(content, "large.py")
        assert notice == "metadata_only"
        assert "[File: large.py" in result
        assert "First 100 lines:" in result
        # Should NOT contain all lines
        assert "line 5999" not in result
        assert "line 0" in result


# ---------------------------------------------------------------------------
# resolve_file
# ---------------------------------------------------------------------------


class TestResolveFile:
    def test_content_provided_used_as_is(self, tmp_path: Path):
        entry = {"path": "test.py", "content": "print('hello')"}
        rf = resolve_file(entry, tmp_path)
        assert rf.content == "print('hello')"
        assert rf.source == "provided"
        assert rf.notice == ""

    def test_relative_path_resolved(self, tmp_path: Path):
        f = tmp_path / "src" / "main.py"
        f.parent.mkdir(parents=True)
        f.write_text("def main(): pass")
        entry = {"path": "src/main.py"}
        rf = resolve_file(entry, tmp_path)
        assert rf.content == "def main(): pass"
        assert rf.source == "disk"
        assert rf.path == "src/main.py"

    def test_absolute_path_in_workspace(self, tmp_path: Path):
        f = tmp_path / "docs" / "design.md"
        f.parent.mkdir(parents=True)
        f.write_text("# Design")
        entry = {"path": str(f)}
        rf = resolve_file(entry, tmp_path)
        assert rf.content == "# Design"
        assert rf.source == "disk"

    def test_absolute_path_outside_workspace_rejected(self, tmp_path: Path):
        entry = {"path": "/etc/passwd"}
        with pytest.raises(ValueError, match="outside workspace"):
            resolve_file(entry, tmp_path)

    def test_symlink_escape_rejected(self, tmp_path: Path):
        """Symlink pointing outside workspace should be rejected."""
        target = Path("/tmp/external_file_test_xyzzy")
        try:
            target.write_text("secret")
            link = tmp_path / "escape.txt"
            link.symlink_to(target)
            entry = {"path": "escape.txt"}
            with pytest.raises(ValueError, match="outside workspace"):
                resolve_file(entry, tmp_path)
        finally:
            target.unlink(missing_ok=True)

    def test_missing_file_raises(self, tmp_path: Path):
        entry = {"path": "nonexistent.py"}
        with pytest.raises(FileNotFoundError, match="not found"):
            resolve_file(entry, tmp_path)

    def test_content_provided_truncated_if_large(self, tmp_path: Path):
        big_content = "x" * (100 * 1024)
        entry = {"path": "big.txt", "content": big_content}
        rf = resolve_file(entry, tmp_path)
        assert rf.notice == "truncated"
        assert rf.source == "provided"


# ---------------------------------------------------------------------------
# resolve_files
# ---------------------------------------------------------------------------


class TestResolveFiles:
    def test_mixed_valid_and_invalid(self, tmp_path: Path):
        good = tmp_path / "good.py"
        good.write_text("ok")
        entries = [
            {"path": "good.py"},
            {"path": "/etc/shadow"},
            {"path": "missing.py"},
        ]
        resolved, errors = resolve_files(entries, tmp_path)
        assert len(resolved) == 1
        assert resolved[0].path == "good.py"
        assert len(errors) == 2

    def test_empty_list(self, tmp_path: Path):
        resolved, errors = resolve_files([], tmp_path)
        assert resolved == []
        assert errors == []

    def test_all_content_provided(self, tmp_path: Path):
        entries = [
            {"path": "a.py", "content": "aaa"},
            {"path": "b.py", "content": "bbb"},
        ]
        resolved, errors = resolve_files(entries, tmp_path)
        assert len(resolved) == 2
        assert errors == []
        assert all(rf.source == "provided" for rf in resolved)
