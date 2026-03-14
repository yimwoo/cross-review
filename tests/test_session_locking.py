"""Concurrency tests for SessionStore file locking.

Uses multiprocessing (not threading) to verify fcntl-based locks
prevent data corruption when multiple MCP server processes contend
on the same session files.
"""

from __future__ import annotations

import multiprocessing
import tempfile
from pathlib import Path
from types import SimpleNamespace

from cross_review.sessions import RoundRecord, SessionStore


# --- Worker functions (must be top-level for pickling) ---


def _worker_append_round(args: tuple[str, str]) -> int:
    """Append one round to the given session, return the allocated round number."""
    base_dir, session_id = args
    store = SessionStore(base_dir=Path(base_dir))
    record = store.append_round(
        session_id,
        RoundRecord(round_number=0, request_payload={"worker": "yes"}),
    )
    return record.round_number


def _worker_update_memory(args: tuple[str, str, str]) -> None:
    """Merge a unique decision into the session memory."""
    base_dir, session_id, decision = args
    store = SessionStore(base_dir=Path(base_dir))
    result = SimpleNamespace(
        decision_points=[decision],
        final_recommendation="",
        conflicting_findings=[],
        consensus_findings=[],
    )
    store.update_memory(session_id, result)


def _worker_create_session(base_dir: str) -> str:
    """Create a session and return its ID."""
    store = SessionStore(base_dir=Path(base_dir))
    meta = store.create(workspace="/test")
    return meta.session_id


def _worker_append_during_delete(args: tuple[str, str]) -> str:
    """Try to append a round; return 'ok' or 'deleted'."""
    base_dir, session_id = args
    store = SessionStore(base_dir=Path(base_dir))
    try:
        store.append_round(
            session_id,
            RoundRecord(round_number=0, request_payload={"late": "write"}),
        )
        return "ok"
    except (FileNotFoundError, OSError):
        return "deleted"


def _worker_delete_session(args: tuple[str, str]) -> str:
    """Delete a session, return 'deleted'."""
    base_dir, session_id = args
    store = SessionStore(base_dir=Path(base_dir))
    store.delete(session_id)
    return "deleted"


# --- Tests ---

N_WORKERS = 10


class TestConcurrentAppendRound:
    def test_no_duplicate_round_numbers(self):
        """N processes each append a round; all round numbers must be unique and sequential."""
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(base_dir=Path(tmp))
            meta = store.create()
            sid = meta.session_id

            args = [(tmp, sid)] * N_WORKERS
            with multiprocessing.Pool(N_WORKERS) as pool:
                round_numbers = pool.map(_worker_append_round, args)

            assert sorted(round_numbers) == list(range(1, N_WORKERS + 1))

            # Verify on disk
            rounds = store.load_rounds(sid)
            assert len(rounds) == N_WORKERS
            disk_numbers = sorted(r.round_number for r in rounds)
            assert disk_numbers == list(range(1, N_WORKERS + 1))


class TestConcurrentUpdateMemory:
    def test_no_lost_writes(self):
        """N processes each merge a unique decision; all must be present in final memory."""
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(base_dir=Path(tmp))
            meta = store.create()
            sid = meta.session_id

            decisions = [f"decision_{i}" for i in range(N_WORKERS)]
            args = [(tmp, sid, d) for d in decisions]
            with multiprocessing.Pool(N_WORKERS) as pool:
                pool.map(_worker_update_memory, args)

            _, memory = store.load(sid)
            for d in decisions:
                assert d in memory.decisions, f"Lost write: {d}"


class TestConcurrentCreate:
    def test_no_collisions(self):
        """N processes each create a session; all must exist with unique IDs."""
        with tempfile.TemporaryDirectory() as tmp:
            with multiprocessing.Pool(N_WORKERS) as pool:
                session_ids = pool.map(_worker_create_session, [tmp] * N_WORKERS)

            assert len(set(session_ids)) == N_WORKERS

            store = SessionStore(base_dir=Path(tmp))
            sessions = store.list_sessions()
            assert len(sessions) == N_WORKERS


class TestDeleteDuringConcurrentWrites:
    def test_no_crash_no_partial_state(self):
        """One process deletes while others write; no crash, no partial state."""
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(base_dir=Path(tmp))
            meta = store.create()
            sid = meta.session_id
            # Pre-populate a round so the session is non-trivial
            store.append_round(sid, RoundRecord(round_number=0))

            # Launch writers and one deleter concurrently
            write_args = [(tmp, sid)] * (N_WORKERS - 1)
            delete_args = [(tmp, sid)]

            with multiprocessing.Pool(N_WORKERS) as pool:
                write_results = pool.map_async(_worker_append_during_delete, write_args)
                delete_results = pool.map_async(_worker_delete_session, delete_args)

                writes = write_results.get(timeout=30)
                deletes = delete_results.get(timeout=30)

            # No crashes — all workers returned
            assert all(r in ("ok", "deleted") for r in writes)
            assert deletes == ["deleted"]

            # Final state: session is logically gone.
            # The directory may still exist if a concurrent writer's _flock
            # recreated it for the lock file, but session.json must be absent
            # (the session data was fully deleted).
            session_dir = Path(tmp) / sid
            meta_file = session_dir / "session.json"
            if session_dir.is_dir():
                remaining = {p.name for p in session_dir.iterdir()}
                # Only lock artifacts may remain — no session data
                assert not meta_file.is_file(), (
                    f"session.json should not survive delete; remaining: {remaining}"
                )
