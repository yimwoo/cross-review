"""Tests for session persistence primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from cross_review.sessions import (
    RoundRecord,
    SessionMemory,
    SessionStore,
)


class TestSessionCreate:
    def test_create_generates_id_and_writes_files(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create(workspace="/my/project")

        assert meta.session_id.startswith("crs_")
        session_dir = tmp_path / meta.session_id
        assert (session_dir / "session.json").is_file()
        assert (session_dir / "memory.json").is_file()
        assert (session_dir / "rounds").is_dir()

    def test_create_stores_workspace(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create(workspace="/my/project")
        assert meta.workspace == "/my/project"

    def test_create_stores_tool_metadata(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create(tool_metadata={"host": "cline"})
        assert meta.tool_metadata == {"host": "cline"}


class TestSessionLoad:
    def test_load_reads_back_created_session(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        created = store.create(workspace="/ws")

        meta, memory = store.load(created.session_id)
        assert meta.session_id == created.session_id
        assert meta.workspace == "/ws"
        assert memory.decisions == []

    def test_load_missing_session_raises(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="Session not found"):
            store.load("crs_nonexistent")

    def test_load_recovers_from_corrupted_memory(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        created = store.create()
        memory_file = tmp_path / created.session_id / "memory.json"
        memory_file.write_text("NOT JSON", encoding="utf-8")

        meta, memory = store.load(created.session_id)
        assert meta.session_id == created.session_id
        assert memory == SessionMemory()  # recovered with defaults


class TestSessionSave:
    def test_save_updates_timestamp(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        original_updated = meta.updated_at

        memory = SessionMemory(decisions=["use postgres"])
        store.save(meta, memory)

        reloaded_meta, reloaded_memory = store.load(meta.session_id)
        assert reloaded_meta.updated_at >= original_updated
        assert reloaded_memory.decisions == ["use postgres"]


class TestSessionDelete:
    def test_delete_removes_directory(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        assert (tmp_path / meta.session_id).is_dir()

        store.delete(meta.session_id)
        assert not (tmp_path / meta.session_id).exists()

    def test_delete_nonexistent_is_noop(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        store.delete("crs_doesnotexist")  # should not raise


class TestSessionList:
    def test_list_returns_all_sessions(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        store.create(workspace="/a")
        store.create(workspace="/b")

        sessions = store.list_sessions()
        assert len(sessions) == 2
        workspaces = {s.workspace for s in sessions}
        assert workspaces == {"/a", "/b"}

    def test_list_empty_dir(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        assert store.list_sessions() == []

    def test_list_skips_corrupted_entries(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        store.create()
        bad_dir = tmp_path / "crs_bad"
        bad_dir.mkdir()
        (bad_dir / "session.json").write_text("BROKEN", encoding="utf-8")

        sessions = store.list_sessions()
        assert len(sessions) == 1


class TestFindByWorkspace:
    def test_find_returns_most_recent(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        store.create(workspace="/ws")
        second = store.create(workspace="/ws")

        # Make second more recent
        meta2, mem2 = store.load(second.session_id)
        store.save(meta2, mem2)

        found = store.find_by_workspace("/ws")
        assert found is not None
        assert found.session_id == second.session_id

    def test_find_returns_none_for_unknown_workspace(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        store.create(workspace="/other")
        assert store.find_by_workspace("/unknown") is None


class TestRounds:
    def test_append_and_load_rounds(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()

        record1 = RoundRecord(round_number=1, request_payload={"q": "first"})
        record2 = RoundRecord(round_number=2, request_payload={"q": "second"})
        store.append_round(meta.session_id, record1)
        store.append_round(meta.session_id, record2)

        rounds = store.load_rounds(meta.session_id)
        assert len(rounds) == 2
        assert rounds[0].round_number == 1
        assert rounds[1].round_number == 2
        assert rounds[0].request_payload == {"q": "first"}

    def test_next_round_number(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()

        assert store.next_round_number(meta.session_id) == 1
        store.append_round(meta.session_id, RoundRecord(round_number=1))
        assert store.next_round_number(meta.session_id) == 2

    def test_load_rounds_empty(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        assert store.load_rounds(meta.session_id) == []

    def test_load_rounds_skips_corrupted(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        store.append_round(meta.session_id, RoundRecord(round_number=1))

        bad_file = tmp_path / meta.session_id / "rounds" / "2.json"
        bad_file.write_text("BROKEN", encoding="utf-8")

        rounds = store.load_rounds(meta.session_id)
        assert len(rounds) == 1


class TestUpdateMemory:
    def _make_result(self, **kwargs):
        """Create a minimal FinalResult-like object for testing."""
        from types import SimpleNamespace

        defaults = {
            "decision_points": [],
            "final_recommendation": "",
            "conflicting_findings": [],
            "consensus_findings": [],
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_merges_decision_points(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        result = self._make_result(decision_points=["Use PostgreSQL", "Deploy on K8s"])

        memory = store.update_memory(meta.session_id, result)
        assert "Use PostgreSQL" in memory.decisions
        assert "Deploy on K8s" in memory.decisions

    def test_merges_final_recommendation(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        result = self._make_result(final_recommendation="Go with option A")

        memory = store.update_memory(meta.session_id, result)
        assert "Go with option A" in memory.decisions

    def test_deduplicates_decisions(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        result = self._make_result(decision_points=["Use PostgreSQL"])

        store.update_memory(meta.session_id, result)
        memory = store.update_memory(meta.session_id, result)
        assert memory.decisions.count("Use PostgreSQL") == 1

    def test_extracts_conflicting_recommendations(self, tmp_path: Path):
        from types import SimpleNamespace

        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        cluster = SimpleNamespace(conflicting_recommendations=["Use Redis", "Use Memcached"])
        result = self._make_result(conflicting_findings=[cluster])

        memory = store.update_memory(meta.session_id, result)
        assert "Use Redis" in memory.disagreements
        assert "Use Memcached" in memory.disagreements

    def test_extracts_high_severity_constraints(self, tmp_path: Path):
        from types import SimpleNamespace

        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        cluster = SimpleNamespace(
            severity="HIGH",
            category="SECURITY",
            target="auth module",
            conflicting_recommendations=[],
        )
        result = self._make_result(consensus_findings=[cluster])

        memory = store.update_memory(meta.session_id, result)
        assert "[SECURITY] auth module" in memory.constraints

    def test_ignores_low_severity_for_constraints(self, tmp_path: Path):
        from types import SimpleNamespace

        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        cluster = SimpleNamespace(
            severity="LOW",
            category="COMPLEXITY",
            target="utils",
            conflicting_recommendations=[],
        )
        result = self._make_result(consensus_findings=[cluster])

        memory = store.update_memory(meta.session_id, result)
        assert memory.constraints == []


class TestBuildContextSummary:
    def test_empty_memory_returns_empty_string(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        assert store.build_context_summary(meta.session_id) == ""

    def test_missing_session_returns_empty_string(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        assert store.build_context_summary("crs_nonexistent") == ""

    def test_includes_decisions_and_constraints(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        _, memory = store.load(meta.session_id)
        memory.decisions = ["Use PostgreSQL"]
        memory.constraints = ["[SECURITY] auth module"]
        memory.open_questions = ["Which region?"]
        store.save(meta, memory)

        summary = store.build_context_summary(meta.session_id)
        assert "Prior decisions:" in summary
        assert "Use PostgreSQL" in summary
        assert "Active constraints:" in summary
        assert "Open questions:" in summary
        assert "Which region?" in summary

    def test_omits_empty_sections(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        meta = store.create()
        _, memory = store.load(meta.session_id)
        memory.decisions = ["Only this"]
        store.save(meta, memory)

        summary = store.build_context_summary(meta.session_id)
        assert "Prior decisions:" in summary
        assert "constraints" not in summary.lower()


class TestNewSessionBranching:
    def test_new_session_creates_separate_session(self, tmp_path: Path):
        store = SessionStore(base_dir=tmp_path)
        first = store.create(workspace="/ws")
        second = store.create(workspace="/ws")

        assert first.session_id != second.session_id
        assert len(store.list_sessions()) == 2
