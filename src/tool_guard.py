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
    # NOTE: Hermes's built-in `memory` tool is intentionally NOT allowed here.
    # It writes to a capped local notes file (~/.hermes/MEMORY.md /
    # ~/.hermes/USER.md) that does NOT cross sessions or machines. All
    # persistent memory must flow through Engram via the `mcp_engram_mem_*`
    # tools listed below, which sync to Engram Cloud automatically.
    "cobalt_preset",
    "cobalt_firewall",
    "cobalt_incognito",
    "finance_reconcile",
    # Firefly III read tools (Phase 1) — orchestrator reads finance state
    # directly (lists/gets/search/summary). Writes are NOT here: the model must
    # be deliberate about mutating the ledger, and incognito blocks them.
    "mcp_firefly_list_account",
    "mcp_firefly_get_account",
    "mcp_firefly_list_transaction",
    "mcp_firefly_get_transaction",
    "mcp_firefly_list_transaction_by_account",
    "mcp_firefly_list_transaction_by_category",
    "mcp_firefly_list_transaction_by_tag",
    "mcp_firefly_list_transaction_by_bill",
    "mcp_firefly_get_transaction_by_journal",
    "mcp_firefly_list_links_by_journal",
    "mcp_firefly_list_bill",
    "mcp_firefly_get_bill",
    "mcp_firefly_list_category",
    "mcp_firefly_get_category",
    "mcp_firefly_list_tag",
    "mcp_firefly_get_tag",
    "mcp_firefly_list_piggy_bank_by_account",
    "mcp_firefly_list_event_by_transaction",
    "mcp_firefly_list_attachment_by_account",
    "mcp_firefly_list_attachment_by_bill",
    "mcp_firefly_list_attachment_by_category",
    "mcp_firefly_list_attachment_by_tag",
    "mcp_firefly_list_attachment_by_transaction",
    "mcp_firefly_list_rule_by_bill",
    "mcp_firefly_search_accounts",
    "mcp_firefly_search_transactions",
    "mcp_firefly_get_basic_summary",
    # Firefly III write tools — orchestrator logs expenses directly (no sub-agent
    # hop per entry). Still in incognito.WRITE_TOOLS, so a private turn is blocked
    # regardless of caller; the privacy guarantee is unchanged.
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
    # Karakeep references MCP (Phase 2) — reads + writes so the orchestrator
    # saves/searches bookmarks directly (no sub-agent hop). Hermes sanitizes the
    # hyphenated tool names to underscores (create-bookmark -> ...create_bookmark).
    # The 7 writers are also in incognito.WRITE_TOOLS, so a private turn blocks them.
    "mcp_karakeep_search_bookmarks",
    "mcp_karakeep_get_bookmark",
    "mcp_karakeep_get_bookmark_content",
    "mcp_karakeep_get_lists",
    "mcp_karakeep_create_bookmark",
    "mcp_karakeep_update_bookmark",
    "mcp_karakeep_create_list",
    "mcp_karakeep_add_bookmark_to_list",
    "mcp_karakeep_remove_bookmark_from_list",
    "mcp_karakeep_attach_tag_to_bookmark",
    "mcp_karakeep_detach_tag_from_bookmark",
    # iris SRS tools (Phase 3) — orchestrator manages review cards directly.
    # create/review are also in incognito.WRITE_TOOLS (they persist).
    "mcp_iris_iris_srs_create_card",
    "mcp_iris_iris_srs_due_cards",
    "mcp_iris_iris_srs_review_card",
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
    # All of these are CRUD-on-memory; the orchestrator must be allowed
    # to call them directly so it does not waste a sub-agent round trip
    # on a one-shot delete / stats / timeline lookup.
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
    # Iris brain MCP — knowledge graph, context retrieval, decision support.
    # Listed here so the orchestrator can query Iris directly without a round
    # trip through delegate_task. These are no-ops when the server is absent
    # (the tools simply will not exist in the namespace).
    # Hermes sanitizes dots in MCP tool names to underscores, so iris's
    # `iris.search` is exposed as `mcp_iris_iris_search` (all underscores).
    "mcp_iris_iris_search",
    "mcp_iris_iris_get_context",
    "mcp_iris_iris_timeline",
    "mcp_iris_iris_remember",
    "mcp_iris_iris_decide",
    # Phase 4 proactive-nudge feedback loop. The orchestrator reads pending
    # nudges and records how the user responded so Iris learns which framings
    # work. iris.proactive_check is deliberately NOT allowed here: planning and
    # delivering proactive nudges is owned by the deterministic cron tick, not
    # the LLM, so the scheduling gate (caps, quiet hours, Fogg window) cannot be
    # bypassed on demand.
    "mcp_iris_iris_pending_nudges",
    "mcp_iris_iris_record_nudge_outcome",
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
