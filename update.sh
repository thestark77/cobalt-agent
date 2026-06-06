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
  "memory_protocol.py" "markitdown_protocol.py" "incognito.py" "iris_protocol.py" "finance_protocol.py" "reconcile.py" "karakeep_protocol.py" "ghostfolio_protocol.py" "iris_capture.py" "firewall.py"
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
# 2b. Redeploy the managed SOUL.md block (preserves the user's custom section)
#     Mirrors install.sh Step 6: only the text between the cobalt:managed
#     markers is updated; everything after cobalt:managed:end is left intact.
# ---------------------------------------------------------------------------
if [ -f "$SCRIPT_DIR/SOUL.md" ]; then
  PYBIN=""
  for cand in "$HERMES_HOME/hermes-agent/venv/bin/python" python3 python; do
    if command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ]; then PYBIN="$cand"; break; fi
  done
  # The bot's Google account lives in ~/.hermes/config.yaml under `cobalt.bot_email`,
  # alongside the other Hermes settings (telegram, mcp_servers, cobalt_firewall) —
  # no separate file. An exported COBALT_BOT_EMAIL still overrides it (for testing).
  CONFIG_FILE="$HERMES_HOME/config.yaml"
  if [ -z "${COBALT_BOT_EMAIL:-}" ] && [ -n "$PYBIN" ] && [ -f "$CONFIG_FILE" ]; then
    COBALT_BOT_EMAIL="$("$PYBIN" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])) or {}; print(((d.get("cobalt") or {}).get("bot_email")) or "")' "$CONFIG_FILE" 2>/dev/null || true)"
  fi
  if [ -z "${COBALT_BOT_EMAIL:-}" ]; then
    warn "cobalt.bot_email not set in $CONFIG_FILE — SOUL.md will say 'its own Google account'. Add a 'cobalt:' key with bot_email to name the bot account."
  fi
  if [ -n "$PYBIN" ]; then
    SOUL_RESULT=$(COBALT_BOT_EMAIL="${COBALT_BOT_EMAIL:-}" "$PYBIN" - "$SCRIPT_DIR/SOUL.md" "$HERMES_HOME/SOUL.md" << 'PYEOF'
import os, sys
from pathlib import Path

src_path, dest_path = Path(sys.argv[1]), Path(sys.argv[2])
START = "<!-- cobalt:managed:start"
END   = "<!-- cobalt:managed:end -->"
# Deploy-time placeholder substitution. Falls back to a neutral phrase so the
# orchestrator prompt never leaks the raw "{{COBALT_BOT_EMAIL}}" token.
email = os.environ.get("COBALT_BOT_EMAIL", "").strip() or "its own Google account"

def render(text):
    return text.replace("{{COBALT_BOT_EMAIL}}", email)

new_content = src_path.read_text(encoding="utf-8")

if not dest_path.exists():
    dest_path.write_text(render(new_content), encoding="utf-8")
    print("deployed (fresh)"); sys.exit(0)

existing = dest_path.read_text(encoding="utf-8")
if START not in existing:
    dest_path.with_suffix(".md.bak").write_text(existing, encoding="utf-8")
    dest_path.write_text(render(new_content), encoding="utf-8")
    print("deployed (migrated — backup saved)"); sys.exit(0)

end_idx = existing.find(END)
user_section = existing[end_idx + len(END):] if end_idx != -1 else ""
start_new, end_new = new_content.find(START), new_content.find(END)
if start_new == -1 or end_new == -1:
    dest_path.write_text(render(new_content), encoding="utf-8")
    print("deployed (untagged source)"); sys.exit(0)

managed_block = new_content[start_new : end_new + len(END)]
dest_path.write_text(render(managed_block + user_section), encoding="utf-8")
print("merged (managed section updated, user additions preserved)")
PYEOF
    )
    ok "SOUL.md $SOUL_RESULT"
  else
    warn "No python found; skipped SOUL.md redeploy. Re-run install.sh to update it."
  fi
fi

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
