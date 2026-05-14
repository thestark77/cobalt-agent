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

# WHEN TO SEARCH (mandatory, BEFORE acting)
- User says: "remember", "recall", "what did we do", "recordar", "qué hicimos",
  "acordate", or references prior work → call `mem_context` first, then
  `mem_search` if not found in recent history, then `mem_get_observation`
  for full content.
- User's FIRST message of the session references a project, feature, or
  problem → call `mem_search` with keywords from the message BEFORE responding.
- Starting any non-trivial task that may have been worked on before → search.

# WHEN TO SAVE (mandatory, IMMEDIATELY after the event — do NOT batch)
Call `mem_save` after ANY of:
- Architecture, design, or workflow decision taken
- Bug fix completed (include root cause in content)
- Convention or pattern established (naming, structure, approach)
- Non-obvious discovery, gotcha, or edge case learned
- Tool or library choice made with tradeoffs documented
- Configuration / environment change applied
- User preference or constraint stated
- Feature implemented with non-obvious approach

`mem_save` format:
  title: "<verb> <what>"           (e.g. "Fixed N+1 query in UserList")
  type:  bugfix | decision | architecture | discovery | pattern | config | preference
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
- Unsure → call `mem_suggest_topic_key` first

# SESSION CLOSE (mandatory, before saying "done"/"listo"/"that's it")
Call `mem_session_summary` with:
  ## Goal
  ## Discoveries
  ## Accomplished
  ## Next Steps
  ## Relevant Files

Skipping this leaves the next session blind. NOT optional.

# AFTER DELEGATION (cobalt automatically prepends this to every sub-agent goal)
Sub-agents that make discoveries, decisions, or fix bugs MUST call
`mem_save` before returning. The orchestrator does NOT see sub-agent
context, so the sub-agent is responsible for persisting.

# AFTER COMPACTION (recovery)
On compaction message:
1. `mem_session_summary` with the compaction content — persists pre-compact work
2. `mem_context` — recover prior session history
3. Only then resume work
"""

_SUBAGENT_MEMORY_RIDER = (
    "\n\n[MEMORY — sub-agent rule]\n"
    "If you make a decision, fix a bug, or learn something non-obvious during "
    "this task, call `mem_save` BEFORE returning. The orchestrator does not "
    "see your context — save it yourself or it is lost."
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
    "mem_save",
    "mem_search",
    "mem_get_observation",
    "mem_context",
    "mem_session_summary",
    "mem_save_prompt",
    "mem_suggest_topic_key",
    "mem_current_project",
    "mem_update",
})
