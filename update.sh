#!/usr/bin/env bash
# =============================================================================
# update.sh -- One-command update for an EXISTING cobalt-agent install.
#
# Pulls latest and redeploys the cobalt-routing plugin into Hermes with nothing
# manual:
#   1. git pull (fast-forward only)
#   2. copy the plugin sources into ~/.hermes/plugins/cobalt-routing/
#   3. scrub the stale __pycache__
#   4. restart the Hermes gateway (Telegram/WhatsApp pick up the new plugin)
#
# Self-contained and independent of iris-ai. Safe to re-run. For a first-time
# install (deps, skills, MCP wiring, opencode) use install.sh instead.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/cobalt-routing"

if [ -t 1 ]; then
  C_RESET="\033[0m"; C_BOLD="\033[1m"; C_GREEN="\033[32m"
  C_YELLOW="\033[33m"; C_RED="\033[31m"; C_CYAN="\033[36m"
else
  C_RESET=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""
fi
log()  { printf "%b[cobalt]%b %s\n" "$C_CYAN$C_BOLD" "$C_RESET" "$*"; }
ok()   { printf "%b[cobalt] OK%b %s\n" "$C_GREEN$C_BOLD" "$C_RESET" "$*"; }
warn() { printf "%b[cobalt] WARN%b %s\n" "$C_YELLOW$C_BOLD" "$C_RESET" "$*" >&2; }
die()  { printf "%b[cobalt] ERROR%b %s\n" "$C_RED$C_BOLD" "$C_RESET" "$*" >&2; exit 1; }

NO_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --no-restart) NO_RESTART=1 ;;
    --help|-h)
      printf "Usage: bash update.sh [--no-restart]\n  --no-restart  Redeploy the plugin but do not restart the gateway.\n"
      exit 0 ;;
    *) die "Unknown flag: $arg (see --help)" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git not found."
[ -d .git ] || die "Not a git checkout: $SCRIPT_DIR. Use install.sh for a fresh install."
[ -d "$PLUGIN_DIR" ] || die "$PLUGIN_DIR not found. Run install.sh first (this updater only redeploys)."

# Files that make up the cobalt-routing plugin (kept in sync with install.sh).
PLUGIN_FILES=(
  "__init__.py" "router.py" "tool_guard.py" "skill_injector.py" "sdd_triage.py"
  "memory_protocol.py" "markitdown_protocol.py" "iris_protocol.py" "firewall.py"
  "firewall_tool.py" "context_loader.py" "version_manager.py" "compat.py"
  "preset_tool.py" "config.py" "utils.py" "plugin.yaml" "presets.yaml"
)

# ---------------------------------------------------------------------------
# 1. Pull latest
# ---------------------------------------------------------------------------
BEFORE="$(git rev-parse HEAD)"
log "Pulling latest (fast-forward only)..."
git pull --ff-only 2>&1 | sed 's/^/  /'
AFTER="$(git rev-parse HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  log "Already at the latest commit ($AFTER). Redeploying anyway to be safe."
else
  ok "Updated ${BEFORE:0:9} -> ${AFTER:0:9}"
fi

# ---------------------------------------------------------------------------
# 2. Redeploy plugin sources
# ---------------------------------------------------------------------------
rm -rf "$PLUGIN_DIR/__pycache__"
COPIED=0
for f in "${PLUGIN_FILES[@]}"; do
  if [ -f "$SCRIPT_DIR/src/$f" ]; then
    cp "$SCRIPT_DIR/src/$f" "$PLUGIN_DIR/$f"
    COPIED=$((COPIED + 1))
  fi
done
ok "Redeployed $COPIED plugin file(s) to $PLUGIN_DIR"

# ---------------------------------------------------------------------------
# 3. Restart the Hermes gateway
# ---------------------------------------------------------------------------
if [ "$NO_RESTART" -eq 1 ]; then
  warn "--no-restart: plugin redeployed; restart the gateway to apply it."
else
  log "Restarting Hermes gateway..."
  if command -v systemctl >/dev/null 2>&1 && systemctl --user restart hermes-gateway 2>/dev/null; then
    ok "hermes-gateway restarted (systemd --user)."
  elif command -v hermes >/dev/null 2>&1; then
    nohup hermes gateway run --replace >/dev/null 2>&1 &
    ok "hermes gateway relaunched (--replace)."
  else
    warn "Could not restart the gateway automatically. Restart it yourself to apply the plugin."
  fi
fi

printf "\n%b[cobalt] Update complete.%b\n" "$C_GREEN$C_BOLD" "$C_RESET"
