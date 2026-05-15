"""Cobalt Routing - Centralized configuration constants.

All constants that were previously scattered across modules live here.
This module MUST NOT import from any sibling module (only stdlib + typing)
to avoid circular imports.
"""

from typing import Dict, FrozenSet, List, Tuple


# ── Plugin metadata ─────────────────────────────────────────────────────────

PLUGIN_VERSION = "0.7.0"

# ── Timeout configuration ───────────────────────────────────────────────────

TIMEOUT_PER_TYPE: Dict[str, int] = {
    "scout": 300,
    "explore": 300,
    "summarize": 300,
    "apply": 600,
    "archive": 600,
    "design": 900,
    "spec": 900,
    "tasks": 900,
    "verify": 900,
    "propose": 900,
}

DEFAULT_TIMEOUT = 600

# ── Task type inference ─────────────────────────────────────────────────────

TASK_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "apply": [
        "implementa", "escribe", "crea el", "crea un", "crear", "modifica",
        "write", "implement", "create", "modify", "refactor", "fix",
        "genera", "generate", "develop", "build", "construye", "code", "programa",
    ],
    "verify": [
        "test", "tests", "prueba", "valida", "validate", "ejecuta el",
        "run the", "failing", "broken", "coverage", "pytest",
        "funciona correctamente", "verifica que", "verify",
        "ensure it works", "check that",
    ],
    "design": [
        "arquitectura", "architecture", "design the", "architect",
        "structure", "system design",
    ],
    "spec": [
        "requisitos", "requirements", "spec", "criteria",
        "acceptance", "given/when/then",
    ],
    "propose": [
        "propone", "evalua", "decide", "propose", "evaluate",
        "compare", "tradeoff", "alternative", "opcion",
    ],
    "explore": [
        "investiga", "analiza", "explica", "explain", "investigate",
        "analyze", "understand", "trace", "examine", "read the",
        "lee el", "estudia",
    ],
    "scout": [
        "busca en", "encuentra", "find", "search for", "locate",
        "scan", "discover", "busca informacion", "busca documentacion", "web",
    ],
    "summarize": [
        "resume", "resumen", "summarize", "summary",
        "condense", "overview", "synopsis",
    ],
}

TASK_TYPE_PRIORITY: List[str] = [
    "apply", "verify", "design", "spec", "propose",
    "explore", "scout", "summarize",
]

ROLE_TO_TASK_TYPE: Dict[str, str] = {
    "worker": "apply",
    "researcher": "explore",
    "reviewer": "verify",
    "planner": "design",
    "scout": "scout",
}

# ── Subagent identification ─────────────────────────────────────────────────

# Prefixes that identify sub-agent task_ids in Hermes.
# "sa-" is the primary format (Hermes 0.12.x): f"sa-{task_index}-{uuid}"
# "subagent-" is the fallback format if _subagent_id is not set.
SUBAGENT_PREFIXES: Tuple[str, ...] = ("sa-", "subagent-")


def is_subagent(task_id: str) -> bool:
    """Return True if this task_id belongs to a sub-agent (not the orchestrator)."""
    if not task_id:
        return False
    return task_id.startswith(SUBAGENT_PREFIXES)


# ── Tool guard ──────────────────────────────────────────────────────────────

# Tools the orchestrator is ALLOWED to call directly.
# NOTE: Hermes's built-in `memory` tool is intentionally NOT included here.
# It writes to a capped local notes file (~/.hermes/MEMORY.md /
# ~/.hermes/USER.md) that does NOT cross sessions or machines. All persistent
# memory must flow through Engram via the mcp_engram_mem_* tools.
ORCHESTRATOR_ALLOWED: FrozenSet[str] = frozenset({
    "delegate_task",
    "cobalt_preset",
    "clarify",
    "todo",
    "skills_list",
    "skill_view",
    "skill_manage",
    "send_message",
    "session_search",
    "cronjob",
    # Engram memory tools — orchestrator accesses memory directly.
    # Hermes prefixes MCP tools as mcp_<server>_<tool>, so the engram
    # server's `mem_save` becomes `mcp_engram_mem_save` when invoked.
    "mcp_engram_mem_save",
    "mcp_engram_mem_search",
    "mcp_engram_mem_get_observation",
    "mcp_engram_mem_context",
    "mcp_engram_mem_session_summary",
    "mcp_engram_mem_save_prompt",
    "mcp_engram_mem_suggest_topic_key",
    "mcp_engram_mem_current_project",
    "mcp_engram_mem_update",
    "mcp_engram_mem_delete",
    "mcp_engram_mem_stats",
    "mcp_engram_mem_timeline",
    "mcp_engram_mem_session_start",
    "mcp_engram_mem_session_end",
    "mcp_engram_mem_compare",
    "mcp_engram_mem_judge",
    "mcp_engram_mem_doctor",
    "mcp_engram_mem_capture_passive",
    "mcp_engram_mem_merge_projects",
    # markitdown MCP (Microsoft) — file conversion, cheap, no need to delegate.
    "mcp_markitdown_convert_to_markdown",
})

# Configurable: set to False to disable guard (debugging)
GUARD_ENABLED = True

# ── Context loader ──────────────────────────────────────────────────────────

MAX_CONTEXT_BYTES = 32 * 1024  # 32 KB safety cap — anything larger isn't context, it's noise.

# ── Version management ──────────────────────────────────────────────────────

VERSIONS_DIR_NAME = "context/appVersions"

# ── Curation / response format suffixes ─────────────────────────────────────

DISCARDED_SECTION = (
    "\n\n[DISCARDED INFO] After your main response, add a section titled "
    "'## Discarded' listing information you found but excluded (topic + 1-line reason "
    "why you excluded it). The orchestrator uses this to decide if a follow-up query is needed."
)

CURATION_SUFFIXES: Dict[str, str] = {
    "scout": (
        "\n\n[RESPONSE FORMAT] Return: key findings, relevant URLs/endpoints, "
        "and critical data points. No raw dumps. Max 400 words."
        + DISCARDED_SECTION
    ),
    "explore": (
        "\n\n[RESPONSE FORMAT] Return: summary of findings, key patterns, "
        "file locations, and relevant code signatures. No full file contents. Max 500 words."
        + DISCARDED_SECTION
    ),
    "summarize": (
        "\n\n[RESPONSE FORMAT] Return a structured summary: main points, "
        "decisions, and actionable items. Max 300 words."
        + DISCARDED_SECTION
    ),
    "verify": (
        "\n\n[RESPONSE FORMAT] Return: pass/fail status, failing test names, "
        "error messages (first 3 lines each), and suggested fixes. No full logs."
        + DISCARDED_SECTION
    ),
}

# ── Schema patching ─────────────────────────────────────────────────────────

TASK_TYPE_SCHEMA = {
    "type": "string",
    "enum": [
        "scout", "explore", "summarize",
        "apply", "archive",
        "design", "spec", "tasks", "verify", "propose",
    ],
    "description": (
        "REQUIRED on every delegation. Determines which model handles the task. "
        "scout/explore/summarize route to fast/cheap model. "
        "apply/archive route to mid-tier. "
        "design/spec/tasks/verify/propose route to reasoning model."
    ),
}

ROUTING_GUIDANCE = (
    "\n\n"
    "COBALT ROUTING ACTIVE:\n"
    "Each task_type routes to a cost-optimized model automatically. "
    "Always set task_type — it determines which model runs the child agent.\n"
    "If omitted, task_type is inferred from the goal, but explicit is better.\n\n"
    "task_type reference:\n"
    "- scout: search, list, find, discover, locate, scan\n"
    "- explore: read, investigate, analyze, understand, trace\n"
    "- summarize: condense, overview, synopsis\n"
    "- apply: write, implement, create, modify, refactor, fix\n"
    "- verify: test, validate, check, run tests, coverage\n"
    "- design: architecture, system design, structure\n"
    "- spec: requirements, criteria, acceptance\n"
    "- tasks: breakdown, planning, decompose\n"
    "- propose: evaluate, compare, tradeoffs, decide\n"
    "- archive: cleanup, wrap-up, document"
)

# ── Preset defaults ─────────────────────────────────────────────────────────

DEFAULT_PRESET = "economy"
