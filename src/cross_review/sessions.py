"""Persistent session storage for multi-turn cross-review interactions."""

from __future__ import annotations

import fcntl
import json
import platform
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field


def _default_sessions_dir() -> Path:
    """Return the platform-appropriate sessions directory."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "cross-review" / "sessions"
    return Path.home() / ".config" / "cross-review" / "sessions"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_session_id() -> str:
    return f"crs_{uuid.uuid4().hex[:12]}"


class SessionMeta(BaseModel):
    """Metadata for a single review session."""

    session_id: str
    workspace: str = ""
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    tool_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionMemory(BaseModel):
    """Rolling structured memory accumulated across rounds."""

    decisions: list[str] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    referenced_artifacts: list[str] = Field(default_factory=list)


class RoundRecord(BaseModel):
    """Record of a single review round."""

    round_number: int
    request_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow)


class SessionStore:
    """Manages session directories on disk.

    Each session is stored as a directory containing:
    - session.json  (SessionMeta)
    - memory.json   (SessionMemory)
    - rounds/N.json (RoundRecord per round)
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or _default_sessions_dir()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def _session_dir(self, session_id: str) -> Path:
        return self._base_dir / session_id

    @staticmethod
    @contextmanager
    def _flock(lock_path: Path, *, shared: bool = False) -> Iterator[None]:
        """Acquire a POSIX file lock (shared or exclusive)."""
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = lock_path.open("w")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    def _store_lock(self, *, shared: bool = False) -> contextmanager:
        """Lock for store-level operations (create, delete, list, find)."""
        return self._flock(self._base_dir / ".store.lock", shared=shared)

    def _session_lock(self, session_id: str, *, shared: bool = False) -> contextmanager:
        """Lock for per-session operations (save, load, rounds, memory)."""
        return self._flock(self._session_dir(session_id) / ".lock", shared=shared)

    # --- Unlocked internal helpers (no lock acquisition) ---

    def _create_unlocked(
        self, workspace: str = "", tool_metadata: dict[str, Any] | None = None,
    ) -> SessionMeta:
        session_id = _generate_session_id()
        meta = SessionMeta(
            session_id=session_id,
            workspace=workspace,
            tool_metadata=tool_metadata or {},
        )
        memory = SessionMemory()

        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "rounds").mkdir(exist_ok=True)

        self._write_json(session_dir / "session.json", meta.model_dump())
        self._write_json(session_dir / "memory.json", memory.model_dump())

        return meta

    def _load_unlocked(self, session_id: str) -> tuple[SessionMeta, SessionMemory]:
        session_dir = self._session_dir(session_id)
        meta_file = session_dir / "session.json"
        if not meta_file.is_file():
            raise FileNotFoundError(f"Session not found: {session_id}")

        meta = SessionMeta.model_validate(self._read_json(meta_file))
        try:
            memory = SessionMemory.model_validate(self._read_json(session_dir / "memory.json"))
        except (FileNotFoundError, json.JSONDecodeError):
            memory = SessionMemory()

        return meta, memory

    def _save_unlocked(self, meta: SessionMeta, memory: SessionMemory) -> None:
        meta.updated_at = _utcnow()
        session_dir = self._session_dir(meta.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "rounds").mkdir(exist_ok=True)

        self._write_json(session_dir / "session.json", meta.model_dump())
        self._write_json(session_dir / "memory.json", memory.model_dump())

    def _delete_unlocked(self, session_id: str) -> None:
        import shutil

        session_dir = self._session_dir(session_id)
        if session_dir.is_dir():
            # A concurrent process may recreate the lock file or rounds dir
            # between rmtree's walk and final rmdir.  Retry once on OSError.
            try:
                shutil.rmtree(session_dir)
            except OSError:
                shutil.rmtree(session_dir, ignore_errors=True)

    def _list_sessions_unlocked(self) -> list[SessionMeta]:
        if not self._base_dir.is_dir():
            return []
        sessions: list[SessionMeta] = []
        for child in sorted(self._base_dir.iterdir()):
            meta_file = child / "session.json"
            if child.is_dir() and meta_file.is_file():
                try:
                    meta = SessionMeta.model_validate(self._read_json(meta_file))
                    sessions.append(meta)
                except (json.JSONDecodeError, Exception):
                    continue
        return sessions

    def _load_rounds_unlocked(self, session_id: str) -> list[RoundRecord]:
        rounds_dir = self._session_dir(session_id) / "rounds"
        if not rounds_dir.is_dir():
            return []
        records: list[RoundRecord] = []
        for path in sorted(rounds_dir.iterdir()):
            if path.suffix == ".json":
                try:
                    records.append(RoundRecord.model_validate(self._read_json(path)))
                except (json.JSONDecodeError, Exception):
                    continue
        return records

    def _next_round_number_unlocked(self, session_id: str) -> int:
        rounds = self._load_rounds_unlocked(session_id)
        if not rounds:
            return 1
        return max(r.round_number for r in rounds) + 1

    # --- Public methods (acquire locks, delegate to unlocked helpers) ---

    def create(
        self, workspace: str = "", tool_metadata: dict[str, Any] | None = None,
    ) -> SessionMeta:
        """Create a new session and write initial files to disk."""
        with self._store_lock(shared=False):
            return self._create_unlocked(workspace=workspace, tool_metadata=tool_metadata)

    def load(self, session_id: str) -> tuple[SessionMeta, SessionMemory]:
        """Load session metadata and memory from disk.

        Raises FileNotFoundError if the session directory does not exist.
        """
        with self._session_lock(session_id, shared=True):
            return self._load_unlocked(session_id)

    def save(self, meta: SessionMeta, memory: SessionMemory) -> None:
        """Write session metadata and memory to disk, updating the timestamp."""
        with self._session_lock(meta.session_id, shared=False):
            self._save_unlocked(meta, memory)

    def delete(self, session_id: str) -> None:
        """Remove a session directory from disk.

        Acquires global store lock first, then per-session exclusive lock,
        to prevent races with concurrent session writes.
        """
        with self._store_lock(shared=False):
            with self._session_lock(session_id, shared=False):
                self._delete_unlocked(session_id)

    def list_sessions(self) -> list[SessionMeta]:
        """Return metadata for all sessions on disk."""
        with self._store_lock(shared=True):
            return self._list_sessions_unlocked()

    def find_by_workspace(self, workspace: str) -> SessionMeta | None:
        """Find the most recently updated session for a workspace."""
        with self._store_lock(shared=True):
            candidates = [s for s in self._list_sessions_unlocked() if s.workspace == workspace]
            if not candidates:
                return None
            return max(candidates, key=lambda s: s.updated_at)

    def append_round(self, session_id: str, record: RoundRecord) -> RoundRecord:
        """Append a round record to the session.

        Atomically allocates the next round number under an exclusive
        session lock, ignoring any value pre-set on the record.
        Returns the record with its final round_number assigned.
        """
        with self._session_lock(session_id, shared=False):
            record.round_number = self._next_round_number_unlocked(session_id)
            session_dir = self._session_dir(session_id)
            rounds_dir = session_dir / "rounds"
            rounds_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(rounds_dir / f"{record.round_number}.json", record.model_dump())
            return record

    def load_rounds(self, session_id: str) -> list[RoundRecord]:
        """Load all round records for a session, sorted by round number."""
        with self._session_lock(session_id, shared=True):
            return self._load_rounds_unlocked(session_id)

    def next_round_number(self, session_id: str) -> int:
        """Return the next round number for a session."""
        with self._session_lock(session_id, shared=True):
            return self._next_round_number_unlocked(session_id)

    def update_memory(self, session_id: str, result: Any) -> SessionMemory:
        """Merge a FinalResult into the session's rolling memory.

        Extracts decision_points, final_recommendation, conflicting findings,
        and consensus findings to update the structured memory fields.
        """
        with self._session_lock(session_id, shared=False):
            _, memory = self._load_unlocked(session_id)

            # decision_points → decisions
            for dp in getattr(result, "decision_points", []) or []:
                if dp and dp not in memory.decisions:
                    memory.decisions.append(dp)

            # final_recommendation → decisions (as a summary)
            rec = getattr(result, "final_recommendation", "")
            if rec and rec not in memory.decisions:
                memory.decisions.append(rec)

            # conflicting findings → disagreements
            for cluster in getattr(result, "conflicting_findings", []) or []:
                for rec_text in getattr(cluster, "conflicting_recommendations", []) or []:
                    if rec_text and rec_text not in memory.disagreements:
                        memory.disagreements.append(rec_text)

            # consensus findings → constraints (high-severity items)
            for cluster in getattr(result, "consensus_findings", []) or []:
                severity = getattr(cluster, "severity", None)
                if severity and str(severity).upper() in ("HIGH", "CRITICAL"):
                    summary = f"[{cluster.category}] {cluster.target}"
                    if summary not in memory.constraints:
                        memory.constraints.append(summary)

            meta, _ = self._load_unlocked(session_id)
            self._save_unlocked(meta, memory)
            return memory

    def build_context_summary(self, session_id: str) -> str:
        """Produce a compact text summary of session memory for LLM injection."""
        with self._session_lock(session_id, shared=True):
            try:
                _, memory = self._load_unlocked(session_id)
            except FileNotFoundError:
                return ""

            parts: list[str] = []

            if memory.decisions:
                parts.append("Prior decisions:\n" + "\n".join(f"- {d}" for d in memory.decisions))
            if memory.constraints:
                parts.append("Active constraints:\n" + "\n".join(f"- {c}" for c in memory.constraints))
            if memory.open_questions:
                items = "\n".join(f"- {q}" for q in memory.open_questions)
                parts.append(f"Open questions:\n{items}")
            if memory.disagreements:
                parts.append("Unresolved disagreements:\n" + "\n".join(f"- {d}" for d in memory.disagreements))

            return "\n\n".join(parts)

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
