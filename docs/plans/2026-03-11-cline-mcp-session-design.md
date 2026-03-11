# Cline MCP Session Design for Oracle Plugin-First Use

> **Status:** Proposed
> **Audience:** cross-review maintainers, Oracle-internal users running Cline with OCA

## Goal

Allow users who are already signed into Oracle Code Assist through Cline to run `cross-review` directly from the Cline chat UI, without building a second VS Code extension or asking users to log in again.

The primary path is a Cline-facing MCP tool backed by persisted `cross-review` sessions. A simpler shell-wrapper fallback is included for environments where MCP wiring is unavailable or harder to debug.

## Non-Goals

- Building a new standalone VS Code extension for `cross-review`
- Implementing generic third-party OAuth inside `cross-review`
- Depending on provider-managed conversation state for review continuity
- Making this a public GitHub-facing auth workflow for all providers

## Recommendation

Use `cross-review` as a Cline tool, not as a prompt-only skill.

The recommended integration is:

1. Cline stays the user-facing chat UI
2. Cline exposes `cross-review` through MCP
3. `cross-review` persists its own review sessions on disk
4. OCA auth is reused from Cline's existing local login state

This keeps conversation UX in Cline while keeping review memory, orchestration, and determinism inside `cross-review`.

## Why Tool, Not Skill

A prompt-only skill would let Cline restate prior discussion in natural language, but it would not give `cross-review` deterministic control over:

- multi-turn session continuity
- file attachment structure
- explicit session branching
- replay/debugging of review rounds

An MCP tool is the right abstraction because the user can choose when to call it, while the tool receives structured inputs and can return a real `session_id` for follow-up rounds.

## User Experience

### Primary Flow: MCP Tool

Example user flow inside Cline:

1. User asks: "Review this design doc with cross-review"
2. Cline calls the `cross_review` MCP tool with the document contents and question
3. `cross-review` creates a session, runs Builder + Reviewer orchestration, and returns the result plus `session_id`
4. User asks: "What about rollback?"
5. Cline calls the same tool again with the follow-up question and the same `session_id`
6. `cross-review` reloads prior session memory and continues the review thread

### Mid-Conversation First Use

If the user only decides to use `cross-review` after several turns of normal Cline chat, `cross-review` does not automatically inherit that earlier chat.

The first tool call in that situation must include a compact `prior_context` summary produced by Cline. After that first call, `cross-review` owns session continuity.

### Fallback Flow: Shell Wrapper

If MCP integration is unavailable, Cline can still run a local wrapper command that:

1. reads the OCA token from Cline's local storage
2. generates a temporary `cross-review` config with `api_key_file`
3. invokes `cr run`
4. returns stdout back into the chat

This fallback is operationally useful, but it is a weaker long-term UX because session handling is less natural unless the wrapper also manages `session_id`.

## Session Model

### Recommendation

Persist sessions locally on disk outside the repo, with optional in-memory caching.

Recommended storage locations:

- macOS: `~/Library/Application Support/cross-review/sessions/`
- Linux: `~/.config/cross-review/sessions/`

Do not store sessions in the git workspace by default.

### Why Persisted Local Sessions

Persisted local storage is the best default because it:

- survives VS Code or Cline restarts
- supports resumable review threads
- avoids polluting project repos
- keeps the implementation local and Oracle-internal
- provides audit/debuggability for review rounds

### Session Scope

Default behavior should be:

- auto-reuse one `cross-review` session per `Cline conversation + workspace`
- allow explicit override with `session_id`
- allow reset with `new_session=true`

This gives a low-friction chat UX while still allowing deliberate branching and resumption.

### Session Contents

Each session should persist:

- `session.json`
  - session id
  - workspace identifier
  - created/updated timestamps
  - tool metadata
- `memory.json`
  - rolling structured session memory
- `rounds/<n>.json`
  - raw request and result payloads per round

The rolling memory should capture:

- accepted decisions
- rejected options
- active constraints
- open questions
- unresolved reviewer disagreements
- key attached files or referenced artifacts

Provider tokens must not be stored in session files.

## Context Rules

### Before First Tool Use

If the user has not called `cross-review` yet, there is no `cross-review` history.

Even if the user is in the same Cline conversation, `cross-review` only sees what the tool call passes in.

### First Tool Use in an Existing Chat

If earlier discussion matters, the first tool call should include:

- the current user question
- relevant files or context
- a short `prior_context` summary

The summary should cover only:

- decisions already made
- important constraints
- unresolved questions

It should not include the full chat transcript.

### Follow-Up Calls

After the first tool call creates a session, follow-ups should rely on:

- `session_id`
- session memory
- the previous reconciled result
- any newly attached files

The full raw history should not be reinjected every round.

## MCP Contract

Keep the existing single-tool shape in [mcp_server.py](/Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first/src/cross_review/mcp_server.py), but extend it with session-aware inputs.

### Tool Name

`cross_review`

### Input Schema

```json
{
  "type": "object",
  "properties": {
    "question": {
      "type": "string",
      "description": "The technical question to review"
    },
    "mode": {
      "type": "string",
      "enum": ["fast", "review", "arbitration", "auto"],
      "default": "review"
    },
    "context": {
      "type": "string",
      "description": "Optional plain-text context"
    },
    "constraints": {
      "type": "array",
      "items": { "type": "string" }
    },
    "output_format": {
      "type": "string",
      "enum": ["markdown", "json", "summary"],
      "default": "markdown"
    },
    "session_id": {
      "type": "string",
      "description": "Explicit cross-review session id"
    },
    "new_session": {
      "type": "boolean",
      "default": false,
      "description": "Force creation of a new session"
    },
    "prior_context": {
      "type": "string",
      "description": "Summary of earlier Cline discussion, only needed on the first cross-review call in an ongoing chat"
    },
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": { "type": "string" },
          "content": { "type": "string" }
        },
        "required": ["path", "content"]
      }
    }
  },
  "required": ["question"]
}
```

### Output Shape

The tool should return both rendered output and session metadata:

```json
{
  "session_id": "crs_123",
  "session_status": "created",
  "memory_used": true,
  "result_markdown": "# Cross-Review Result ..."
}
```

For compatibility with current MCP hosts, the initial implementation may still return a rendered text payload, but the internal handler should move toward a structured result object.

## Cline Tool Instructions

Cline should follow these rules when calling `cross_review`:

1. If this is the first `cross_review` call in the current chat and earlier discussion matters, include `prior_context`
2. If the user is continuing a previous `cross-review` result, reuse `session_id`
3. If the user asks to start over or compare a new direction, set `new_session=true`
4. Prefer passing real file contents via `files` when a design doc or plan exists
5. Do not pass the whole conversation transcript as `prior_context`

## Auth Model

### Primary Assumption

Oracle-internal users are already signed into OCA through Cline.

The integration should reuse that existing auth state rather than introducing a separate `cr auth login oca` flow.

### Recommended Token Flow

For the plugin-first Oracle path:

1. read the OCA access token from Cline's local secret storage
2. write the token into a temporary file
3. generate a temporary `cross-review` config using `api_key_file`
4. run `cross-review` against OCA through the configured OpenAI-compatible provider

This matches the work already implemented in:

- [config.py](/Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first/src/cross_review/config.py)
- [base.py](/Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first/src/cross_review/providers/base.py)
- [openai_compatible.py](/Users/yimwu/Documents/workspace/Apps/cross-review/.worktrees/codex/oca-plugin-first/src/cross_review/providers/openai_compatible.py)

### Caveat

This depends on Cline's current local token storage format, which is acceptable for Oracle-internal use but should not be treated as a stable public provider contract.

## Shell-Wrapper Fallback

### Purpose

Provide a no-MCP fallback that can still run from Cline chat using terminal execution.

### Behavior

The wrapper should:

1. locate the OCA token from Cline's local storage
2. write it to a temp token file
3. create a temp config for the OCA OpenAI-compatible provider
4. invoke `cr run`
5. print the result for Cline to read back into chat

### Limitations

Compared with the MCP path, the shell wrapper:

- has weaker structured input handling
- has weaker built-in session UX
- is more dependent on prompt discipline
- is better suited as a fallback or debugging path than as the primary interface

## Data Flow

### MCP Primary Path

```text
User in Cline
  -> Cline chooses to call cross_review tool
  -> Tool handler resolves or creates session
  -> Tool handler loads OCA token via Cline-owned login state
  -> cross-review orchestrator runs Builder + Reviewer flow
  -> result + session metadata returned to Cline
  -> later follow-up reuses session_id
```

### Shell Fallback Path

```text
User in Cline
  -> Cline runs local shell wrapper
  -> wrapper creates temp token file + temp config
  -> wrapper invokes cr run
  -> stdout is returned into Cline chat
```

## Error Handling

The implementation should handle these cases explicitly:

- no OCA token found in Cline storage
- expired token or unauthorized OCA response
- missing `session_id`
- corrupted session files
- first tool use without needed prior context
- oversized file/context payloads

Desired behavior:

- fail with actionable user-facing errors
- never write secrets into logs or session files
- allow session recovery when memory files are missing but raw rounds remain

## Testing Strategy

### Unit Tests

- session creation and lookup
- auto-reuse vs explicit `session_id` vs `new_session`
- `prior_context` seeding on first call
- session memory update logic
- shell-wrapper config generation
- MCP handler argument parsing

### Integration Tests

- first call creates a session
- second call with the same session follows prior context
- `new_session=true` branches correctly
- file attachments are preserved across rounds where needed
- MCP handler still works in host-managed mode

### Live Smoke Tests

For Oracle-internal validation:

- Cline logged into OCA
- `cross-review` called through MCP or wrapper
- builder and reviewer both complete against OCA-backed models

## Rollout Plan

### Phase 1

- add session persistence primitives to `cross-review`
- extend the MCP tool contract with session inputs
- keep current text rendering output

### Phase 2

- add Cline-specific tool instructions
- add the shell-wrapper fallback
- validate against real OCA login state

### Phase 3

- refine structured MCP results
- add smarter session memory compaction
- consider explicit CLI `--session` support for non-Cline use

## Open Questions

- How stable is the Cline conversation identifier exposed to MCP integrations, if any?
- Should the first implementation store session files in a single global directory or also namespace by workspace hash?
- Should the shell wrapper support the same `session_id` behavior from day one, or remain single-shot at first?
- When files are attached on the first call, should they be persisted by value or by path plus snapshot metadata?

## Decision Summary

- Primary path: Cline MCP tool
- Fallback path: shell wrapper
- Session storage: persisted locally on disk, outside the repo
- Default session behavior: auto-reuse with explicit override
- First use mid-conversation: require optional `prior_context`
- Auth approach: reuse existing Cline OCA login, do not build a new login flow into `cross-review`
