"""Cobalt Routing — References domain protocol (Phase 2, iris-ai ADR-0013).

Injects, on every orchestrator turn (only when the Karakeep MCP is wired), the
rules that make the agent use the references organ correctly and compose it with
iris. Behavioral glue — it does NOT hardcode Karakeep tool names; it tells the
orchestrator HOW to behave. Wiring (allowlist, presets, exact tool names) is
added once the MCP is deployed.

Decisions encoded (from the roadmap grilling, Phase 2):
- Ownership: Karakeep is the source of truth for references (links, notes,
  YouTube, images, PDFs-as-references). iris owns reasoning/recall over them.
- Karakeep self-hosted and PRIVATE (no sharing).
- Tagging + summarization run SERVER-SIDE in Karakeep (OpenRouter/deepseek), NOT
  by the agent — the agent just saves the item; Karakeep crawls/tags/summarizes.
- Ingestion via Telegram: links/notes/videos/images/PDFs → save to Karakeep.
  Files that need text extraction → markitdown FIRST, then save.
- Recovery: iris queries Karakeep; references feed iris's RAG/reasoning.
- Proactive resurfacing is OFF at the start (deferred) — do not volunteer saved
  items unprompted yet.

Silently disabled when the Karakeep MCP is not configured.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


KARAKEEP_PROTOCOL_BLOCK = """
[REFERENCES PROTOCOL — Karakeep is wired]

The Karakeep references MCP is available. It is the SOURCE OF TRUTH for saved
references: links, articles, notes, YouTube videos, images, and PDFs kept as
references. It is self-hosted and PRIVATE. iris owns reasoning and recall OVER
these references — never duplicate reference bodies into iris memory; keep thin
pointers (title + Karakeep id) and let iris query Karakeep on demand.

# OWNERSHIP & PRECEDENCE
- A link/article/note/video/image/PDF the user wants to KEEP → save it in
  Karakeep. Do NOT store its content in Engram/iris memory.
- "What did I save about X?", "find that article/video" → search Karakeep.
- Reasoning, synthesis, connecting references to goals → iris (over Karakeep hits).

# INGESTION (via Telegram)
- A URL (article, repo, YouTube, tweet…) → save it to Karakeep. Karakeep crawls,
  archives, auto-TAGS and SUMMARIZES it server-side (OpenRouter) — you do NOT tag
  or summarize it yourself, and you do NOT fetch the page just to describe it.
- A plain note/idea the user wants to keep → save as a Karakeep note.
- A file (PDF/DOCX/image needing text) → convert with markitdown FIRST, then save
  the result/reference to Karakeep.
- Distinguish a REFERENCE (keep for later) from a one-off message: only persist
  to Karakeep what the user is saving/bookmarking, not every passing link.

# RECOVERY & COMPOSITION
- When recalling references, search Karakeep and let its results feed iris's
  reasoning (RAG). Cite the source title/link.
- Proactive resurfacing (volunteering a saved item unprompted) is OFF for now —
  only surface references when the user asks or it is clearly on-topic.
"""


def build_karakeep_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the references protocol block for this turn.

    - Sub-agents: never injected (they get domain rules via their goal).
    - Orchestrator: injected only when the Karakeep MCP is configured.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _karakeep_configured():
        return None
    return KARAKEEP_PROTOCOL_BLOCK


def _karakeep_configured() -> bool:
    """True iff the 'karakeep' MCP server is wired in ~/.hermes/config.yaml.

    Cached on first call; re-import the module to invalidate.
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
        logger.debug("karakeep_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    servers = (data.get("mcp_servers") or {})
    _CONFIGURED = "karakeep" in servers
    return _CONFIGURED


_CONFIGURED: Optional[bool] = None
