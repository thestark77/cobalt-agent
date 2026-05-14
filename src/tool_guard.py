"""Cobalt Tool Guard - Enforces orchestrator delegation rules mechanically.

Blocks forbidden tools at the orchestrator level (depth 0) by returning
a block directive from the pre_tool_call hook. Sub-agents are identified
by their task_id prefix ("sa-" in Hermes 0.12.x) and are unrestricted.

This ensures the orchestrator MUST delegate, regardless of model compliance.
"""

import logging

logger = logging.getLogger(__name__)

# Tools the orchestrator is ALLOWED to call directly
ORCHESTRATOR_ALLOWED = frozenset({
    "delegate_task",
    "memory",
    "cobalt_preset",
    "clarify",
    "todo",
    "skills_list",
    "skill_view",
    "skill_manage",
    "send_message",
    "session_search",
    "cronjob",
    # Engram memory tools — orchestrator accesses memory directly
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

# Prefixes that identify sub-agent task_ids in Hermes.
# "sa-" is the primary format (Hermes 0.12.x): f"sa-{task_index}-{uuid}"
# "subagent-" is the fallback format if _subagent_id is not set.
_SUBAGENT_PREFIXES = ("sa-", "subagent-")

# Configurable: set to False to disable guard (debugging)
_guard_enabled = True


def is_subagent(task_id: str) -> bool:
    """Return True if this task_id belongs to a sub-agent (not the orchestrator)."""
    if not task_id:
        return False
    return task_id.startswith(_SUBAGENT_PREFIXES)


def check_tool_allowed(tool_name: str, task_id: str):
    """Check if tool_name is allowed for this agent depth.

    Returns:
        None if allowed (proceed normally)
        dict with action=block if forbidden
    """
    if not _guard_enabled:
        return None

    # Sub-agents can use ANY tool — no restrictions
    if is_subagent(task_id):
        return None

    # Orchestrator (root agent) — restricted to allowed set
    if tool_name in ORCHESTRATOR_ALLOWED:
        return None

    logger.warning(
        "cobalt-guard: BLOCKED %s at orchestrator level (task_id=%s). Must delegate.",
        tool_name, task_id[:20],
    )

    allowed_list = ", ".join(sorted(ORCHESTRATOR_ALLOWED))
    return {
        "action": "block",
        "message": (
            f"BLOCKED: '{tool_name}' cannot be called directly by the orchestrator. "
            f"You must delegate this work using delegate_task with an appropriate task_type. "
            f"Your allowed tools: {allowed_list}."
        ),
    }
