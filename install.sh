#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# cobalt-agent installer / updater
# https://github.com/thestark77/cobalt-agent
#
# Single command: curl -fsSL https://raw.githubusercontent.com/thestark77/cobalt-agent/main/install.sh | bash
# Or locally:     bash install.sh
#
# Runs the same whether fresh install or update — always converges to the
# tested configuration without touching private credentials.
# ============================================================================

COBALT_VERSION="0.7.0"
HERMES_REPO="https://github.com/NousResearch/hermes-agent.git"
HERMES_TESTED_TAG=""
HERMES_TESTED_VERSION="0.12.0"
HERMES_MAX_COMPATIBLE="0.12.99"
HERMES_WARN_FROM="0.13.0"
HERMES_ERROR_FROM="1.0.0"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_AGENT_DIR="$HERMES_HOME/hermes-agent"
PLUGIN_DIR="$HERMES_HOME/plugins/cobalt-routing"
COBALT_REPO="https://github.com/thestark77/cobalt-agent.git"
COBALT_TMP="/tmp/cobalt-agent-$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()   { echo -e "${GREEN}[cobalt]${NC} $*"; }
warn()  { echo -e "${YELLOW}[cobalt]${NC} $*"; }
err()   { echo -e "${RED}[cobalt]${NC} $*" >&2; }
info()  { echo -e "${CYAN}[cobalt]${NC} $*"; }
header(){ echo -e "\n${BLUE}━━━ $* ━━━${NC}\n"; }

cleanup() { rm -rf "$COBALT_TMP" 2>/dev/null || true; }
trap cleanup EXIT

# ── Helpers ─────────────────────────────────────────────────────────────────

version_tuple() {
    local v="$1"
    local major minor patch
    major=$(echo "$v" | cut -d. -f1)
    minor=$(echo "$v" | cut -d. -f2)
    patch=$(echo "$v" | cut -d. -f3)
    printf "%03d%03d%03d" "${major:-0}" "${minor:-0}" "${patch:-0}"
}

version_gte() { [ "$(version_tuple "$1")" -ge "$(version_tuple "$2")" ]; }
version_gt()  { [ "$(version_tuple "$1")" -gt "$(version_tuple "$2")" ]; }

read_hermes_version() {
    local pyproject="$HERMES_AGENT_DIR/pyproject.toml"
    if [ -f "$pyproject" ]; then
        python3 -c "
import tomllib, sys
with open('$pyproject', 'rb') as f:
    data = tomllib.load(f)
print(data.get('project', {}).get('version', '0.0.0'))
" 2>/dev/null || echo "0.0.0"
    else
        echo "0.0.0"
    fi
}

check_hermes_compat() {
    local ver="$1"
    if version_gte "$ver" "$HERMES_ERROR_FROM"; then
        err "Hermes $ver is INCOMPATIBLE with cobalt-routing v${COBALT_VERSION}"
        err "cobalt-routing was tested on Hermes $HERMES_TESTED_VERSION"
        err "Hermes >= $HERMES_ERROR_FROM introduces breaking changes."
        err "Wait for a cobalt-agent update or pin Hermes to a compatible version."
        exit 1
    fi
    if version_gte "$ver" "$HERMES_WARN_FROM"; then
        warn "Hermes $ver is ABOVE the tested version ($HERMES_TESTED_VERSION)"
        warn "cobalt-routing may still work, but has not been validated on this version."
        warn "Proceeding anyway — report issues at https://github.com/thestark77/cobalt-agent/issues"
        echo ""
    fi
}

# ── Detect install vs update ────────────────────────────────────────────────

IS_UPDATE=0
if [ -f "$HERMES_AGENT_DIR/pyproject.toml" ] && [ -f "$PLUGIN_DIR/__init__.py" ]; then
    IS_UPDATE=1
fi

# ============================================================================
header "cobalt-agent v${COBALT_VERSION} — $([ "$IS_UPDATE" -eq 1 ] && echo 'Updater' || echo 'Installer')"
# ============================================================================

if [ "$IS_UPDATE" -eq 1 ]; then
    CURRENT_VER=$(read_hermes_version)
    log "Existing installation detected (Hermes $CURRENT_VER)"
    log "Mode: UPDATE — will upgrade components to tested versions"
    echo ""
    echo "This updater will:"
    echo "  1. Check prerequisites"
    echo "  2. Update Hermes Agent to latest tested version"
    echo "  3. Update OpenCode Go provider"
    echo "  4. Re-apply source patch (safe if already applied)"
    echo "  5. Update cobalt-routing plugin to v${COBALT_VERSION}"
    echo "  6. Update SOUL.md + merge config (preserves your settings)"
    echo "  7. Update skills"
    echo "  8. Verify everything"
else
    echo "This installer will set up:"
    echo "  1. Prerequisites check"
    echo "  2. Hermes Agent (latest tested version)"
    echo "  3. OpenCode Go provider (free model access)"
    echo "  4. Source patch (delegate_tool.py routing hook)"
    echo "  5. cobalt-routing plugin (model routing + tool guard + skills)"
    echo "  6. SOUL.md + configuration"
    echo "  7. Skills (10 curated skills)"
    echo "  8. Verification"
fi
echo ""

# ============================================================================
header "Step 1/8: Prerequisites"
# ============================================================================

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        err "$1 not found. Please install it first."
        return 1
    fi
    log "$1 found: $(command -v "$1")"
}

MISSING=0
check_cmd git || MISSING=1
check_cmd python3 || MISSING=1
check_cmd curl || MISSING=1
check_cmd pip3 || check_cmd pip || MISSING=1

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    err "Python 3.11+ required (found $PYTHON_VERSION)"
    MISSING=1
fi

if [ "$MISSING" -eq 1 ]; then
    err "Missing prerequisites. Install them and re-run."
    exit 1
fi

log "Python $PYTHON_VERSION OK"

HAS_NPM=0
if command -v npm &>/dev/null || command -v npx &>/dev/null; then
    HAS_NPM=1
    log "npm found"
else
    warn "npm not found. OpenCode CLI won't be installed (you can configure provider manually)"
fi

# ============================================================================
header "Step 2/8: Hermes Agent"
# ============================================================================

VENV_DIR="$HERMES_AGENT_DIR/venv"

if [ -f "$HERMES_AGENT_DIR/pyproject.toml" ]; then
    CURRENT_VER=$(read_hermes_version)
    log "Hermes Agent found at $HERMES_AGENT_DIR (v$CURRENT_VER)"

    check_hermes_compat "$CURRENT_VER"

    if [ "$IS_UPDATE" -eq 1 ]; then
        log "Updating Hermes Agent..."
        cd "$HERMES_AGENT_DIR"
        git fetch origin 2>/dev/null || warn "Could not fetch updates (network issue?)"

        if [ -n "$HERMES_TESTED_TAG" ]; then
            git checkout "$HERMES_TESTED_TAG" 2>/dev/null || {
                warn "Tag $HERMES_TESTED_TAG not found, staying on current version"
            }
        else
            git pull origin main 2>/dev/null || warn "Could not pull updates"
        fi
        cd - >/dev/null

        NEW_VER=$(read_hermes_version)
        if [ "$CURRENT_VER" != "$NEW_VER" ]; then
            log "Updated: $CURRENT_VER → $NEW_VER"
            check_hermes_compat "$NEW_VER"
        else
            log "Already at latest compatible version ($CURRENT_VER)"
        fi
    fi
else
    log "Installing Hermes Agent..."
    mkdir -p "$HERMES_HOME"
    if [ -n "$HERMES_TESTED_TAG" ]; then
        git clone --depth 1 --branch "$HERMES_TESTED_TAG" "$HERMES_REPO" "$HERMES_AGENT_DIR"
    else
        git clone --depth 1 "$HERMES_REPO" "$HERMES_AGENT_DIR"
    fi
    log "Hermes Agent cloned"

    FRESH_VER=$(read_hermes_version)
    check_hermes_compat "$FRESH_VER"
fi

if [ ! -f "$VENV_DIR/bin/pip" ]; then
    log "Creating Python virtual environment..."
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

log "Installing Hermes dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -e "$HERMES_AGENT_DIR"

mkdir -p "$HOME/.local/bin"
ln -sf "$VENV_DIR/bin/hermes" "$HOME/.local/bin/hermes"
log "Linked hermes -> ~/.local/bin/hermes"

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    export PATH="$HOME/.local/bin:$PATH"
    log "Added ~/.local/bin to PATH"
fi

FINAL_VER=$(read_hermes_version)
log "Hermes Agent v$FINAL_VER ready"

# ============================================================================
header "Step 3/8: OpenCode Go Provider"
# ============================================================================

if [ "$HAS_NPM" -eq 1 ]; then
    if ! command -v opencode &>/dev/null; then
        log "Installing OpenCode CLI..."
        npm install -g @anthropics/opencode 2>/dev/null || npm install -g opencode 2>/dev/null || {
            warn "OpenCode CLI install failed. You'll need to configure provider manually."
            warn "Set model.base_url in ~/.hermes/config.yaml to your provider's endpoint."
        }
    else
        log "OpenCode CLI already installed"
    fi
fi

log "Provider will be configured in Step 6"

# ============================================================================
header "Step 4/8: Source Patch (delegate_tool.py)"
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

if [ -d "$SCRIPT_DIR/src" ] && [ -f "$SCRIPT_DIR/src/__init__.py" ]; then
    COBALT_TMP="$SCRIPT_DIR"
    log "Using local source files ($SCRIPT_DIR)"
else
    log "Fetching cobalt-agent source from GitHub..."
    if [ -d "$COBALT_TMP" ]; then
        rm -rf "$COBALT_TMP"
    fi
    git clone --depth 1 "$COBALT_REPO" "$COBALT_TMP" 2>/dev/null || {
        err "Cannot fetch cobalt-agent source and no local files found"
        exit 1
    }
fi

PATCH_SCRIPT="$COBALT_TMP/patches/apply_routing_patch.py"
if [ -f "$PATCH_SCRIPT" ]; then
    PATCH_STATUS=$("$VENV_DIR/bin/python" "$PATCH_SCRIPT" verify 2>&1 || true)
    if echo "$PATCH_STATUS" | grep -qi "applied\|verified\|true"; then
        log "Source patch already applied (idempotent check passed)"
        log "Re-applying to ensure latest version..."
    fi
    "$VENV_DIR/bin/python" "$PATCH_SCRIPT" apply || {
        warn "Patch apply returned non-zero — checking if already applied..."
        if "$VENV_DIR/bin/python" "$PATCH_SCRIPT" verify 2>/dev/null; then
            log "Patch verified OK (was already applied)"
        else
            warn "Patch could not be applied or verified (routing will use inference fallback)"
        fi
    }
else
    warn "Patch script not found, skipping (routing will use inference only)"
fi

# ============================================================================
header "Step 5/8: cobalt-routing Plugin"
# ============================================================================

if [ "$IS_UPDATE" -eq 1 ] && [ -f "$PLUGIN_DIR/__init__.py" ]; then
    OLD_PLUGIN_VER=$(python3 -c "
import re
text = open('$PLUGIN_DIR/__init__.py').read()
m = re.search(r'PLUGIN_VERSION\s*=\s*\"(.*?)\"', text)
print(m.group(1) if m else 'unknown')
" 2>/dev/null || echo "unknown")
    log "Updating plugin: v$OLD_PLUGIN_VER -> v$COBALT_VERSION"
fi

mkdir -p "$PLUGIN_DIR"

PLUGIN_FILES=(
    "__init__.py"
    "router.py"
    "tool_guard.py"
    "skill_injector.py"
    "sdd_triage.py"
    "version_manager.py"
    "compat.py"
    "preset_tool.py"
    "plugin.yaml"
    "presets.yaml"
)

COPIED=0
for f in "${PLUGIN_FILES[@]}"; do
    if [ -f "$COBALT_TMP/src/$f" ]; then
        cp "$COBALT_TMP/src/$f" "$PLUGIN_DIR/$f"
        COPIED=$((COPIED + 1))
    fi
done

log "Plugin installed at $PLUGIN_DIR ($COPIED files)"

# ============================================================================
header "Step 6/8: SOUL.md + Configuration"
# ============================================================================

if [ -f "$COBALT_TMP/SOUL.md" ]; then
    cp "$COBALT_TMP/SOUL.md" "$HERMES_HOME/SOUL.md"
    log "SOUL.md deployed"
else
    warn "SOUL.md not found in source, skipping"
fi

CONFIG_FILE="$HERMES_HOME/config.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
    log "Creating config.yaml..."
    cat > "$CONFIG_FILE" << 'YAML'
_config_version: 23
model:
  api_mode: chat_completions
  base_url: https://opencode.ai/zen/go/v1
  default: kimi-k2.6
  provider: opencode-go
delegation:
  child_timeout_seconds: 600
  inherit_mcp_toolsets: true
  max_concurrent_children: 3
  max_iterations: 50
  max_spawn_depth: 2
  model: deepseek-v4-pro
  orchestrator_enabled: true
  provider: opencode-go
agent:
  max_turns: 90
plugins:
  enabled:
  - cobalt-routing
memory:
  memory_enabled: true
  provider: honcho
display:
  show_reasoning: true
  streaming: true
  tool_progress: verbose
logging:
  level: VERBOSE
YAML
    log "config.yaml created"
else
    log "config.yaml exists, merging cobalt settings (preserving your config)..."
    "$VENV_DIR/bin/python" - << 'PYTHON'
import yaml
from pathlib import Path

config_path = Path.home() / ".hermes" / "config.yaml"
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

plugins = config.setdefault("plugins", {})
enabled = plugins.setdefault("enabled", [])
if "cobalt-routing" not in enabled:
    enabled.append("cobalt-routing")

model = config.setdefault("model", {})
if not model.get("default"):
    model["default"] = "kimi-k2.6"
if not model.get("provider"):
    model["provider"] = "opencode-go"
if not model.get("base_url"):
    model["base_url"] = "https://opencode.ai/zen/go/v1"
model.setdefault("api_mode", "chat_completions")

deleg = config.setdefault("delegation", {})
deleg.setdefault("child_timeout_seconds", 600)
deleg.setdefault("max_concurrent_children", 3)
deleg.setdefault("max_iterations", 50)
deleg.setdefault("max_spawn_depth", 2)
deleg.setdefault("orchestrator_enabled", True)
if not deleg.get("model"):
    deleg["model"] = "deepseek-v4-pro"
if not deleg.get("provider"):
    deleg["provider"] = "opencode-go"

memory = config.setdefault("memory", {})
memory.setdefault("memory_enabled", True)
memory.setdefault("provider", "honcho")

agent = config.setdefault("agent", {})
agent.setdefault("max_turns", 90)

config_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")
print("Config merged successfully")
PYTHON
fi

HONCHO_FILE="$HERMES_HOME/honcho.json"
if [ ! -f "$HONCHO_FILE" ]; then
    cat > "$HONCHO_FILE" << 'JSON'
{
  "apiKey": "YOUR_HONCHO_API_KEY_HERE",
  "enabled": true,
  "environment": "production",
  "workspace": "hermes",
  "aiPeer": "hermes",
  "peerName": "user",
  "pinPeerName": true,
  "saveMessages": true,
  "writeFrequency": "async",
  "contextCadence": 1,
  "dialecticCadence": 2
}
JSON
    warn "honcho.json created with placeholder API key"
    warn "Get your key at https://app.honcho.dev and update ~/.hermes/honcho.json"
else
    log "honcho.json already exists, keeping current (credentials preserved)"
fi

log "Configuration complete"

# ============================================================================
header "Step 7/8: Skills Installation"
# ============================================================================

SKILLS_DIR="$HERMES_HOME/skills"
mkdir -p "$SKILLS_DIR"

SKILLS=(
    "skills-sh/wshobson/agents/prompt-engineering-patterns"
    "skills-sh/anthropics/skills/frontend-design"
    "skills-sh/dammyjay93/interface-design/interface-design"
    "skills-sh/wshobson/agents/e2e-testing-patterns"
    "skills-sh/wshobson/agents/error-handling-patterns"
    "skills-sh/wshobson/agents/postgresql-table-design"
    "skills-sh/gentleman-programming/sdd-agent-team/judgment-day"
    "skills-sh/gentleman-programming/sdd-agent-team/branch-pr"
    "skills-sh/gentleman-programming/sdd-agent-team/skill-creator"
    "skills-sh/thestark77/autosdd/knowledge-graph"
)

SKILL_NAMES=(
    "prompt-engineering-patterns"
    "frontend-design"
    "interface-design"
    "e2e-testing-patterns"
    "error-handling-patterns"
    "postgresql-table-design"
    "judgment-day"
    "branch-pr"
    "skill-creator"
    "knowledge-graph"
)

log "Installing ${#SKILLS[@]} skills..."

HERMES_BIN="$HOME/.local/bin/hermes"
SKILLS_INSTALLED=0

for i in "${!SKILLS[@]}"; do
    name="${SKILL_NAMES[$i]}"
    if [ -d "$SKILLS_DIR/$name" ]; then
        if [ "$IS_UPDATE" -eq 1 ]; then
            if "$HERMES_BIN" skills install "${SKILLS[$i]}" --force 2>/dev/null; then
                log "  $name (updated)"
                SKILLS_INSTALLED=$((SKILLS_INSTALLED + 1))
            else
                log "  $name (kept existing)"
                SKILLS_INSTALLED=$((SKILLS_INSTALLED + 1))
            fi
        else
            log "  $name (already installed)"
            SKILLS_INSTALLED=$((SKILLS_INSTALLED + 1))
        fi
    else
        if "$HERMES_BIN" skills install "${SKILLS[$i]}" --force 2>/dev/null; then
            log "  $name (installed)"
            SKILLS_INSTALLED=$((SKILLS_INSTALLED + 1))
        else
            warn "  $name (failed — install manually: hermes skills install ${SKILLS[$i]})"
        fi
    fi
done

log "$SKILLS_INSTALLED/${#SKILLS[@]} skills installed"

# ============================================================================
header "Step 8/8: Verification"
# ============================================================================

CHECKS=0
TOTAL=6

# Check 1: Hermes binary
if command -v hermes &>/dev/null || [ -x "$HERMES_BIN" ]; then
    log "✓ hermes binary accessible"
    CHECKS=$((CHECKS + 1))
else
    err "✗ hermes not in PATH"
fi

# Check 2: Plugin files
if [ -f "$PLUGIN_DIR/__init__.py" ] && [ -f "$PLUGIN_DIR/router.py" ]; then
    log "✓ cobalt-routing plugin installed"
    CHECKS=$((CHECKS + 1))
else
    err "✗ plugin files missing"
fi

# Check 3: Source patch
if [ -f "$PATCH_SCRIPT" ] && "$VENV_DIR/bin/python" "$PATCH_SCRIPT" verify 2>/dev/null; then
    log "✓ delegate_tool.py patch applied"
    CHECKS=$((CHECKS + 1))
else
    warn "△ source patch not verified (routing will use inference fallback)"
    CHECKS=$((CHECKS + 1))
fi

# Check 4: SOUL.md
if [ -f "$HERMES_HOME/SOUL.md" ]; then
    log "✓ SOUL.md deployed"
    CHECKS=$((CHECKS + 1))
else
    err "✗ SOUL.md missing"
fi

# Check 5: Config has plugin enabled
if grep -q "cobalt-routing" "$CONFIG_FILE" 2>/dev/null; then
    log "✓ Plugin enabled in config"
    CHECKS=$((CHECKS + 1))
else
    err "✗ Plugin not enabled in config"
fi

# Check 6: Version compatibility
FINAL_HERMES=$(read_hermes_version)
if version_gte "$FINAL_HERMES" "$HERMES_ERROR_FROM"; then
    err "✗ Hermes $FINAL_HERMES is incompatible"
elif version_gte "$FINAL_HERMES" "$HERMES_WARN_FROM"; then
    warn "△ Hermes $FINAL_HERMES is above tested version ($HERMES_TESTED_VERSION)"
    CHECKS=$((CHECKS + 1))
else
    log "✓ Hermes $FINAL_HERMES compatible (tested: $HERMES_TESTED_VERSION)"
    CHECKS=$((CHECKS + 1))
fi

echo ""
header "$([ "$IS_UPDATE" -eq 1 ] && echo 'Update' || echo 'Installation') Complete"

if [ "$CHECKS" -eq "$TOTAL" ]; then
    log "All checks passed ($CHECKS/$TOTAL)"
else
    warn "Some checks failed ($CHECKS/$TOTAL) — review warnings above"
fi

echo ""
echo -e "${CYAN}Next steps:${NC}"
if [ ! -f "$HONCHO_FILE" ] || grep -q "YOUR_HONCHO_API_KEY_HERE" "$HONCHO_FILE" 2>/dev/null; then
    echo "  1. Configure your Honcho API key in ~/.hermes/honcho.json"
    echo "     (Get one free at https://app.honcho.dev)"
    echo ""
fi
echo "  Start Hermes:"
echo "     hermes chat"
echo ""
echo "  Test cobalt-routing:"
echo '     Ask: "Necesito un script en Python que lea un archivo JSON y genere un reporte"'
echo "     Watch for: triage -> explore -> apply -> verify phases with model routing"
echo ""
echo -e "${GREEN}cobalt-agent v${COBALT_VERSION} $([ "$IS_UPDATE" -eq 1 ] && echo 'updated' || echo 'ready').${NC}"
