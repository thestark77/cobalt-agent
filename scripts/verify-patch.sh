#!/usr/bin/env bash
# ============================================================================
# cobalt-agent — Patch verifier (VPS cron)
#
# Runs apply_routing_patch.py verify against the local Hermes install.
# On failure (or drift), sends a Telegram notification.
#
# Designed to be idempotent and silent on success. Run via cron — see
# install.sh which configures a daily entry automatically.
#
# Required env vars for notifications (read at runtime):
#   TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
#   TELEGRAM_CHAT_ID    — Target chat ID
#
# Optional:
#   HERMES_HOME         — Default: $HOME/.hermes
#   COBALT_HOME         — Default: directory containing this script's parent
#   COBALT_QUIET        — If set, suppress success output (cron-friendly)
# ============================================================================

set -uo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COBALT_HOME="${COBALT_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_PYTHON="$HERMES_HOME/hermes-agent/venv/bin/python"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || hostname)"

# Resolve apply_routing_patch.py from the most likely locations.
# Order matters: HERMES_HOME-installed copy first (survives repo deletion),
# then the local repo (works when run from the cloned source dir).
_resolve_patch_script() {
    local candidate
    for candidate in \
        "$HERMES_HOME/cobalt-patches/apply_routing_patch.py" \
        "$COBALT_HOME/patches/apply_routing_patch.py"; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}
PATCH_SCRIPT="$(_resolve_patch_script || true)"

log() {
    if [ -z "${COBALT_QUIET:-}" ]; then
        echo "[$(date -u +%FT%TZ)] $*"
    fi
}

err() {
    echo "[$(date -u +%FT%TZ)] ERROR: $*" >&2
}

read_hermes_version() {
    local pyproject="$HERMES_HOME/hermes-agent/pyproject.toml"
    if [ ! -f "$pyproject" ]; then
        echo "unknown"
        return
    fi
    python3 - "$pyproject" << 'PY' 2>/dev/null || echo "unknown"
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    print(tomllib.load(f).get("project", {}).get("version", "unknown"))
PY
}

notify_telegram() {
    local message="$1"
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        err "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping notification"
        return 1
    fi
    curl -sS --max-time 10 \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "parse_mode=Markdown" \
        --data-urlencode "text=${message}" \
        >/dev/null || err "Telegram notification failed"
}

# ── Preflight ──────────────────────────────────────────────────────────────

if [ -z "$PATCH_SCRIPT" ] || [ ! -f "$PATCH_SCRIPT" ]; then
    err "apply_routing_patch.py not found in $HERMES_HOME/cobalt-patches/ or $COBALT_HOME/patches/"
    err "Re-run install.sh to refresh the patch script copy."
    exit 2
fi

if [ ! -d "$HERMES_HOME/hermes-agent" ]; then
    err "Hermes not installed at $HERMES_HOME/hermes-agent"
    exit 2
fi

# Prefer Hermes venv python; fall back to system python3.
if [ -x "$VENV_PYTHON" ]; then
    PY="$VENV_PYTHON"
else
    PY="$(command -v python3 || true)"
fi

if [ -z "$PY" ]; then
    err "No python3 available"
    exit 2
fi

HERMES_VER="$(read_hermes_version)"
log "Verifying patch against Hermes $HERMES_VER on $HOSTNAME_SHORT"

# ── Verify ─────────────────────────────────────────────────────────────────

if "$PY" "$PATCH_SCRIPT" verify >/dev/null 2>&1; then
    log "OK — patch verified"
    exit 0
fi

# Patch not applied. Try to re-apply once before alerting.
log "Patch not verified — attempting re-apply..."
if "$PY" "$PATCH_SCRIPT" apply >/dev/null 2>&1 \
    && "$PY" "$PATCH_SCRIPT" verify >/dev/null 2>&1; then
    log "Recovered — patch re-applied successfully"
    notify_telegram "ℹ️ *cobalt-agent* on \`$HOSTNAME_SHORT\`
Patch was missing on Hermes \`$HERMES_VER\` and re-applied automatically.
No action required, but worth checking why it drifted."
    exit 0
fi

# Real failure.
err "Patch broken on Hermes $HERMES_VER"
notify_telegram "⚠️ *cobalt-agent* on \`$HOSTNAME_SHORT\`
Patch verification *failed* on Hermes \`$HERMES_VER\`.

The source pattern in \`delegate_tool.py\` likely changed. Until fixed, model routing falls back to inference only.

Check: https://github.com/thestark77/cobalt-agent/issues"
exit 1
