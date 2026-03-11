"""In-flight request coalescing and short-TTL result cache.

Prevents duplicate orchestration runs when a host (e.g. Cline) fires the
same tool call twice in rapid succession.

Two-tier behavior:
  1. **In-flight coalescing** — if a request with the same fingerprint is
     already running, the second caller awaits the same future.
  2. **Completed-result cache** — finished results are cached for a short
     TTL (default 5 s).  Calls with ``new_session=true`` are never cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable


def fingerprint(
    arguments: dict[str, Any],
    workspace_root: Path | None = None,
) -> str:
    """Compute a deterministic cache key from normalised request inputs.

    For ``new_session=true`` a unique key is returned so it never collides.

    File identity:
      * content-provided → hash the content
      * path-only → ``(realpath, st_mtime_ns, st_size)``
      * unresolvable → ``("error", raw_path)``
    """
    if arguments.get("new_session", False):
        return uuid.uuid4().hex

    file_identities: list[str] = []
    for f in arguments.get("files", []):
        content = f.get("content", "")
        path_str = f.get("path", "")
        if content:
            file_identities.append(
                hashlib.sha256(content.encode()).hexdigest()
            )
        elif path_str and workspace_root is not None:
            try:
                p = Path(path_str)
                resolved = (
                    p.resolve()
                    if p.is_absolute()
                    else (workspace_root / p).resolve()
                )
                ws = workspace_root.resolve()
                if not str(resolved).startswith(str(ws) + os.sep) and resolved != ws:
                    file_identities.append(f"error:{path_str}")
                else:
                    st = resolved.stat()
                    file_identities.append(
                        f"{resolved}:{st.st_mtime_ns}:{st.st_size}"
                    )
            except (OSError, ValueError):
                file_identities.append(f"error:{path_str}")
        else:
            file_identities.append(f"raw:{path_str}")

    parts = (
        arguments.get("question", ""),
        arguments.get("mode", "review"),
        arguments.get("context", ""),
        json.dumps(sorted(arguments.get("constraints", [])), sort_keys=True),
        arguments.get("output_format", "markdown"),
        arguments.get("prior_context", ""),
        arguments.get("session_id", ""),
        str(arguments.get("_workspace", "")),
        json.dumps(file_identities, sort_keys=True),
    )
    raw = "\0".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


class _CacheEntry:
    """A single cache entry: either in-flight or completed."""

    __slots__ = ("future", "result", "completed_at")

    def __init__(self, future: asyncio.Future[Any]) -> None:
        self.future = future
        self.result: Any = None
        self.completed_at: float = 0.0


class RequestCache:
    """Two-tier in-flight coalescing + short-TTL result cache."""

    def __init__(self, ttl: float = 5.0) -> None:
        self._ttl = ttl
        self._entries: dict[str, _CacheEntry] = {}

    async def get_or_run(
        self,
        key: str,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Return a cached/in-flight result or run *coro_factory*.

        If another call with the same key is in-flight, awaits its result.
        If a completed result exists within TTL, returns it immediately.
        Otherwise runs *coro_factory*, caches the result, and returns it.
        """
        self._cleanup()

        entry = self._entries.get(key)

        # Completed result still within TTL
        if entry is not None and entry.completed_at > 0:
            if (time.monotonic() - entry.completed_at) < self._ttl:
                return entry.result

        # In-flight — await the same future
        if entry is not None and entry.completed_at == 0.0:
            return await entry.future

        # New request — create future and run
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        entry = _CacheEntry(future)
        self._entries[key] = entry

        try:
            result = await coro_factory()
        except BaseException:
            # Don't cache errors — remove entry and propagate
            self._entries.pop(key, None)
            # Cancel instead of set_exception to avoid
            # "Future exception was never retrieved" warnings
            if not future.done():
                future.cancel()
            raise

        entry.result = result
        entry.completed_at = time.monotonic()
        if not future.done():
            future.set_result(result)

        return result

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = time.monotonic()
        expired = [
            k
            for k, e in self._entries.items()
            if e.completed_at > 0 and (now - e.completed_at) >= self._ttl
        ]
        for k in expired:
            del self._entries[k]
