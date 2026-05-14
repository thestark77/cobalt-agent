"""Cobalt Routing — Engram Memory Protocol Injection.

Hooks into pre_llm_call (composed with sdd_triage) to inject a strict,
deterministic memory protocol on EVERY orchestrator turn.

The protocol is RULE-BASED, not LLM-decision-based: every save/search/
session-close trigger is enumerated. The orchestrator MUST follow them.
This complements (and will eventually be replaced by) a native Hermes
memory plugin that calls Engram on the prefetch/sync_turn lifecycle
mechanically.

Engram is self-hosted, MCP-native, and exposes its tools through
Hermes's MCP server config.
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


# ── Static protocol blocks ──────────────────────────────────────────────────

ENGRAM_PROTOCOL_BLOCK = """
[MANDATORY MEMORY PROTOCOL — Engram]

You have persistent memory via Engram. The protocol is ALWAYS ACTIVE,
not on-demand. Follow these rules without being asked.

# PROJECT SCOPING (mandatory — Engram is project-partitioned)

Engram stores observations under a project key and partitions search,
save, and delete by that key. If you do not pass `project="..."`, the
call defaults to whatever Engram detected from the MCP server's working
directory at startup (often "home" or the user's username, NOT the
project the user is talking about). You will save into one bucket and
search a different one, silently losing data.

Always pass `project` EXPLICITLY in these cases:
- User names a project ("for the X project", "para el proyecto X",
  "in libertanza", "en libertanza") → `project="x"` / `project="libertanza"`.
  Project names are case-insensitive and normalized to lowercase by Engram.
- You are inside a delegation rider that names a project.
- You see a previous tool result that recorded a `project` field and you
  are following up on the same data.

When the user does NOT name a project and you have no other signal,
call `mcp_engram_mem_current_project` once to find out which project
Engram resolved before searching or saving. Reuse that value across
the turn instead of relying on the implicit default.

NEVER assume "no project arg" means "all projects". It means "current
default project" — which is one specific bucket, not a wildcard.

# `session_search` vs Engram — DO NOT confuse the two stores

`session_search` reads your CONVERSATION HISTORY (transcripts of past
chat turns). Engram stores CURATED KNOWLEDGE (decisions, patterns,
bug fixes, preferences) explicitly saved via `mcp_engram_mem_save`.
They are different data sets, not redundant copies.

Pick the right one for the question:
- "What did we decide about X?" / "How did we fix N+1?" / "What's the
  convention here?" / "What do you know about my preferences?" →
  Engram (`mcp_engram_mem_search` / `mcp_engram_mem_context`).
- "What did you say two hours ago?" / "Show me the code you posted
  yesterday" / "Recover that snippet from the last session" →
  `session_search` is the right tool.

CRITICAL: when `mcp_engram_mem_search` returns 0 results, do NOT
auto-fall-back to `session_search`. Zero results almost always means
the `project=` filter is wrong (see PROJECT SCOPING above). Resolve
the project — call `mcp_engram_mem_current_project` if needed — and
re-run `mcp_engram_mem_search` before considering other tools.
`session_search` is slow (30-120 s) and answers a different question;
using it as a fallback wastes a turn on data that was never going to
be there.

# WHEN TO SEARCH (mandatory, BEFORE acting)
- User says: "remember", "recall", "what did we do", "recordar", "qué hicimos",
  "acordate", or references prior work → call `mcp_engram_mem_context` first, then
  `mcp_engram_mem_search` if not found in recent history, then `mcp_engram_mem_get_observation`
  for full content.
- User's FIRST message of the session references a project, feature, or
  problem → call `mcp_engram_mem_search` with keywords from the message BEFORE responding.
- Starting any non-trivial task that may have been worked on before → search.

# WHEN TO SAVE (mandatory, IMMEDIATELY after the event — do NOT batch)
Call `mcp_engram_mem_save` after ANY of:
- Architecture, design, or workflow decision taken
- Bug fix completed (include root cause in content)
- Convention or pattern established (naming, structure, approach)
- Non-obvious discovery, gotcha, or edge case learned
- Tool or library choice made with tradeoffs documented
- Configuration / environment change applied
- User preference or constraint stated
- Feature implemented with non-obvious approach

`mcp_engram_mem_save` format:
  title: "<verb> <what>"           (e.g. "Fixed N+1 query in UserList")
  type:  bugfix | decision | architecture | discovery | pattern | config | preference
  project: lowercase project name — REQUIRED whenever the user named a
           project; otherwise resolve via mcp_engram_mem_current_project.
  scope: project (default) | personal
  topic_key: stable identifier for evolving topics
             (e.g. "architecture/auth-model", "decision/db-choice",
              "sdd/<change-name>/<artifact-type>")
  content:
    What:     one sentence — what was done
    Why:      motivation (user request, bug, performance, etc.)
    Where:    files or paths affected
    Learned:  gotchas / surprises (omit if none)

Topic-key rules:
- Same topic evolving → reuse `topic_key` (upsert overwrites)
- Different topic → different `topic_key`
- Unsure → call `mcp_engram_mem_suggest_topic_key` first

# SESSION CLOSE (mandatory, before saying "done"/"listo"/"that's it")
Call `mcp_engram_mem_session_summary` with:
  ## Goal
  ## Discoveries
  ## Accomplished
  ## Next Steps
  ## Relevant Files

Skipping this leaves the next session blind. NOT optional.

# AFTER DELEGATION (cobalt automatically prepends this to every sub-agent goal)
Sub-agents that make discoveries, decisions, or fix bugs MUST call
`mcp_engram_mem_save` before returning. The orchestrator does NOT see sub-agent
context, so the sub-agent is responsible for persisting.

# AFTER COMPACTION (recovery)
On compaction message:
1. `mcp_engram_mem_session_summary` with the compaction content — persists pre-compact work
2. `mcp_engram_mem_context` — recover prior session history
3. Only then resume work
"""

_SUBAGENT_MEMORY_RIDER = (
    "\n\n[MEMORY — sub-agent rule, MANDATORY]\n"
    "Before you return, you MUST call `mcp_engram_mem_save` at least once if "
    "your task involved ANY of:\n"
    "  - Choosing a library, framework, pattern, or data structure\n"
    "  - Creating a new file, module, package, or directory\n"
    "  - Establishing a naming, layout, or coding convention\n"
    "  - Resolving an ambiguity in the goal (deciding how to interpret it)\n"
    "  - Fixing a bug, gotcha, or non-obvious edge case\n"
    "Saving is NOT optional and NOT the orchestrator's job — it is yours. "
    "The orchestrator does NOT see your tool calls, sub-agent context, or "
    "intermediate reasoning. If you do not save, the decision is lost forever.\n"
    "\n"
    "How to save (mandatory format):\n"
    "  - `project`: lowercase project name from the goal. If the goal names a "
    "project, pass it explicitly. Otherwise call `mcp_engram_mem_current_project` "
    "first to resolve it.\n"
    "  - `type`: `decision` (you chose between options) or `architecture` "
    "(you defined structure) or `pattern` / `bugfix` / `discovery` as appropriate.\n"
    "  - `title`: verb + what (e.g. \"Chose httpx over requests for async HTTP\").\n"
    "  - `content`: What / Why / Where / Learned, one short paragraph each.\n"
    "\n"
    "If you genuinely took no decisions (e.g. you ran a single read-only "
    "command and returned its output verbatim), no save is required. Otherwise "
    "the default is SAVE, not skip."
)


# ── Public surface ──────────────────────────────────────────────────────────


def build_memory_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the protocol block to inject on this turn.

    - Sub-agents: never injected (they get the rider via goal suffix elsewhere)
    - Orchestrator: always injected
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    return ENGRAM_PROTOCOL_BLOCK


def subagent_memory_rider() -> str:
    """Suffix to append to every sub-agent goal so they save discoveries."""
    return _SUBAGENT_MEMORY_RIDER


# ── Memory tools the orchestrator may call directly ────────────────────────

ENGRAM_ORCHESTRATOR_TOOLS = frozenset({
    "mcp_engram_mem_save",
    "mcp_engram_mem_search",
    "mcp_engram_mem_get_observation",
    "mcp_engram_mem_context",
    "mcp_engram_mem_session_summary",
    "mcp_engram_mem_save_prompt",
    "mcp_engram_mem_suggest_topic_key",
    "mcp_engram_mem_current_project",
    "mcp_engram_mem_update",
})
