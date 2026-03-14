---
intent: Add hybrid file locking to SessionStore so multiple MCP server processes can safely read/write session state concurrently
success_criteria:
  - All SessionStore write methods are protected by fcntl-based file locks
  - Global store lock guards create, delete, list_sessions, find_by_workspace
  - Per-session lock guards save, load, append_round, load_rounds, next_round_number, update_memory, build_context_summary
  - append_round atomically allocates round numbers under lock
  - No nested public lock acquisition (unlocked internal helpers used throughout)
  - delete() acquires global lock then session lock (fixed ordering)
  - Multiprocessing concurrency tests verify no data corruption
  - README clarifies POSIX-only locking, no native Windows Python MCP support
  - All existing tests pass unchanged
risk_level: medium
auto_approve: false
---

## Steps

- [ ] **Step 1: Add _flock context manager and lock helpers to SessionStore**
  action: |
    Add `import fcntl` and a `@contextmanager _flock(lock_path, shared=False)` helper.
    Add `_store_lock(shared)` returning `_flock(self._base_dir / ".store.lock", shared=shared)`.
    Add `_session_lock(session_id, shared)` returning `_flock(self._session_dir(session_id) / ".lock", shared=shared)`.
  loop: false
  verify: python -c "from cross_review.sessions import SessionStore; print('import ok')"

- [ ] **Step 2: Extract unlocked internal helpers**
  action: |
    Rename current method bodies into private unlocked versions:
    - `_load_unlocked(session_id)` — current `load()` body
    - `_save_unlocked(meta, memory)` — current `save()` body
    - `_load_rounds_unlocked(session_id)` — current `load_rounds()` body
    - `_next_round_number_unlocked(session_id)` — current `next_round_number()` body
    Public methods become thin wrappers that acquire the appropriate lock and delegate.
    No behavior change at this step — all existing tests must pass.
  loop: until tests pass
  max_iterations: 3
  verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review && python -m pytest tests/test_sessions.py tests/test_session_integration.py -x -q

- [ ] **Step 3: Wire locks into public methods**
  action: |
    Update public methods to acquire locks before calling unlocked helpers:
    - `load()` → `_session_lock(shared=True)` → `_load_unlocked()`
    - `save()` → `_session_lock(shared=False)` → `_save_unlocked()`
    - `load_rounds()` → `_session_lock(shared=True)` → `_load_rounds_unlocked()`
    - `next_round_number()` → `_session_lock(shared=True)` → `_next_round_number_unlocked()`
    - `build_context_summary()` → `_session_lock(shared=True)` → delegates to `_load_unlocked()`
    - `update_memory()` → `_session_lock(shared=False)` → uses `_load_unlocked()`, `_save_unlocked()`
    - `append_round()` → `_session_lock(shared=False)` → uses `_next_round_number_unlocked()` to atomically allocate round number, then writes
    - `create()` → `_store_lock(shared=False)` → current body
    - `list_sessions()` → `_store_lock(shared=True)` → current body
    - `find_by_workspace()` → `_store_lock(shared=True)` → current body (calls `list_sessions` unlocked inline or via unlocked helper)
    - `delete()` → `_store_lock(shared=False)` then `_session_lock(shared=False)` → current body
    All existing tests must still pass.
  loop: until tests pass
  max_iterations: 3
  verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review && python -m pytest tests/test_sessions.py tests/test_session_integration.py -x -q

- [ ] **Step 4: Update MCP handler to use atomic round allocation**
  action: |
    In `mcp_server.py` lines 379-390, remove the separate `next_round_number()` call.
    Pass `round_number=0` (or any placeholder) to `RoundRecord` — `append_round()` now
    overwrites it with the atomically allocated number.
    Update any code that reads `round_num` after the call if needed.
  loop: until tests pass
  max_iterations: 3
  verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review && python -m pytest tests/test_sessions.py tests/test_session_integration.py -x -q

- [ ] **Step 5: Write multiprocessing concurrency tests**
  action: |
    Add `tests/test_session_locking.py` with tests using `multiprocessing` (not threading):
    1. **test_concurrent_append_round_no_duplicates**: N processes each append a round to the same session concurrently. Assert all round numbers are unique and sequential.
    2. **test_concurrent_update_memory_no_lost_writes**: N processes each merge different decisions into the same session. Assert all decisions are present in final memory.
    3. **test_concurrent_create_no_collisions**: N processes each create a session. Assert all sessions exist with unique IDs.
    4. **test_delete_during_concurrent_writes**: One process deletes while another writes. Assert no crash, no partial state.
    Use `multiprocessing.Pool` or `Process` with a shared `tmp_path` passed as string arg.
  loop: until tests pass
  max_iterations: 3
  verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review && python -m pytest tests/test_session_locking.py -x -q

- [ ] **Step 6: Update README platform claim**
  action: |
    In README.md line 127, change:
      "Works on macOS, Linux, and Windows (Git Bash/WSL)."
    to:
      "Works on macOS, Linux, and Windows under WSL or Git Bash (POSIX environments). Native Windows Python is not supported for the MCP server."
  loop: false
  verify: grep -q "POSIX" README.md

- [ ] **Step 7: Final verification**
  action: Run the full test suite to confirm nothing is broken.
  loop: false
  verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review && python -m pytest tests/ -x -q
  gate: human
