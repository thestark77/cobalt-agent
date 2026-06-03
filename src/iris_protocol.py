"""Cobalt Routing — Iris Brain Protocol.

Injects an optional protocol block on every orchestrator turn when the Iris
brain MCP server is wired into ~/.hermes/config.yaml under mcp_servers.iris.

When the server is absent the entire module is a no-op: every public function
returns None or an empty string.  No import of this module can break cobalt
when Iris is not installed.

The 5 tools exposed by the iris MCP server (x-contract-version: 2):
  iris.search       — full-text / semantic search over brain_nodes
  iris.get_context  — retrieve persona + relevant context items
  iris.timeline     — chronological event log
  iris.remember     — write an observation to Engram + brain store
  iris.decide       — weighted decision support (WSM)

Hermes prefixes MCP tools as mcp_<server>_<tool> and sanitizes dots to
underscores, so these surface as mcp_iris_iris_search, mcp_iris_iris_get_context,
etc. (all underscores — the callable name has no dot).
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


IRIS_PROTOCOL_BLOCK = """
[IRIS BRAIN — context & memory tools]

The Iris brain MCP server is wired into this Hermes install. It exposes 5
tools for semantic search, context retrieval, and decision support over the
project knowledge graph.

# TOOLS

mcp_iris_iris_search(query, limit?)
  Full-text / semantic search over brain_nodes. Use before diving into an
  unknown codebase area, before answering questions about past decisions, or
  when asked "what do we know about X".

mcp_iris_iris_get_context(topic?, limit?)
  Retrieve persona + contextually relevant brain_nodes for the current
  session. Call once at the start of a complex task to load relevant
  background before planning.

mcp_iris_iris_timeline(since?, until?, limit?)
  Chronological event log of observations. Use to reconstruct "what happened
  last week on X" or to audit a sequence of changes.

mcp_iris_iris_remember(content, topic_key?, type?)
  Write a new observation to the Iris brain store. Call whenever you make an
  architectural decision, find a non-obvious gotcha, or establish a convention
  — the same triggers as Engram mem_save.

mcp_iris_iris_decide(question, options, criteria?)
  Weighted-sum decision support (WSM). Use when you have 2+ alternatives and
  want a structured trade-off analysis before recommending a direction.

# WHEN TO USE

- Start of a task touching unfamiliar code → get_context, then search
- "What did we decide about X?" / "Do we have notes on Y?" → search
- Making an architectural or design decision → decide, then remember
- Completing a task with a non-obvious finding → remember
- Reviewing what changed in a time range → timeline

# COMPLEMENTARY TO ENGRAM

Iris and Engram are complementary: Engram (mcp_engram_mem_*) is the canonical
persistent-memory layer; Iris brain adds semantic search, persona context, and
structured decision support on top of the same knowledge graph. Both can be
used in the same session.
"""


def build_iris_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the Iris protocol block to inject on this turn.

    - Sub-agents: never injected (no task_id prefix needed in goal suffix)
    - Orchestrator: injected only when iris is configured in config.yaml
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _iris_configured():
        return None
    return IRIS_PROTOCOL_BLOCK


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
        import yaml
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("iris_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    servers = data.get("mcp_servers") or {}
    _CONFIGURED = "iris" in servers
    return _CONFIGURED


_CONFIGURED: Optional[bool] = None
