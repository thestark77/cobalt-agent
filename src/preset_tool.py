"""Cobalt Routing — Preset management tool for Hermes."""

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

TOOL_NAME = "cobalt_preset"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Manage cobalt-routing presets. Actions: list (show all presets), "
        "get (show active preset and its routing table), "
        "set (switch active preset). Changes persist across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "set"],
                "description": "Action to perform.",
            },
            "preset": {
                "type": "string",
                "enum": ["economy", "balanced", "quality"],
                "description": "Preset name (required for 'set' action).",
            },
        },
        "required": ["action"],
    },
}


def _persist_active_preset(name: str) -> bool:
    """Write active preset back to presets.yaml."""
    try:
        import yaml
        presets_path = Path(__file__).parent / "presets.yaml"
        data = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
        data["active"] = name
        presets_path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        logger.error("cobalt-routing: failed to persist preset: %s", e)
        return False


def handle_preset(args: Dict[str, Any], **kw) -> str:
    """Handle cobalt_preset tool calls."""
    from router import get_active_preset, set_active_preset, list_presets, _presets

    action = args.get("action", "get")

    if action == "list":
        presets = list_presets()
        active = get_active_preset()
        lines = ["Available presets:"]
        for name, desc in presets.items():
            marker = " (active)" if name == active else ""
            lines.append(f"  - {name}{marker}: {desc}")
        return "\n".join(lines)

    elif action == "get":
        active = get_active_preset()
        preset_data = _presets.get(active, {})
        routing = preset_data.get("routing", {})
        provider = preset_data.get("provider", "unknown")
        lines = [
            f"Active preset: {active}",
            f"Provider: {provider}",
            "Routing table:",
        ]
        for task_type, model in sorted(routing.items()):
            lines.append(f"  {task_type:12s} -> {model}")
        return "\n".join(lines)

    elif action == "set":
        target = args.get("preset")
        if not target:
            return "Error: 'preset' parameter required for set action."
        if set_active_preset(target):
            _persist_active_preset(target)
            return f"Preset switched to '{target}'. All new delegations will use this routing."
        else:
            available = ", ".join(list_presets().keys())
            return f"Error: preset '{target}' not found. Available: {available}"

    return f"Unknown action: {action}"
