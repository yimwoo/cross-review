#!/usr/bin/env bash
# install-cline-mcp.sh — Install cross-review and configure it as a Cline MCP tool.
#
# Usage:  bash scripts/install-cline-mcp.sh
#
# What this script does:
#   1. Detects or installs Python >= 3.11
#   2. Installs cross-review with MCP extras
#   3. Adds the cross-review MCP server to Cline's config
#   4. Verifies the installation
set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; }

# --- Step 1: Find Python ---
info "Checking Python..."

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major="${version%%.*}"
        minor="${version#*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python >= 3.11 not found."
    echo "  Install Python 3.11+ via pyenv, brew, or your package manager."
    exit 1
fi

PYTHON_PATH="$(command -v "$PYTHON")"
info "Using $PYTHON_PATH ($($PYTHON --version))"

# --- Step 2: Install cross-review with MCP extras ---
info "Installing cross-review with MCP support..."

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ -f "$REPO_DIR/pyproject.toml" ]; then
    # Install from local repo (development)
    "$PYTHON" -m pip install -q "$REPO_DIR[mcp]"
else
    # Install from GitHub
    "$PYTHON" -m pip install -q "cross-review[mcp] @ git+https://github.com/yimwoo/cross-review.git@feat/oca-cline-integration"
fi

# Find the installed binary
CR_BIN=""
for candidate in \
    "$("$PYTHON" -c "import sysconfig; print(sysconfig.get_path('scripts'))")/cross-review" \
    "$(dirname "$PYTHON_PATH")/cross-review" \
    "$HOME/.local/bin/cross-review"; do
    if [ -x "$candidate" ]; then
        CR_BIN="$candidate"
        break
    fi
done

if [ -z "$CR_BIN" ]; then
    # Try which
    CR_BIN="$(command -v cross-review 2>/dev/null || true)"
fi

if [ -z "$CR_BIN" ]; then
    error "cross-review binary not found after install."
    echo "  Try: $PYTHON -m pip install --user '$REPO_DIR[mcp]'"
    exit 1
fi

info "cross-review installed at: $CR_BIN"

# Verify MCP server works
if ! "$CR_BIN" mcp --help &>/dev/null; then
    error "cross-review mcp command not working."
    exit 1
fi
info "MCP server verified."

# --- Step 3: Configure Cline MCP ---
info "Configuring Cline MCP server..."

# Cline stores MCP config in VS Code's globalStorage.
# The path varies by OS:
#   macOS:   ~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/
#   Linux:   ~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/
#   Windows: $APPDATA/Code/User/globalStorage/saoudrizwan.claude-dev/settings/

CLINE_SUBDIR="Code/User/globalStorage/saoudrizwan.claude-dev/settings"
case "$(uname -s)" in
    Darwin)
        VSCODE_CLINE_DIR="$HOME/Library/Application Support/$CLINE_SUBDIR"
        ;;
    Linux)
        VSCODE_CLINE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$CLINE_SUBDIR"
        ;;
    MINGW*|MSYS*|CYGWIN*)
        # Windows (Git Bash / MSYS2 / Cygwin)
        VSCODE_CLINE_DIR="${APPDATA:-$HOME/AppData/Roaming}/$CLINE_SUBDIR"
        ;;
    *)
        VSCODE_CLINE_DIR=""
        ;;
esac

CLINE_MCP_CONFIG=""
CANDIDATES=()
[ -n "$VSCODE_CLINE_DIR" ] && CANDIDATES+=("$VSCODE_CLINE_DIR/cline_mcp_settings.json")
CANDIDATES+=("$HOME/.cline/data/settings/cline_mcp_settings.json")
CANDIDATES+=("$HOME/.cline/mcp_settings.json")

for candidate in "${CANDIDATES[@]}"; do
    if [ -f "$candidate" ]; then
        CLINE_MCP_CONFIG="$candidate"
        break
    fi
done

if [ -z "$CLINE_MCP_CONFIG" ]; then
    # Try to create in the VS Code globalStorage path first
    if [ -n "$VSCODE_CLINE_DIR" ] && [ -d "$VSCODE_CLINE_DIR" ]; then
        CLINE_MCP_CONFIG="$VSCODE_CLINE_DIR/cline_mcp_settings.json"
        echo '{"mcpServers":{}}' > "$CLINE_MCP_CONFIG"
        info "Created new Cline MCP config at $CLINE_MCP_CONFIG"
    elif [ -n "$VSCODE_CLINE_DIR" ] && [ -d "$(dirname "$VSCODE_CLINE_DIR")" ]; then
        mkdir -p "$VSCODE_CLINE_DIR"
        CLINE_MCP_CONFIG="$VSCODE_CLINE_DIR/cline_mcp_settings.json"
        echo '{"mcpServers":{}}' > "$CLINE_MCP_CONFIG"
        info "Created new Cline MCP config at $CLINE_MCP_CONFIG"
    else
        warn "Cline VS Code extension directory not found. Is Cline installed?"
        echo ""
        echo "  To configure manually, add this to your Cline MCP settings:"
        echo ""
        echo "  {\"mcpServers\":{\"cross-review\":{\"command\":\"$CR_BIN\",\"args\":[\"mcp\"]}}}"
        echo ""
        exit 0
    fi
fi

info "Found Cline MCP config: $CLINE_MCP_CONFIG"

# Add cross-review to the MCP config (or update if exists)
"$PYTHON" -c "
import json, sys

config_path = '$CLINE_MCP_CONFIG'
cr_bin = '$CR_BIN'

try:
    with open(config_path) as f:
        config = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    config = {}

if 'mcpServers' not in config:
    config['mcpServers'] = {}

existing = config['mcpServers'].get('cross-review')
if existing and existing.get('command') == cr_bin:
    print('cross-review already configured in Cline MCP.')
    sys.exit(0)

config['mcpServers']['cross-review'] = {
    'command': cr_bin,
    'args': ['mcp'],
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print('cross-review added to Cline MCP config.')
"

# --- Step 4: Verify OCA token ---
echo ""
"$PYTHON" -c "
from cross_review.oca_discovery import find_oca_token_with_refresh
token = find_oca_token_with_refresh()
if token:
    print('\033[0;32m[+]\033[0m OCA token found — ready to use!')
else:
    print('\033[0;33m[!]\033[0m No OCA token found. Log into OCA via Cline first.')
"

# --- Done ---
echo ""
echo -e "${BOLD}Installation complete!${NC}"
echo ""
echo "  Next steps:"
echo "  1. Restart Cline (or reload the MCP servers in Cline settings)"
echo "  2. In Cline chat, ask:"
echo "     \"Use cross_review to review my design\""
echo ""
echo "  The MCP server auto-discovers your OCA token from Cline's login."
echo "  Tokens are auto-refreshed when they expire."
echo ""
echo "  Config: $CLINE_MCP_CONFIG"
echo "  Binary: $CR_BIN"
