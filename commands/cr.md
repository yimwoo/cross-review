# Cross-Review

Run a structured multi-model technical review with Builder + Reviewer roles,
local reconciliation, and decision-support output.

## Usage

The user provides a technical question. Determine the appropriate mode:
- `fast` for brainstorming, naming, low-risk tasks
- `review` (default) for design review, API planning, schema choices
- `arbitration` for auth, security, production architecture, migrations

## Steps

1. Identify the question from the user's input: $ARGUMENTS
2. Choose mode based on question complexity and risk:
   - If the question involves auth, security, secrets, production, migration,
     or infrastructure → use `arbitration`
   - If the question involves API design, schema, database, caching,
     architecture → use `review`
   - If the question is simple brainstorming or naming → use `fast`
   - When in doubt → use `review`
3. If relevant context exists (current file being discussed, recent git diff),
   save it to a temp file and use `--context-file`
4. Run the CLI:
   ```bash
   cr run --mode <mode> --output markdown "<question>"
   ```
5. Present the full results to the user
6. Ask if they want to adjust mode or dig deeper on any finding
