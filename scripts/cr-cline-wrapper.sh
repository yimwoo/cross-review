#!/usr/bin/env bash
# cr-cline-wrapper.sh — Run cross-review using Cline's OCA login state.
#
# Usage:  bash scripts/cr-cline-wrapper.sh [cr run arguments...]
# Example: bash scripts/cr-cline-wrapper.sh --mode review "Design a cache"
#
# Model selection (env vars):
#   OCA_MODEL           — default model for all roles  (default: oca/gpt-5.4)
#   OCA_MODEL_BUILDER   — builder role model           (default: $OCA_MODEL)
#   OCA_MODEL_SKEPTIC   — skeptic reviewer model       (default: $OCA_MODEL)
#   OCA_MODEL_PRAGMATIST — pragmatist reviewer model   (default: $OCA_MODEL)
#
# Examples:
#   # Use gpt-5.3-codex for building, gpt-5.4 for reviewing:
#   OCA_MODEL_BUILDER=oca/gpt-5.3-codex OCA_MODEL_SKEPTIC=oca/gpt-5.4 \
#     bash scripts/cr-cline-wrapper.sh --mode review "Design a cache"
#
#   # Use one model for everything:
#   OCA_MODEL=oca/gpt-oss-120b bash scripts/cr-cline-wrapper.sh "Quick check"
#
# This script:
#   1. Locates the OCA access token from Cline's VS Code secret storage
#   2. Writes it to a temporary file
#   3. Generates a temporary cross-review TOML config with api_key_file
#   4. Invokes `cr run` with the generated config
#   5. Cleans up temp files on exit
set -euo pipefail

# --- Configuration ---
OCA_BASE_URL="${OCA_BASE_URL:-https://code-internal.aiservice.us-chicago-1.oci.oraclecloud.com/20250206/app/litellm/v1}"
OCA_MODEL="${OCA_MODEL:-oca/gpt-5.4}"

# Per-role model overrides (fall back to OCA_MODEL)
OCA_MODEL_BUILDER="${OCA_MODEL_BUILDER:-$OCA_MODEL}"
OCA_MODEL_SKEPTIC="${OCA_MODEL_SKEPTIC:-$OCA_MODEL}"
OCA_MODEL_PRAGMATIST="${OCA_MODEL_PRAGMATIST:-$OCA_MODEL}"

# --- Temp file cleanup ---
TMPDIR_CR=""
cleanup() {
    if [ -n "$TMPDIR_CR" ] && [ -d "$TMPDIR_CR" ]; then
        rm -rf "$TMPDIR_CR"
    fi
}
trap cleanup EXIT

# --- Locate OCA token ---
# Cline stores secrets in VS Code's secret storage, which on macOS is backed
# by the Keychain.  The globalState JSON file contains non-secret state.
# For OCA, the token is typically available via the OCA extension's local
# storage or can be read from the keychain.
#
# Strategy (in priority order):
#   1. OCA_TOKEN environment variable (explicit override)
#   2. Read from Cline's secrets.json (~/.cline/data/secrets.json)
#   3. Read from a well-known token file (~/.oca/token)

find_oca_token() {
    # 1. Environment variable
    if [ -n "${OCA_TOKEN:-}" ]; then
        echo "$OCA_TOKEN"
        return 0
    fi

    # 2. Cline's file-backed secrets
    local cline_secrets="${HOME}/.cline/data/secrets.json"
    if [ -f "$cline_secrets" ]; then
        local token
        token="$(python3 -c "
import json, sys
try:
    d = json.load(open('$cline_secrets'))
    t = d.get('ocaApiKey', '')
    if t:
        print(t, end='')
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
" 2>/dev/null)" && [ -n "$token" ] && {
            echo "$token"
            return 0
        }
    fi

    # 3. Well-known token file
    local token_file="${HOME}/.oca/token"
    if [ -f "$token_file" ]; then
        cat "$token_file"
        return 0
    fi

    return 1
}

OCA_TOKEN_VALUE=""
if ! OCA_TOKEN_VALUE="$(find_oca_token)"; then
    echo "Error: Could not locate OCA token." >&2
    echo "Set OCA_TOKEN, write to ~/.oca/token, or log in via Cline" >&2
    exit 1
fi

if [ -z "$OCA_TOKEN_VALUE" ]; then
    echo "Error: OCA token is empty." >&2
    exit 1
fi

# --- Write temp token file ---
TMPDIR_CR="$(mktemp -d)"
TOKEN_FILE="${TMPDIR_CR}/oca-token"
printf '%s' "$OCA_TOKEN_VALUE" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

# --- Generate temp config ---
CONFIG_FILE="${TMPDIR_CR}/cross-review.toml"
cat > "$CONFIG_FILE" <<TOML
[providers.oca]
type = "openai_compatible"
base_url = "${OCA_BASE_URL}"
api_key_file = "${TOKEN_FILE}"
default_model = "${OCA_MODEL}"

[roles.builder]
provider = "oca"
model = "${OCA_MODEL_BUILDER}"

[roles.skeptic_reviewer]
provider = "oca"
model = "${OCA_MODEL_SKEPTIC}"

[roles.pragmatist_reviewer]
provider = "oca"
model = "${OCA_MODEL_PRAGMATIST}"
TOML

# --- Run cross-review ---
exec cr run --config "$CONFIG_FILE" "$@"
