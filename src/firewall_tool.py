"""Cobalt Firewall Tool — Hermes toggle tool for the irreversibility firewall.

Exposes the `cobalt_firewall` tool with actions: status, set, enable, disable.
Persists state to ~/.hermes/config.yaml under key `cobalt_firewall`.

Mirrors the structure of preset_tool.py.

Default when key absent: {enabled: true, mode: "strict"}.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

TOOL_NAME = "cobalt_firewall"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Manage the cobalt irreversibility firewall. "
        "Actions: status (show enabled+mode), set (change mode: strict|warn), "
        "enable (turn on), disable (turn off). "
        "Modes: strict=block any hit, warn=block only irreversible (data-loss, history-rewrite), "
        "off=allow everything. Changes persist across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "set", "enable", "disable"],
                "description": "Action to perform.",
            },
            "mode": {
                "type": "string",
                "enum": ["strict", "warn"],
                "description": "Firewall mode (required for 'set' action). 'off' is achieved via disable.",
            },
        },
        "required": ["action"],
    },
}

_CONFIG_KEY = "cobalt_firewall"
_DEFAULT_ENABLED = True
_DEFAULT_MODE = "strict"

# Module-level cache; invalidated when the tool writes the config.
_cache: Optional[Tuple[bool, str]] = None


def _config_path() -> Path:
    return Path.home() / ".hermes" / "config.yaml"


def load_firewall_config() -> Tuple[bool, str]:
    """Return (enabled: bool, mode: str) from ~/.hermes/config.yaml.

    Exception-safe: returns defaults on any error.
    Cached on first call; cache is invalidated when _save_firewall_config writes.
    """
    global _cache
    if _cache is not None:
        return _cache

    try:
        import yaml

        path = _config_path()
        if not path.exists():
            _cache = (_DEFAULT_ENABLED, _DEFAULT_MODE)
            return _cache

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        fw = data.get(_CONFIG_KEY)
        if not isinstance(fw, dict):
            _cache = (_DEFAULT_ENABLED, _DEFAULT_MODE)
            return _cache

        enabled = bool(fw.get("enabled", _DEFAULT_ENABLED))
        mode = fw.get("mode", _DEFAULT_MODE)
        if mode not in ("strict", "warn", "off"):
            mode = _DEFAULT_MODE

        _cache = (enabled, mode)
        return _cache

    except Exception as exc:
        logger.debug("cobalt-firewall: cannot read config (%s) — using defaults", exc)
        return (_DEFAULT_ENABLED, _DEFAULT_MODE)


def _save_firewall_config(enabled: bool, mode: str) -> bool:
    """Write firewall config back to ~/.hermes/config.yaml.

    Returns True on success. Invalidates cache on write.
    """
    global _cache
    try:
        import yaml

        path = _config_path()
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            data = {}

        data[_CONFIG_KEY] = {"enabled": enabled, "mode": mode}
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        _cache = None  # invalidate
        return True
    except Exception as exc:
        logger.error("cobalt-firewall: failed to persist config: %s", exc)
        return False


def handle_firewall(args: Dict[str, Any], **kw) -> str:
    """Handle cobalt_firewall tool calls."""
    action = args.get("action", "status")
    enabled, mode = load_firewall_config()

    if action == "status":
        status_str = "enabled" if enabled else "disabled"
        effective_mode = mode if enabled else "off (disabled)"
        return (
            f"Cobalt firewall: {status_str}\n"
            f"Mode: {effective_mode}\n"
            f"Irreversible classes always blocked in warn/strict: data-loss, history-rewrite\n"
            f"To change: cobalt_firewall action='set' mode='warn'|'strict' or action='enable'|'disable'"
        )

    elif action == "set":
        new_mode = args.get("mode")
        if not new_mode:
            return "Error: 'mode' parameter required for set action. Values: strict, warn"
        if new_mode not in ("strict", "warn"):
            return f"Error: invalid mode '{new_mode}'. Valid modes: strict, warn. Use disable for off."
        if _save_firewall_config(enabled=True, mode=new_mode):
            return (
                f"Firewall mode set to '{new_mode}' and enabled.\n"
                f"  strict — blocks any firewall hit\n"
                f"  warn   — blocks only irreversible hits (data-loss, history-rewrite)"
            )
        return "Error: failed to persist firewall config. Check ~/.hermes/config.yaml permissions."

    elif action == "enable":
        if _save_firewall_config(enabled=True, mode=mode):
            return f"Firewall enabled (mode: {mode})."
        return "Error: failed to persist firewall config."

    elif action == "disable":
        if _save_firewall_config(enabled=False, mode=mode):
            return (
                "Firewall disabled. All commands will be allowed through the firewall. "
                "Re-enable with: cobalt_firewall action='enable'"
            )
        return "Error: failed to persist firewall config."

    return f"Unknown action: {action}"
