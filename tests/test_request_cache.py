"""Tests for the in-flight request coalescing cache."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cross_review._request_cache import RequestCache, coalescing_key, fingerprint


# ---------------------------------------------------------------------------
# fingerprint tests
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_deterministic(self):
        args = {"question": "hello", "mode": "fast"}
        assert fingerprint(args) == fingerprint(args)

    def test_differs_on_mode_change(self):
        a = fingerprint({"question": "hello", "mode": "fast"})
        b = fingerprint({"question": "hello", "mode": "review"})
        assert a != b

    def test_new_session_always_unique(self):
        args = {"question": "hello", "new_session": True}
        keys = {fingerprint(args) for _ in range(20)}
        assert len(keys) == 20

    def test_file_content_hashed(self):
        a = fingerprint({"question": "q", "files": [{"path": "a.py", "content": "x"}]})
        b = fingerprint({"question": "q", "files": [{"path": "a.py", "content": "y"}]})
        assert a != b

    def test_file_path_uses_stat(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        args = {"question": "q", "files": [{"path": str(f)}]}
        k1 = fingerprint(args, workspace_root=tmp_path)
        # Modify file → different stat
        f.write_text("changed")
        k2 = fingerprint(args, workspace_root=tmp_path)
        assert k1 != k2

    def test_file_outside_workspace(self, tmp_path: Path):
        """Out-of-workspace path produces an error identity, not a crash."""
        args = {"question": "q", "files": [{"path": "/etc/hosts"}]}
        key = fingerprint(args, workspace_root=tmp_path)
        assert isinstance(key, str)
        assert len(key) == 64  # SHA256 hex

    def test_missing_file(self, tmp_path: Path):
        args = {"question": "q", "files": [{"path": "nonexistent.py"}]}
        key = fingerprint(args, workspace_root=tmp_path)
        assert isinstance(key, str)

    def test_differs_on_constraints(self):
        a = fingerprint({"question": "q", "constraints": ["a"]})
        b = fingerprint({"question": "q", "constraints": ["b"]})
        assert a != b


# ---------------------------------------------------------------------------
# coalescing_key tests
# ---------------------------------------------------------------------------


class TestCoalescingKey:
    def test_same_files_same_mode_same_key(self, tmp_path: Path):
        """Different questions but same file + mode → same coalescing key."""
        f = tmp_path / "design.md"
        f.write_text("content")
        a = coalescing_key(
            {"question": "Review this", "mode": "review",
             "files": [{"path": str(f)}]},
            workspace_root=tmp_path,
        )
        b = coalescing_key(
            {"question": "Thoroughly review this for risks", "mode": "review",
             "files": [{"path": str(f)}]},
            workspace_root=tmp_path,
        )
        assert a == b

    def test_different_mode_different_key(self, tmp_path: Path):
        f = tmp_path / "design.md"
        f.write_text("content")
        a = coalescing_key(
            {"question": "q", "mode": "fast",
             "files": [{"path": str(f)}]},
            workspace_root=tmp_path,
        )
        b = coalescing_key(
            {"question": "q", "mode": "deep",
             "files": [{"path": str(f)}]},
            workspace_root=tmp_path,
        )
        assert a != b

    def test_different_files_different_key(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        a = coalescing_key(
            {"question": "q", "files": [{"path": str(tmp_path / "a.md")}]},
            workspace_root=tmp_path,
        )
        b = coalescing_key(
            {"question": "q", "files": [{"path": str(tmp_path / "b.md")}]},
            workspace_root=tmp_path,
        )
        assert a != b

    def test_ignores_content_vs_path_only(self, tmp_path: Path):
        """Path-only and content-provided for same path → same coalescing key."""
        f = tmp_path / "design.md"
        f.write_text("content")
        a = coalescing_key(
            {"question": "q", "files": [{"path": str(f)}]},
            workspace_root=tmp_path,
        )
        b = coalescing_key(
            {"question": "q", "files": [{"path": str(f), "content": "content"}]},
            workspace_root=tmp_path,
        )
        assert a == b


    def test_no_files_returns_none(self):
        """No-files requests return None (no basis for coalescing)."""
        assert coalescing_key({"question": "Review this", "mode": "review"}) is None

    def test_new_session_returns_none(self):
        args = {"question": "q", "new_session": True, "files": [{"path": "a.md"}]}
        assert coalescing_key(args) is None


# ---------------------------------------------------------------------------
# RequestCache tests
# ---------------------------------------------------------------------------


class TestRequestCache:
    @pytest.fixture()
    def cache(self) -> RequestCache:
        return RequestCache(ttl=5.0)

    async def test_inflight_coalescing(self, cache: RequestCache):
        """Two concurrent calls with same key → one coro execution."""
        call_count = 0

        async def slow_coro():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return {"text": "result"}

        key = "same-key"
        r1, r2 = await asyncio.gather(
            cache.get_or_run(key, slow_coro),
            cache.get_or_run(key, slow_coro),
        )
        assert call_count == 1
        assert r1 is r2

    async def test_completed_cache_hit(self, cache: RequestCache):
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return {"text": "result"}

        key = "hit-key"
        r1 = await cache.get_or_run(key, coro)
        r2 = await cache.get_or_run(key, coro)
        assert call_count == 1
        assert r1 is r2

    async def test_completed_cache_expires(self):
        cache = RequestCache(ttl=0.1)
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return {"text": f"result-{call_count}"}

        key = "expire-key"
        await cache.get_or_run(key, coro)
        await asyncio.sleep(0.15)
        await cache.get_or_run(key, coro)
        assert call_count == 2

    async def test_coro_error_not_cached(self, cache: RequestCache):
        call_count = 0

        async def bad_coro():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return {"text": "ok"}

        key = "error-key"
        with pytest.raises(RuntimeError, match="boom"):
            await cache.get_or_run(key, bad_coro)

        # Second call should run fresh (error not cached)
        result = await cache.get_or_run(key, bad_coro)
        assert result == {"text": "ok"}
        assert call_count == 2

    async def test_different_keys_run_independently(self, cache: RequestCache):
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return {"text": f"r{call_count}"}

        r1 = await cache.get_or_run("key-a", coro)
        r2 = await cache.get_or_run("key-b", coro)
        assert call_count == 2
        assert r1 != r2

    async def test_coalescing_key_dedup(self, cache: RequestCache):
        """Different strict keys but same coalescing key → one coro run."""
        call_count = 0

        async def slow_coro():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return {"text": "result"}

        r1, r2 = await asyncio.gather(
            cache.get_or_run("strict-1", slow_coro, coalesce_key="same-coal"),
            cache.get_or_run("strict-2", slow_coro, coalesce_key="same-coal"),
        )
        assert call_count == 1
        assert r1 is r2

    async def test_coalescing_key_different_coal_keys(self, cache: RequestCache):
        """Different coalescing keys → independent runs."""
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return {"text": f"r{call_count}"}

        r1 = await cache.get_or_run("s1", coro, coalesce_key="coal-a")
        r2 = await cache.get_or_run("s2", coro, coalesce_key="coal-b")
        assert call_count == 2
