"""Cobalt Routing — Incognito mode (ADR-0014, iris-ai).

"Read yes, write no": while incognito is active the agent may READ existing
memory/profile to answer well, but NOTHING about the turn is persisted or
learned — no Engram save, no passive capture, no profiling, no accounting.

Two modes, both deterministic (do not depend on the model complying):
  - /incognito  → sticky session toggle (on until off), with a safety TTL that
                  auto-offs after inactivity so it never stays on by accident.
  - /secret     → one-shot: applies ONLY to the message that carries it. Text,
                  images, files, audio in that message are processed by the LLM
                  and then nothing persists. Turn-scoped, not sticky.

Enforcement lives entirely in cobalt-routing (iris stays untouched — it simply
receives no data on incognito turns):
  - pre_llm_call: detects the commands, flips session state, disables passive
    capture + the memory-save protocol, and injects an explicit directive.
  - pre_tool_call: blocks the write/persistence tools (WRITE_TOOLS) for the turn.

Honest boundary: the guarantee covers the AGENT KNOWLEDGE layers (Engram, iris
capture/profiling, Firefly accounting). Hermes-core's transient conversation
buffer and the DocumentHandler temp files are runtime-owned, not the plugin's —
they are ephemeral, not long-term memory, but outside cobalt's control.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Tools that WRITE/persist. Blocked at pre_tool_call while a turn is incognito.
# Reads (mem_search, get_observation, iris.search/get_context, …) are allowed.
WRITE_TOOLS = frozenset({
    # Engram persistence writers
    "mcp_engram_mem_save",
    "mcp_engram_mem_save_prompt",
    "mcp_engram_mem_session_summary",
    "mcp_engram_mem_update",
    "mcp_engram_mem_delete",
    "mcp_engram_mem_capture_passive",
    "mcp_engram_mem_session_start",
    "mcp_engram_mem_session_end",
    "mcp_engram_mem_merge_projects",
    "mcp_engram_mem_judge",
    # iris writers
    "mcp_iris_iris_remember",
    "mcp_iris_iris_decide",
    "mcp_iris_iris_record_nudge_outcome",
    "mcp_iris_iris_srs_create_card",
    "mcp_iris_iris_srs_review_card",
    "mcp_iris_iris_ingest_document",  # document ingest (Phase 5); find is read-only → NOT listed
    # Hermes built-in local notes
    "memory",
    # Firefly III writers (Phase 1) — create/update/delete must be blocked in
    # incognito so a private turn never mutates the finance ledger.
    "mcp_firefly_store_transaction",
    "mcp_firefly_update_transaction",
    "mcp_firefly_delete_transaction",
    "mcp_firefly_delete_transaction_journal",
    "mcp_firefly_store_account",
    "mcp_firefly_update_account",
    "mcp_firefly_delete_account",
    "mcp_firefly_store_bill",
    "mcp_firefly_update_bill",
    "mcp_firefly_delete_bill",
    "mcp_firefly_store_category",
    "mcp_firefly_update_category",
    "mcp_firefly_delete_category",
    "mcp_firefly_store_tag",
    "mcp_firefly_update_tag",
    "mcp_firefly_delete_tag",
    # Karakeep writers (Phase 2) — a private turn must not create/modify bookmarks.
    "mcp_karakeep_create_bookmark",
    "mcp_karakeep_update_bookmark",
    "mcp_karakeep_create_list",
    "mcp_karakeep_add_bookmark_to_list",
    "mcp_karakeep_remove_bookmark_from_list",
    "mcp_karakeep_attach_tag_to_bookmark",
    "mcp_karakeep_detach_tag_from_bookmark",
    # Ghostfolio writers (Phase 4) — a private turn must not mutate the portfolio.
    "mcp_ghostfolio_create_account",
    "mcp_ghostfolio_delete_account",
    "mcp_ghostfolio_upsert_asset_profile",
    "mcp_ghostfolio_delete_asset_profile",
    "mcp_ghostfolio_import_transactions",
    "mcp_ghostfolio_add_market_data_points",
    "mcp_ghostfolio_create_activity",
    "mcp_ghostfolio_delete_activity",
})

# Safety TTL: a sticky session auto-offs after this many seconds of inactivity,
# so incognito never stays on silently. Override for tests.
DEFAULT_TTL_SECONDS = 2 * 60 * 60  # 2 hours

# Command detection. Slash-commands are primary; a couple of natural-language
# phrases are accepted too. Matched on a word boundary so "/secretariat" etc.
# do not trigger.
# Require the slash command at start-of-string or after whitespace, so a URL
# path like https://x.com/secret or a word like "/secretariat" never triggers.
_INCOGNITO_CMD_RE = re.compile(r"(?:^|(?<=\s))/incognito(?:\s+(on|off|toggle|status))?\b", re.IGNORECASE)
_SECRET_CMD_RE = re.compile(r"(?:^|(?<=\s))/secret\b", re.IGNORECASE)
_NL_ON_RE = re.compile(r"\b(activar|activá|activa|prender|prendé|prende)\s+(el\s+)?modo\s+inc[oó]gnito\b", re.IGNORECASE)
_NL_OFF_RE = re.compile(r"\b(desactivar|desactivá|desactiva|apagar|apagá|apaga)\s+(el\s+)?modo\s+inc[oó]gnito\b", re.IGNORECASE)


def _state_path() -> Path:
    override = os.environ.get("COBALT_INCOGNITO_FILE")
    if override:
        return Path(override)
    return Path.home() / ".hermes" / "cobalt-incognito.json"


def _secret_marker_path() -> Path:
    """Marker for a one-shot /secret turn. Unlike the sticky session, /secret
    is not persisted as state — but it must cross the orchestrator→sub-agent
    PROCESS boundary (sub-agents run in their own process and cannot see the
    in-memory _TURN_INCOGNITO flag). So a /secret turn drops this file marker;
    it is cleared at the start of the next non-secret orchestrator turn."""
    p = _state_path()
    return p.with_name(p.stem + "-secret.json")


def set_secret_marker() -> None:
    try:
        path = _secret_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"since": _now().isoformat()}), encoding="utf-8")
    except Exception as exc:
        logger.debug("cobalt-incognito: cannot set secret marker (%s)", exc)


def clear_secret_marker() -> None:
    try:
        _secret_marker_path().unlink()
    except Exception:
        pass


def _secret_marker_present() -> bool:
    return _secret_marker_path().exists()


def _armed_marker_path() -> Path:
    """Marker meaning 'the NEXT message is one-shot secret'. Set by the
    `/secret` slash command (which is handled by the gateway and never reaches
    the LLM, so it cannot make its OWN message incognito). Consumed by the next
    evaluate_turn()."""
    p = _state_path()
    return p.with_name(p.stem + "-armed.json")


def arm_secret() -> None:
    try:
        path = _armed_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"since": _now().isoformat()}), encoding="utf-8")
    except Exception as exc:
        logger.debug("cobalt-incognito: cannot arm secret (%s)", exc)


def is_armed() -> bool:
    return _armed_marker_path().exists()


def clear_armed() -> None:
    try:
        _armed_marker_path().unlink()
    except Exception:
        pass


def _ttl_seconds() -> int:
    raw = os.environ.get("COBALT_INCOGNITO_TTL_SECONDS")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_TTL_SECONDS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(active: bool, since: Optional[str] = None) -> None:
    path = _state_path()
    now_iso = _now().isoformat()
    payload = {
        "active": bool(active),
        "since": since or now_iso,
        "last_activity": now_iso,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # fail-open: never break a turn over state I/O
        logger.debug("cobalt-incognito: cannot persist state (%s)", exc)


def _clear_state() -> None:
    try:
        _state_path().unlink()
    except Exception:
        pass


def is_session_active() -> bool:
    """True if the sticky session is on AND has not timed out.

    Applies the inactivity TTL: if the last activity is older than the TTL the
    session is treated as off and the state is cleared.
    """
    state = _load_state()
    if not state.get("active"):
        return False
    ttl = _ttl_seconds()
    if ttl > 0:
        last = state.get("last_activity")
        try:
            last_dt = datetime.fromisoformat(last) if last else None
        except (TypeError, ValueError):
            last_dt = None
        if last_dt is not None:
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (_now() - last_dt).total_seconds() > ttl:
                _clear_state()
                return False
    return True


def set_session(active: bool) -> None:
    """Turn the sticky session on or off, persisting the change."""
    if active:
        prev = _load_state()
        since = prev.get("since") if prev.get("active") else None
        _save_state(True, since=since)
    else:
        _clear_state()


def _touch_activity() -> None:
    """Refresh last_activity so an active session does not time out mid-use."""
    state = _load_state()
    if state.get("active"):
        _save_state(True, since=state.get("since"))


def parse_commands(user_message: str) -> dict:
    """Extract incognito intent from a message.

    Returns: {"toggle": "on"|"off"|"toggle"|None, "secret": bool}.
    """
    msg = user_message or ""
    toggle: Optional[str] = None
    m = _INCOGNITO_CMD_RE.search(msg)
    if m:
        arg = (m.group(1) or "toggle").lower()
        toggle = None if arg == "status" else arg  # /incognito status changes nothing
    if toggle is None:
        if _NL_ON_RE.search(msg):
            toggle = "on"
        elif _NL_OFF_RE.search(msg):
            toggle = "off"
    return {"toggle": toggle, "secret": bool(_SECRET_CMD_RE.search(msg))}


def evaluate_turn(user_message: str) -> Tuple[bool, Optional[str]]:
    """Apply any toggle in the message and decide if THIS turn is incognito.

    Returns (turn_is_incognito, user_note). `user_note` is a short confirmation
    the agent should relay when the state changed (else None).
    """
    cmds = parse_commands(user_message)
    active = is_session_active()
    armed = is_armed()
    if armed:
        clear_armed()  # one-shot: consume the arm set by a prior /secret command
    note: Optional[str] = None

    if cmds["toggle"] == "on":
        set_session(True)
        active = True
        note = ("🕶️ Modo incógnito ACTIVADO (sticky). Nada de lo que hablemos se "
                "guardará en memoria, decisiones ni contabilidad hasta que lo apagues "
                "con /incognito off. Se apaga solo tras 2 h de inactividad.")
    elif cmds["toggle"] == "off":
        set_session(False)
        active = False
        note = "Modo incógnito desactivado. Vuelvo a recordar normalmente."
    elif cmds["toggle"] == "toggle":
        active = not active
        set_session(active)
        note = ("🕶️ Modo incógnito ACTIVADO (sticky)." if active
                else "Modo incógnito desactivado.")

    if active:
        _touch_activity()

    # This turn is one-shot secret if the message embeds /secret OR a prior
    # /secret command armed it. The marker crosses the process boundary to
    # sub-agents; it is cleared on any later non-secret, non-session turn.
    secret_this_turn = cmds["secret"] or armed
    if secret_this_turn:
        set_secret_marker()
    elif not active:
        clear_secret_marker()

    turn_incognito = active or secret_this_turn
    return turn_incognito, note


# ── Per-turn bridge (pre_llm_call sets it; pre_tool_call reads it) ───────────
# A module global mirrors the markitdown opt-out bridge: pre_tool_call has no
# view of the user message, so pre_llm_call records whether the current turn is
# incognito. Single-user reactive host → safe.
_TURN_INCOGNITO: bool = False


def set_turn_incognito(value: bool) -> None:
    global _TURN_INCOGNITO
    _TURN_INCOGNITO = bool(value)


def is_turn_incognito() -> bool:
    return _TURN_INCOGNITO


def is_incognito_effective() -> bool:
    """True if writes must be suppressed right now, by ANY signal:
    the in-process per-turn flag, the persistent sticky session, or the
    cross-process /secret marker. The last two are file-based so they hold in a
    sub-agent process where the in-memory flag is always False."""
    return _TURN_INCOGNITO or is_session_active() or _secret_marker_present()


def block_if_incognito(tool_name: str) -> Optional[dict]:
    """Return a block directive if incognito is in effect and `tool_name` is a
    write/persistence tool. None otherwise (read tools always pass)."""
    if tool_name not in WRITE_TOOLS:
        return None
    if not is_incognito_effective():
        return None
    return {
        "action": "block",
        "message": (
            f"[incognito] '{tool_name}' is blocked: this turn is incognito, so "
            f"nothing may be persisted (no memory, decisions, profiling or "
            f"accounting). Answer the user without saving anything. Reads are fine."
        ),
    }


INCOGNITO_DIRECTIVE = (
    "\n[INCÓGNITO — no persistir nada este turno]\n"
    "Este turno es incógnito: respondé usando lo que ya sabés, pero NO guardes "
    "nada — ni memoria (Engram), ni decisiones, ni perfil, ni contabilidad, ni los "
    "archivos/fotos/audio del mensaje. Las herramientas de escritura están "
    "bloqueadas a propósito. Solo procesá y respondé."
)

# Anti-rider appended to delegated sub-agent goals on incognito turns. Counters
# the standard "you MUST save to Engram" memory rider so the sub-agent is told
# the truth (the tool-call block is the hard guarantee; this fixes the
# instruction the sub-agent sees).
SUBAGENT_INCOGNITO_RIDER = (
    "\n\n[INCOGNITO — do NOT persist anything]\n"
    "This task is incognito. Do NOT call mem_save or any memory/decision/"
    "accounting write tool. Process and return your result; persist nothing. "
    "Any write attempt will be blocked."
)


def build_incognito_directive(turn_incognito: bool, note: Optional[str]) -> Optional[str]:
    """Context block injected on incognito turns (and the state-change note)."""
    if not turn_incognito and not note:
        return None
    parts = []
    if note:
        parts.append(f"[INCÓGNITO — avisá al usuario]: {note}")
    if turn_incognito:
        parts.append(INCOGNITO_DIRECTIVE)
    return "\n".join(parts) if parts else None


# ── Hermes tool: cobalt_incognito (status|on|off) ────────────────────────────
# Deterministic command detection (parse_commands) is the primary path; this
# tool lets the orchestrator report status or flip state explicitly too.

TOOL_NAME = "cobalt_incognito"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Manage incognito mode (read-yes / write-no). Actions: status, on, off. "
        "While ON, nothing about the conversation is persisted (no memory, "
        "decisions, profiling or accounting); reads still work. The sticky "
        "session auto-offs after 2h of inactivity. Note: a single message can be "
        "made incognito without toggling the session by including '/secret' in it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "on", "off"],
                "description": "status (report), on (start sticky), off (stop).",
            },
        },
        "required": ["action"],
    },
}


# ── Slash-command handlers (gateway: fn(raw_args) -> str) ────────────────────
# Registered via ctx.register_command so Hermes routes /incognito and /secret to
# these instead of rejecting them as unknown commands.

def handle_incognito_command(raw_args: str = "", **kw) -> str:
    """/incognito [on|off|status] — toggle the sticky session (bare = toggle)."""
    arg = (raw_args or "").strip().lower()
    if arg == "status":
        return handle_incognito({"action": "status"})
    if arg == "on":
        return handle_incognito({"action": "on"})
    if arg == "off":
        return handle_incognito({"action": "off"})
    # bare /incognito → toggle
    new_state = not is_session_active()
    return handle_incognito({"action": "on" if new_state else "off"})


def handle_secret_command(raw_args: str = "", **kw) -> str:
    """/secret — arm the NEXT message as a one-shot private message.

    A leading-slash command is handled by the gateway and never reaches the
    LLM, so it cannot make its OWN message incognito (and cannot see its
    attachments). Instead it arms the next message: whatever you send next
    (text + images + files + audio) is processed and answered, but nothing
    persists. For a one-shot WITHOUT this two-step, put /secret NOT at the start
    of the message (e.g. "mira esto /secret") — that reaches the agent directly."""
    arm_secret()
    extra = ""
    if (raw_args or "").strip():
        extra = (" (Nota: lo que escribiste después de /secret no lo procesé; "
                 "mandá el contenido privado en el próximo mensaje.)")
    return (
        "🔒 Listo: tu PRÓXIMO mensaje será privado — lo proceso y respondo, pero "
        "no guardo nada (ni texto ni archivos/fotos/audio). Mandalo ahora." + extra
    )


def handle_incognito(args: dict, **kw) -> str:
    action = (args or {}).get("action", "status")
    if action == "on":
        set_session(True)
        return ("🕶️ Incognito ON (sticky). Nothing will be persisted until you turn "
                "it off (cobalt_incognito off or /incognito off). Auto-offs after 2h idle.")
    if action == "off":
        set_session(False)
        return "Incognito OFF. Persistence resumed."
    # status
    state = _load_state()
    if is_session_active():
        return f"Incognito: ON (sticky). since={state.get('since')} last_activity={state.get('last_activity')}"
    return "Incognito: OFF. (Use /secret in a single message for a one-shot, or cobalt_incognito on.)"
