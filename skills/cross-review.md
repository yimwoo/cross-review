---
name: cross-review
description: Run structured multi-model technical review via cross-review CLI
---

# Cross-Review Skill

Run a structured multi-model technical review with Builder + Reviewer roles,
local reconciliation, and decision-support output.

## When to Use

- User asks for architecture review
- User asks for design critique
- User asks for cross-model review
- User wants a second opinion from multiple AI models

## Process

1. **Determine mode** from user intent:
   - `fast` — brainstorming, naming, low-risk tasks
   - `review` (default) — design review, API planning, schema choices
   - `arbitration` — auth, security, production architecture, migrations

2. **Gather context** if relevant:
   - If a specific file is being discussed, read it and save to a temp file
   - If recent git changes are relevant, capture `git diff` to a temp file

3. **Build and run the CLI command:**

   Without context:
   ```bash
   cross-review run --mode <mode> --output markdown "<question>"
   ```

   With context file:
   ```bash
   cross-review run --mode <mode> --output markdown --context-file <path> "<question>"
   ```

4. **Present the results** to the user in full.

5. **Offer follow-up:** Ask if they want to adjust mode or dig deeper on any finding.
