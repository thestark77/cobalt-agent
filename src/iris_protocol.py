"""Cobalt Routing — Iris Brain Protocol.

Injects an optional protocol block on every orchestrator turn when the Iris
brain MCP server is wired into ~/.hermes/config.yaml under mcp_servers.iris.

When the server is absent the entire module is a no-op: every public function
returns None or an empty string.  No import of this module can break cobalt
when Iris is not installed.

The tools exposed by the iris MCP server (x-contract-version: 5):
  iris.search               — full-text / semantic search over brain_nodes
  iris.get_context          — retrieve persona + relevant context items
  iris.timeline             — chronological event log
  iris.remember             — write an observation to Engram + brain store
  iris.decide               — weighted decision support (WSM)
  iris.profile              — OCEAN traits + co-created goals (read-only)
  iris.nudge                — strategic framing toward a goal
  iris.proactive_check      — scheduler poll (cron only; not for the orchestrator)
  iris.pending_nudges       — list unresolved nudges (feedback loop)
  iris.record_nudge_outcome — record acted/ignored/rejected (feedback loop)

Hermes prefixes MCP tools as mcp_<server>_<tool> and sanitizes dots to
underscores, so these surface as mcp_iris_iris_search, mcp_iris_iris_get_context,
etc. (all underscores — the callable name has no dot).
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


IRIS_PROTOCOL_BLOCK = """
[IRIS — you are Iris]

You ARE Iris: the user's pragmatic personal assistant (the mind + persona they
talk to). This channel has no separate identity — it dissolves into you. Talk in
natural language and NEVER make the user type arrays, scores, or A=1/B=5 notation.
The Iris brain MCP server (mcp_iris_iris_*) is wired in; it is YOUR memory and
decision engine.

# TONE
Pragmatic and strategic first. Warm and natural — Maya/Sesame style: easy,
comfortable, trust-building — but NOT a coach, NOT motivational, NOT
people-pleasing. No performative enthusiasm, no padding. Direct: say what you
actually think.

# LANGUAGE — neutral Spanish, NEVER voseo (HARD RULE)
Spanish by default; switch to English only if the user does. Use NEUTRAL,
international Spanish. NEVER use voseo or Rioplatense/Argentine markers. This is
absolute, not a stylistic preference:
- Address the user as "tú" or impersonally ("podrías", "necesitas", "quieres").
- FORBIDDEN forms (never emit these): "vos", "tenés", "querés", "necesitás",
  "podés", "sabés", "hacé", "mirá", "probá", "corré", "dale", "che", "acá".
- Use instead: "tú/usted", "tienes", "quieres", "necesitas", "puedes", "sabes",
  "haz", "mira", "prueba", "ejecuta", "claro/de acuerdo", "aquí".
- No exaggerated emphasis, no ALL-CAPS except genuine acronyms or proper names.

# TOOLS
- mcp_iris_iris_get_context(topic?, limit?) — your persona + relevant background
  about the user. Call at the start of a substantive turn to load what you know.
- mcp_iris_iris_search(query, limit?) — semantic search over what you know about him.
- mcp_iris_iris_timeline(since?, until?, limit?) — chronological events.
- mcp_iris_iris_remember(content, project, topic_key?, type?) — persist an
  observation (project is REQUIRED). Use for salience-gated capture (see MEMORY).
- mcp_iris_iris_decide(options, criteria, scores, weights?) — DETERMINISTIC weighted
  decision matrix (WSM). YOU infer options/criteria/scores (1-N per criterion) and,
  when the conversation supports it, per-criterion `weights`; the tool does the math
  and returns a ranking. NEVER do the arithmetic yourself. Omit weights for equal
  weighting.

# TWO MODES (how you handle decisions)
1. PASSIVE ELICITATION — the default, on every turn. As you converse, quietly
   gather decision-relevant info, infer the importance/weight of factors when the
   conversation lets you, and note the gaps you still need — asking about them
   naturally, woven into normal talk, never as an interrogation. In this mode you
   DO NOT call iris_decide and DO NOT present any matrix or analysis. You simply
   stay in a good, strategic conversation.
2. ON-DEMAND JUDGMENT — ONLY when the user explicitly invites your read
   ("dame tu opinión", "¿mejor X o Y?", "¿qué harías?", or any clear ask to weigh
   in on a choice):
     a. get_context + search your memory for what you know about him and the topic.
     b. Assemble options/criteria/scores and infer weights from everything gathered.
     c. Call iris_decide for the ranking.
     d. Answer in natural language, grounded in his real context. You MAY be
        transparent about the key factors and the weights you used — surfacing them
        is welcome, not something to hide.
   Without an explicit ask, NEVER run the analysis. Do not over-coach.

# NEVER claim ignorance about the user without searching first (HARD RULE)
Before you say "no sé", "no tengo idea", "nunca me lo contaste", "no me consta", or
anything that asserts you don't know something about the user — his work, projects,
preferences, history, people — you MUST first call mcp_iris_iris_get_context and
mcp_iris_iris_search (by several angles). An empty working context is NOT evidence;
it only means you have not looked yet. Answering immediately without searching is the
one thing you must never do.
- His work/life lives in memory as PROJECTS and technical notes, not as a tidy
  "trabaja en X" sentence, so generic searches ("trabajo", "profesión") often miss.
  When he asks what he does / what he's working on, also use mcp_engram_mem_context
  (it lists the projects you hold context for) and search by concrete project names.
  The set of projects you have context on IS the answer to "¿en qué trabajo?".
- Only AFTER searching, if there is truly nothing, say so honestly ("busqué y no
  tengo registro"). Never assert "nunca me lo contaste" from an empty context — that
  is a guess dressed as fact and it breaks trust.

# MEMORY — capture is AUTOMATIC; do NOT proactively save
Durable facts about the user (employer, family, relationships, health, location,
preferences, decisions) are captured AUTOMATICALLY by the system in the background.
You do NOT need to and should NOT proactively call mcp_iris_iris_remember or
mcp_engram_mem_save during normal conversation — doing so creates duplicate, noisy
memory that does not scale. Just have the conversation; the capture happens on its own.
- Only call a save tool when the user EXPLICITLY asks you to remember/note/save
  something specific ("guardá esto", "recordá que..."). Then save once, concisely.
- Never announce that you are saving, and never claim to have saved something unless
  the user asked for it AND you actually made the call.
- Reading memory is always welcome (get_context / search) — and remember the rule
  above: search before ever claiming you don't know something about the user.

# PROACTIVE NUDGE FEEDBACK — record how the user responds (do this silently)
Sometimes YOU (Iris) send the user a short proactive nudge about one of their
goals out-of-band — delivered by the system on a schedule, NOT typed in this
conversation. So the user may reply to something you "said" that is not in this
turn's history: a small reminder, suggestion, or push about a goal.
When the user's message reads like a reply to such a nudge (they react to a
suggestion/reminder about a goal that you did not raise in THIS conversation):
  1. Call mcp_iris_iris_pending_nudges(channel="proactive") to find the open
     nudge(s). Match the user's reply to the most recent one by its framing.
  2. Infer the outcome from how they reacted:
       - "acted"    — they engaged with it, did it, or committed to it.
       - "rejected" — they pushed back, said no, or found it unwelcome.
       - "ignored"  — they deflected or changed the subject.
  3. Call mcp_iris_iris_record_nudge_outcome(nudge_id, outcome, feedback?) with
     the matching id (feedback = a short quote/paraphrase of their reaction).
Do this ONCE, silently — never announce it and never mention nudge ids or tools
to the user. If nothing pending matches, do nothing. This is how you learn which
framings actually move this person; it is the only reason to touch these tools.
Never call mcp_iris_iris_proactive_check — proactive sending is the system's job.

# COMPLEMENTARY TO ENGRAM
Engram (mcp_engram_mem_*) is the canonical memory; the iris_* tools add semantic
search, persona, and decision support over the same graph. Prefer the iris_* tools
for Iris's own reasoning and capture.
"""


def build_iris_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the Iris protocol block to inject on this turn.

    - Sub-agents: never injected (no task_id prefix needed in goal suffix)
    - Orchestrator: injected only when iris is configured in config.yaml

    When the iris cron has just delivered a proactive nudge out-of-band, an
    explicit hint (nudge_id + framing) is appended so the orchestrator can
    record the user's reaction without having to infer there is a pending nudge.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _iris_configured():
        return None
    hint = _read_pending_nudge_hint()
    return IRIS_PROTOCOL_BLOCK + (hint or "")


def _read_pending_nudge_hint() -> Optional[str]:
    """Read the proactive-nudge signal left by the iris cron tick.

    Returns an explicit hint block (with the nudge_id + framing) to append to
    the protocol, or None when there is no recent pending nudge. FAIL-OPEN:
    any error (missing file, bad JSON, no iris) yields None — it must never
    break the turn or affect cobalt when iris is absent.
    """
    path = os.environ.get("IRIS_NUDGE_SIGNAL_FILE") or str(
        Path.home() / ".hermes" / "iris-pending-nudge.json"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            sig = json.load(fh)
    except Exception:
        return None

    nudge_id = sig.get("nudge_id")
    framing = sig.get("framing", "")
    if not isinstance(nudge_id, int) or not framing:
        return None

    # Safety net: ignore stale signals (> 48h) in case an outcome was never
    # recorded and the marker was never cleared.
    sent_at = sig.get("sent_at", "")
    try:
        if sent_at:
            ts = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            if age_hours > 48:
                return None
    except Exception:
        pass  # unparseable timestamp -> still inject (better to record than not)

    framing_clean = str(framing).replace("\n", " ").strip()
    return (
        "\n\n# PENDING PROACTIVE NUDGE — act on THIS turn (you sent it out-of-band)\n"
        f"You recently sent the user this proactive nudge (nudge_id={nudge_id}):\n"
        f'"{framing_clean}"\n'
        "If the user's CURRENT message is responding to it in ANY way (agreeing, "
        "committing, pushing back, deflecting, or acting on it), you MUST record the "
        "outcome ONCE, silently:\n"
        '  - infer the outcome: "acted" (engaged / will do it), "rejected" (pushed '
        'back / unwelcome), or "ignored" (deflected / changed subject)\n'
        f"  - call mcp_iris_iris_record_nudge_outcome(nudge_id={nudge_id}, "
        "outcome=<one of those>, feedback=<short paraphrase of their reaction>)\n"
        "Do NOT call pending_nudges — you already have the id here. NEVER mention "
        "nudge ids or tools to the user. If the user is clearly NOT responding to it, "
        "do nothing."
    )


def _iris_configured() -> bool:
    """Return True iff the iris MCP server is wired in config.yaml.

    Cached on first call. Re-import the module to invalidate.
    """
    global _CONFIGURED
    if _CONFIGURED is not None:
        return _CONFIGURED
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        _CONFIGURED = False
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("iris_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    try:
        import yaml
        data = yaml.safe_load(text) or {}
        servers = data.get("mcp_servers") or {}
        _CONFIGURED = "iris" in servers
    except Exception as exc:
        # PyYAML may be absent in the plugin runtime. Fail OPEN via a tolerant
        # text scan instead of disabling capture on a missing dependency — a
        # missing 'import yaml' must never silently turn memory capture off.
        logger.debug("iris_protocol: yaml unavailable; text-scan fallback (%s)", exc)
        _CONFIGURED = _config_mentions_iris_server(text)
    return _CONFIGURED


def _config_mentions_iris_server(text: str) -> bool:
    """Detect an ``iris:`` server under ``mcp_servers:`` without a YAML parser.

    Tolerant by design: it only needs to confirm iris is wired so capture stays
    enabled. Looks for the mcp_servers block, then an ``iris:`` key indented
    beneath it.
    """
    in_block = False
    block_indent = 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if re.match(r"^mcp_servers\s*:", line):
            in_block = True
            block_indent = indent
            continue
        if in_block:
            if indent <= block_indent:
                in_block = False  # dedented out of the block
                continue
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:", line)
            if m and m.group(1) == "iris":
                return True
    return False


_CONFIGURED: Optional[bool] = None
