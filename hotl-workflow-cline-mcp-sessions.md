---
intent: Enable cross-review MCP tool with persisted multi-turn sessions for Cline/OCA users
success_criteria:
  - MCP tool accepts session_id and returns structured results with session metadata
  - Sessions persist across Cline/VS Code restarts
  - Follow-up calls reuse session memory without re-injecting full history
  - Shell-wrapper fallback produces equivalent review output
  - Unit and integration tests pass for session lifecycle and MCP handler
risk_level: medium
auto_approve: false
worktree: false
---

## Steps

### Phase 1: Session Persistence Primitives

- [ ] **Step 1: Create session storage module with data models**
action: Create `src/cross_review/sessions.py` with Pydantic models for `SessionMeta` (id, workspace, created/updated timestamps, tool metadata), `SessionMemory` (decisions, rejected options, constraints, open questions, disagreements, referenced artifacts), and `RoundRecord` (request payload, result payload, round number). Add a `SessionStore` class that manages the XDG-compliant session directory (`~/Library/Application Support/cross-review/sessions/` on macOS, `~/.config/cross-review/sessions/` on Linux). Implement `create()`, `load()`, `save()`, `list()`, and `delete()` methods. Each session is a directory containing `session.json`, `memory.json`, and `rounds/<n>.json`.
loop: false
verify: python -c "from cross_review.sessions import SessionStore, SessionMeta, SessionMemory, RoundRecord; print('import ok')"

- [ ] **Step 2: Write unit tests for session CRUD**
action: Create `tests/test_sessions.py` with tests for session creation (generates ID, writes files), load (reads back), save (updates timestamps), auto-reuse by workspace, explicit session_id lookup, `new_session=true` branching, round append, memory update, delete, and corrupted-file recovery. Use `tmp_path` fixture for isolation.
loop: until tests pass
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_sessions.py -v

- [ ] **Step 3: Implement session memory update logic**
action: Add a `update_memory()` method to `SessionStore` that takes a `FinalResult` and merges decisions, constraints, and open questions into the rolling `SessionMemory`. Add a `build_context_summary()` method that produces a compact text summary of session memory for injection into the next orchestrator call. Write tests for both.
loop: until tests pass
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_sessions.py -v

### Phase 2: MCP Contract Extension

- [ ] **Step 4: Extend MCP tool input schema with session fields**
action: In `mcp_server.py`, add `session_id` (string, optional), `new_session` (boolean, default false), `prior_context` (string, optional), and `files` (array of {path, content}, optional) to the tool's `inputSchema`. Update `handle_cross_review()` to extract these from arguments.
loop: false
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_mcp_server.py -v

- [ ] **Step 5: Wire session lifecycle into MCP handler**
action: In `handle_cross_review()`, use `SessionStore` to resolve or create a session based on `session_id` / `new_session`. Before calling the orchestrator, inject `prior_context` or session memory summary into the review request context. After orchestration, save the round and update session memory from the result. Return `session_id`, `session_status` (created/resumed), and `memory_used` alongside the rendered result.
loop: until tests pass
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_mcp_server.py -v

- [ ] **Step 6: Update MCP tool output to include session metadata**
action: Modify the MCP handler return to include both the rendered text content and structured session metadata (`session_id`, `session_status`, `memory_used`) in the tool result. Maintain backward compatibility by keeping the text content as the primary `TextContent` block and adding metadata as a second content block (JSON).
loop: until tests pass
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_mcp_server.py -v

- [ ] **Step 7: Phase 1 review gate**
action: Review session persistence and MCP contract changes for correctness, secret leakage, and backward compatibility.
loop: false
gate: human

### Phase 3: Shell-Wrapper Fallback

- [ ] **Step 8: Create shell-wrapper script**
action: Create `scripts/cr-cline-wrapper.sh` that locates the OCA token from Cline's local VS Code storage, writes it to a temp file, generates a temp `cross-review` TOML config with `api_key_file` pointing to the temp token, invokes `cr run` with the generated config, and prints the result. Clean up temp files on exit (trap). The script should accept the same arguments as `cr run`.
loop: false
verify: bash -n /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first/scripts/cr-cline-wrapper.sh

- [ ] **Step 9: Write tests for shell-wrapper config generation**
action: Create `tests/test_shell_wrapper.py` with tests that verify the wrapper generates valid TOML config, handles missing token gracefully, and cleans up temp files. Use subprocess or shell mocking as appropriate.
loop: until tests pass
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_shell_wrapper.py -v

### Phase 4: Integration & Docs

- [ ] **Step 10: Write integration tests for session continuity**
action: Add tests to a new `tests/test_session_integration.py` that verify: (a) first MCP call creates a session and returns `session_id`, (b) second call with same `session_id` loads prior memory, (c) `new_session=true` creates a separate session, (d) file attachments are preserved in round records. Use mock providers.
loop: until tests pass
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/test_session_integration.py -v

- [ ] **Step 11: Add Cline tool instructions to README**
action: Add a section to `README.md` with Cline-specific tool instructions covering when to include `prior_context`, when to reuse `session_id`, when to set `new_session=true`, and file attachment best practices. Keep it concise — 5 rules max.
loop: false
verify: test -f /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first/README.md

- [ ] **Step 12: Run full test suite and lint**
action: Run the complete test suite and linter to ensure no regressions.
loop: until clean
max_iterations: 3
verify: cd /Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first && python -m pytest tests/ -v && python -m ruff check src/ tests/

- [ ] **Step 13: Final review gate**
action: Review all changes for completeness against the design doc's success criteria and governance contract. Verify no secrets in session files, no regressions, backward-compatible MCP contract.
loop: false
gate: human
