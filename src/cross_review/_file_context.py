"""Smart file context resolution for the MCP server.

Resolves file entries from tool arguments into content suitable for the
Builder prompt.  Supports:

  - Content-provided files (used as-is)
  - Path-only files (read from disk, sandboxed to workspace root)
  - Automatic truncation for large files

All paths are resolved relative to the workspace root.  Absolute paths must
fall under the workspace root — external paths are hard-rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Truncation thresholds (bytes)
_FULL_LIMIT = 50 * 1024  # 50 KB — full content to Builder
_TRUNCATE_LIMIT = 200 * 1024  # 200 KB — first 50 KB + notice
_METADATA_LINES = 100  # lines shown for metadata-only files


@dataclass
class ResolvedFile:
    """A file entry resolved to its content."""

    path: str
    content: str
    notice: str = ""
    source: str = ""  # "provided" | "disk"


def _is_within_workspace(resolved: Path, workspace: Path) -> bool:
    """Check that *resolved* is under *workspace* (both must be resolved)."""
    try:
        resolved.relative_to(workspace)
        return True
    except ValueError:
        return False


def resolve_file(
    file_entry: dict[str, str],
    workspace_root: Path,
) -> ResolvedFile:
    """Resolve a single file entry to its content.

    Args:
        file_entry: Dict with ``path`` and optionally ``content``.
        workspace_root: Resolved workspace root path.

    Returns:
        A :class:`ResolvedFile` with content and metadata.

    Raises:
        ValueError: If the path is outside the workspace.
        FileNotFoundError: If the path does not exist.
    """
    path_str = file_entry.get("path", "")
    content = file_entry.get("content", "")

    if content:
        truncated, notice = truncate_for_builder(content, path_str)
        return ResolvedFile(
            path=path_str,
            content=truncated,
            notice=notice,
            source="provided",
        )

    # Resolve path against workspace
    p = Path(path_str)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (workspace_root / p).resolve()

    ws = workspace_root.resolve()
    if not _is_within_workspace(resolved, ws):
        raise ValueError(
            f"Path '{path_str}' resolves outside workspace ({ws})"
        )

    if not resolved.is_file():
        raise FileNotFoundError(
            f"File not found: '{path_str}' (resolved to {resolved})"
        )

    raw = resolved.read_text(encoding="utf-8", errors="replace")
    truncated, notice = truncate_for_builder(raw, path_str)
    return ResolvedFile(
        path=path_str,
        content=truncated,
        notice=notice,
        source="disk",
    )


def truncate_for_builder(content: str, path: str) -> tuple[str, str]:
    """Truncate file content for the Builder prompt.

    Returns:
        ``(content, notice)`` where *notice* is empty, ``"truncated"``,
        or ``"metadata_only"``.
    """
    size = len(content.encode("utf-8", errors="replace"))

    if size <= _FULL_LIMIT:
        return content, ""

    if size <= _TRUNCATE_LIMIT:
        # Take first 50KB worth of characters (approximate)
        cut = content[:_FULL_LIMIT]
        size_kb = size // 1024
        return (
            cut + f"\n\n[Truncated: file is {size_kb}KB, showing first 50KB]",
            "truncated",
        )

    # Metadata only
    lines = content.splitlines()
    size_kb = size // 1024
    first_lines = "\n".join(lines[:_METADATA_LINES])
    return (
        f"[File: {path}, {size_kb}KB, {len(lines)} lines]\n"
        f"First {_METADATA_LINES} lines:\n{first_lines}",
        "metadata_only",
    )


def resolve_files(
    file_entries: list[dict[str, str]],
    workspace_root: Path,
) -> tuple[list[ResolvedFile], list[str]]:
    """Resolve a list of file entries, collecting errors instead of raising.

    Returns:
        ``(resolved_files, errors)`` where *errors* contains human-readable
        messages for files that could not be resolved.
    """
    resolved: list[ResolvedFile] = []
    errors: list[str] = []
    ws = workspace_root.resolve()

    for entry in file_entries:
        try:
            rf = resolve_file(entry, ws)
            resolved.append(rf)
        except ValueError as exc:
            errors.append(str(exc))
        except FileNotFoundError as exc:
            errors.append(str(exc))
        except OSError as exc:
            errors.append(f"Cannot read '{entry.get('path', '?')}': {exc}")

    return resolved, errors
