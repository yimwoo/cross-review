# cross-review

Multi-model structured technical review engine. Sends your technical question to a **Builder** (proposes a solution), then to one or more **Reviewers** (critique it), and reconciles findings locally in Python — no LLM arbitration. Produces structured decision-support output with consensus findings, conflicts, shortcut warnings, and decision points.

## How It Works

```text
Question → Builder (Claude) → Reviewer(s) (OpenAI, Gemini) → Local Reconciliation → Structured Output
```

**Default roles:**

| Role | Provider | Model |
|------|----------|-------|
| Builder | Claude | claude-sonnet-4-20250514 |
| Skeptic Reviewer | OpenAI | gpt-5.2 |
| Pragmatist Reviewer | Gemini | gemini-2.5-pro |

## Installation

```bash
git clone https://github.com/yimwoo/cross-review.git
cd cross-review
pip install .
```

`cross-review` is not currently published on PyPI, so `pip install cross-review` will fail. Install from source instead. The install exposes both `cross-review` and the shorter `cr` command.

PyPI/TestPyPI release automation is configured in:
- `.github/workflows/release.yml`
- `.github/workflows/testpypi.yml`

Maintainer release steps are documented in `docs/releasing.md`.

For MCP server support:

```bash
pip install ".[mcp]"
```

For development:

```bash
pip install -e ".[dev]"
```

## Auth Modes

cross-review supports two auth modes, resolved automatically:

| Mode | How it works | When it's used |
|------|-------------|----------------|
| `provider_managed` | Direct API calls with your keys | API keys are set |
| `host_managed` | Delegates LLM calls to the MCP host via sampling | Running inside Claude Code (or Codex) with no API keys |

**Auto-detection** (default): if API keys are set, uses `provider_managed`. If running as an MCP server inside a host with sampling support and no keys, uses `host_managed`. If neither, shows a clear error.

In host-managed mode, all roles use the host's single provider (e.g. Claude via Claude Code). Reviewers are capped at 1 since same-model multi-reviewer has diminishing returns. Output includes a warning noting the single-provider limitation.

### API Keys (provider-managed)

For multi-provider cross-model reviews, set API keys:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...
```

### No Keys Required (host-managed)

When running as an MCP server inside Claude Code, no API keys are needed — cross-review automatically delegates LLM calls to the host via MCP sampling.

## Usage

### CLI

```bash
# Default review mode (Builder + 1 Reviewer)
cr run "Design a caching layer for a multi-tenant SaaS app"

# Fast mode (Builder only, 1 LLM call)
cr run --mode fast "Name this service"

# Arbitration mode (Builder + all Reviewers in parallel)
cr run --mode arbitration "Design the auth flow for production"

# JSON output
cr run --output json "Design a rate limiter"

# Include a file as context
cr run --context-file schema.sql "Review this database schema"

# Custom config
cr run --config ./my-config.toml "Review this API design"
```

`cr` is the recommended terminal alias. `cross-review` still works and remains the canonical command for MCP/server configs.

### Execution Modes

| Mode | LLM Calls | When to Use |
|------|-----------|-------------|
| `fast` | 1 (Builder only) | Brainstorming, naming, low-risk tasks |
| `review` | 2 (Builder + Skeptic) | Design review, API planning, schema choices (default) |
| `arbitration` | 3+ (Builder + all Reviewers) | Auth, security, production architecture, migrations |

### Claude Code — Slash Command

Copy the command file into your Claude Code commands directory:

```bash
# User-level (available in all projects)
mkdir -p ~/.claude/commands
cp commands/cr.md ~/.claude/commands/cr.md

# Or project-level (available in this project only)
mkdir -p .claude/commands
cp commands/cr.md .claude/commands/cr.md
```

Then use it in Claude Code:

```bash
/cr "Design a production-ready caching layer"
```

### Claude Code — MCP Server

Add to your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "cross-review": {
      "command": "cross-review",
      "args": ["mcp"]
    }
  }
}
```

Claude Code will automatically start the MCP server and expose `cross_review` as a tool. You can also use it with any other MCP-compatible host (Codex, etc.).

When running as an MCP server, cross-review auto-detects whether API keys are available. If not, it uses the host's own LLM via MCP sampling — no configuration needed.

### HOTL Skill

If you use [HOTL](https://github.com/yimwoo/hotl-plugin), install the skill file:

```bash
cp skills/cross-review.md <your-hotl-skills-directory>/cross-review.md
```

Then invoke via `/hotl:cross-review "Review this architecture"`.

## Configuration

Configuration is loaded with the following precedence (highest to lowest):

1. CLI flags
2. Environment variables (`CROSS_REVIEW_<SECTION>_<KEY>`)
3. Config file (`~/.config/cross-review/config.toml`)
4. Built-in defaults

### Example `config.toml`

```toml
[router]
default_mode = "review"

[budget]
max_total_calls = 4
max_reviewers = 2
soft_token_limit = 20000
hard_token_limit = 30000
orchestration_timeout_seconds = 60

[roles.builder]
provider = "claude"
model = "claude-sonnet-4-20250514"

[roles.skeptic_reviewer]
provider = "openai"
model = "gpt-5.2"

[roles.pragmatist_reviewer]
provider = "gemini"
model = "gemini-2.5-pro"
```

### Custom Providers

`cross-review` includes built-in provider aliases for `claude`, `openai`, `gemini`, and `ollama`. You can also register any OpenAI-compatible provider in config:

```toml
[providers.deepseek]
type = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
default_model = "deepseek-chat"

[roles.builder]
provider = "deepseek"
model = "deepseek-reasoner"
```

If a provider defines `default_model`, you can omit `model` in the role:

```toml
[providers.ollama]
type = "openai_compatible"
base_url = "http://localhost:11434/v1"
default_model = "gemma3:1b"

[roles.builder]
provider = "ollama"
```

For local smoke tests with Ollama:

```bash
export OLLAMA_SMOKE=1
export OLLAMA_MODEL=gemma3:1b
pytest tests/test_e2e_ollama.py -v
```

### Environment Variable Overrides

```bash
export CROSS_REVIEW_BUDGET_MAX_TOTAL_CALLS=6
export CROSS_REVIEW_BUDGET_HARD_TOKEN_LIMIT=50000
export CROSS_REVIEW_ROUTER_DEFAULT_MODE=arbitration
```

## Output Formats

- **markdown** (default) — human-readable with sections for recommendations, findings, conflicts, and trace info
- **json** — full structured output, machine-parseable
- **summary** — single-line compact summary with counts

## Development

```bash
git clone <repo-url>
cd cross-review
make install-dev

# See available commands
make help

# Run the same local checks as CI
make dev-check

# Build release artifacts
make build
make check-dist
```

## License

MIT
