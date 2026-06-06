"""Cobalt Routing — Calendar domain protocol (Phase 4, iris-ai ADR-0013/0002).

Injects, on every orchestrator turn (only when a Google Calendar MCP is wired),
the rules that make the agent use the calendar organ correctly. Behavioral glue —
it does NOT hardcode tool names; it tells the orchestrator HOW to behave. Wiring
(allowlist, exact tool names) is added once the MCP is deployed.

Decisions encoded (roadmap grilling — Fase 4 calendario):
- The agent runs as its OWN dedicated Google account. The user SHARES their
  calendars to it: personal (read, full details) and work (read, free/busy only —
  the org blocks detail sharing). The agent also owns its OWN calendar.
- WRITE pattern: the agent creates/edits events on its OWN calendar only — the
  user's personal/work calendars are READ-ONLY shares, never written to. This is
  the safe, reversible design (the agent can never break the user's real entries).
- Respect the work free/busy blocks when proposing times, even without details.
- Calendar is the organ; iris owns the reasoning (what to schedule/remind and why,
  tied to goals, SRS-due cards, finance dates).

Silently disabled when no calendar MCP is configured.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


CALENDAR_PROTOCOL_BLOCK = """
[CALENDAR PROTOCOL — Google Calendar is wired]

A Google Calendar MCP is available, authenticated as the agent's OWN dedicated
Google account. It is the SOURCE OF TRUTH for schedule/events. iris owns the
reasoning (what to schedule or remind, and why).

# WHAT THE AGENT CAN SEE
- The user's PERSONAL calendar: shared read-only WITH details — read freely.
- The user's WORK calendar: shared read-only as FREE/BUSY only (the org blocks
  details). Treat busy blocks as "occupied" without assuming what they are.
- The agent's OWN calendar: full read/write.

# WRITE PATTERN (critical — never break the user's real calendars)
- Create/edit/delete events ONLY on the agent's OWN calendar. The user's personal
  and work calendars are READ-ONLY shares — do NOT attempt to write to them.
- Before creating an event, confirm the details (title, date/time, duration) with
  the user. Never fabricate or silently move events.

# SCHEDULING & REMINDERS
- When proposing a time, AVOID the user's busy blocks (personal details + work
  free/busy). Surface conflicts explicitly.
- Use the calendar for reminders, birthdays, deadlines, and follow-ups — and tie
  them to context iris owns: goals, SRS cards due, finance dates (e.g. a bill).
- Timezone: America/Bogota unless the user says otherwise.
"""


def build_calendar_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the calendar protocol block for this turn.

    - Sub-agents: never injected (they get domain rules via their goal).
    - Orchestrator: injected only when a calendar MCP is configured.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _calendar_configured():
        return None
    return CALENDAR_PROTOCOL_BLOCK


def _calendar_configured() -> bool:
    """True iff a calendar MCP server is wired in ~/.hermes/config.yaml.

    Accepts either 'calendar' or 'google-calendar' as the server name. Cached on
    first call; re-import the module to invalidate.
    """
    global _CONFIGURED
    if _CONFIGURED is not None:
        return _CONFIGURED
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        _CONFIGURED = False
        return False
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("calendar_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    servers = (data.get("mcp_servers") or {})
    _CONFIGURED = "calendar" in servers or "google-calendar" in servers
    return _CONFIGURED


_CONFIGURED: Optional[bool] = None
