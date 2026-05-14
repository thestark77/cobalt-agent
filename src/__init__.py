"""Cobalt Routing Plugin v0.7.0 - Model routing + Tool Guard + Skill Injection for Hermes Agent.

Five enforcement mechanisms via hooks:
1. TOOL GUARD: Blocks forbidden tools at orchestrator level (pre_tool_call)
2. MODEL ROUTING: Injects _routed_model into delegate_task (pre_tool_call)
3. SKILL INJECTION: Instructs sub-agents to load relevant skills (pre_tool_call)
4. SDD TRIAGE: Forces orchestrator to classify and select SDD phases (pre_llm_call)
5. DYNAMIC TIMEOUT: Sets per-task timeout via env var before each delegation (pre_tool_call)

Requires source patch in delegate_tool.py for _routed_model fields.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.7.0"

_plugin_dir = Path(__file__).parent
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

_registered = False

_TASK_TYPE_SCHEMA = {
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

_ROUTING_GUIDANCE = (
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

_schema_patched = False


def _patch_delegate_schema():
    """Patch delegate_task schema to include task_type. Idempotent."""
    global _schema_patched
    if _schema_patched:
        return True

    try:
        from tools.registry import registry
        entry = registry.get_entry("delegate_task")
        if entry is None:
            return False

        current_desc = entry.schema.get("description", "")
        if "COBALT ROUTING" not in current_desc:
            entry.schema["description"] = current_desc + _ROUTING_GUIDANCE

        props = entry.schema.get("parameters", {}).get("properties", {})
        if "task_type" not in props:
            props["task_type"] = _TASK_TYPE_SCHEMA

        params = entry.schema.get("parameters", {})
        required = params.get("required", [])
        if "task_type" not in required:
            required.append("task_type")
            params["required"] = required

        tasks_prop = props.get("tasks", {})
        items = tasks_prop.get("items", {})
        items_props = items.get("properties", {})
        if items_props and "task_type" not in items_props:
            items_props["task_type"] = _TASK_TYPE_SCHEMA

        _schema_patched = True
        logger.info("cobalt-routing: delegate_task schema patched successfully")
        return True
    except Exception as e:
        logger.debug("cobalt-routing: schema patch deferred: %s", e)
        return False


_DISCARDED_SECTION = (
    "\n\n[DISCARDED INFO] After your main response, add a section titled "
    "'## Discarded' listing information you found but excluded (topic + 1-line reason "
    "why you excluded it). The orchestrator uses this to decide if a follow-up query is needed."
)

_CURATION_SUFFIXES = {
    "scout": (
        "\n\n[RESPONSE FORMAT] Return: key findings, relevant URLs/endpoints, "
        "and critical data points. No raw dumps. Max 400 words."
        + _DISCARDED_SECTION
    ),
    "explore": (
        "\n\n[RESPONSE FORMAT] Return: summary of findings, key patterns, "
        "file locations, and relevant code signatures. No full file contents. Max 500 words."
        + _DISCARDED_SECTION
    ),
    "summarize": (
        "\n\n[RESPONSE FORMAT] Return a structured summary: main points, "
        "decisions, and actionable items. Max 300 words."
        + _DISCARDED_SECTION
    ),
    "verify": (
        "\n\n[RESPONSE FORMAT] Return: pass/fail status, failing test names, "
        "error messages (first 3 lines each), and suggested fixes. No full logs."
        + _DISCARDED_SECTION
    ),
}


def _ensure_skills_toolset(task_dict: dict) -> None:
    """Ensure 'skills' toolset is available when skill injection is active."""
    toolsets = task_dict.get("toolsets")
    if not toolsets:
        task_dict["toolsets"] = "skills"
        logger.info("cobalt-routing: set 'skills' toolset for skill_view access")
        return
    if isinstance(toolsets, list):
        if "skills" not in toolsets:
            toolsets.append("skills")
            logger.info("cobalt-routing: added 'skills' toolset (list mode)")
    elif isinstance(toolsets, str):
        if "skills" not in toolsets:
            task_dict["toolsets"] = toolsets + ",skills"
            logger.info("cobalt-routing: added 'skills' toolset (string mode)")


def _inject_routing(task_dict: dict, task_type: str) -> None:
    """Inject routing fields, curation, skills, memory + markitdown riders, timeout."""
    from router import resolve_routing, apply_dynamic_timeout
    from skill_injector import inject_skill_instruction
    from memory_protocol import subagent_memory_rider
    from markitdown_protocol import subagent_markitdown_rider

    apply_dynamic_timeout(task_type)

    routing = resolve_routing(task_type)
    if routing:
        task_dict["_routed_model"] = routing["model"]
        if "provider" in routing:
            task_dict["_routed_provider"] = routing["provider"]
        if "base_url" in routing:
            task_dict["_routed_base_url"] = routing["base_url"]
        if "api_key" in routing:
            task_dict["_routed_api_key"] = routing["api_key"]
        if "api_mode" in routing:
            task_dict["_routed_api_mode"] = routing["api_mode"]
        logger.info(
            "cobalt-routing: [%s] type=%s -> model=%s",
            task_dict.get("goal", "")[:50], task_type, routing["model"]
        )

    injected_skills = inject_skill_instruction(task_dict, task_type)

    if injected_skills:
        _ensure_skills_toolset(task_dict)

    suffix = _CURATION_SUFFIXES.get(task_type)
    if suffix:
        task_dict["goal"] = task_dict.get("goal", "") + suffix
        logger.info("cobalt-routing: curation suffix injected for task_type=%s", task_type)

    task_dict["goal"] = task_dict.get("goal", "") + subagent_memory_rider()
    task_dict["goal"] = task_dict.get("goal", "") + subagent_markitdown_rider()


def _pre_tool_call_hook(tool_name: str, args: dict, **kwargs):
    """Unified pre_tool_call hook: guard + routing + timeout.

    1. GUARD: If orchestrator tries a forbidden tool, return block directive.
    2. ROUTING: If delegate_task, inject _routed_model based on task_type.
    3. TIMEOUT: Set per-task timeout via env var.
    """
    task_id = kwargs.get("task_id", "")

    from tool_guard import check_tool_allowed
    block = check_tool_allowed(tool_name, task_id)
    if block is not None:
        return block

    if tool_name != "delegate_task":
        return None

    if args.get("_cobalt_routed"):
        return None
    args["_cobalt_routed"] = True

    _patch_delegate_schema()

    from router import _infer_task_type, resolve_task_type_from_role

    top_task_type = args.pop("task_type", None)
    tasks = args.get("tasks")

    if tasks and isinstance(tasks, list):
        for t in tasks:
            tt = t.pop("task_type", None) or top_task_type
            if not tt:
                role = t.get("role")
                tt = resolve_task_type_from_role(role, t.get("goal", ""))
            try:
                _inject_routing(t, tt)
            except Exception as e:
                logger.error("cobalt-routing: inject_routing failed for batch task: %s", e)
    elif args.get("goal"):
        role = args.get("role")
        effective_tt = top_task_type or resolve_task_type_from_role(role, args.get("goal", ""))
        saved_goal = args.get("goal")
        saved_ctx = args.get("context")
        saved_ts = args.get("toolsets")
        saved_role = args.get("role")
        try:
            task_entry = {
                "goal": args.pop("goal"),
            }
            ctx = args.pop("context", None)
            if ctx:
                task_entry["context"] = ctx
            ts = args.pop("toolsets", None)
            if ts:
                task_entry["toolsets"] = ts
            r = args.pop("role", None)
            if r:
                task_entry["role"] = r
            _inject_routing(task_entry, effective_tt)
            args["tasks"] = [task_entry]
        except Exception as e:
            logger.error("cobalt-routing: single->batch conversion failed: %s — restoring args", e)
            args["goal"] = saved_goal
            if saved_ctx:
                args["context"] = saved_ctx
            if saved_ts:
                args["toolsets"] = saved_ts
            if saved_role:
                args["role"] = saved_role

    args.pop("_cobalt_routed", None)
    return None


def _pre_llm_call_hook(
    user_message: str = "",
    task_id: str = "",
    conversation_history: list = None,
    **kwargs,
):
    """Composite pre_llm_call hook: SDD triage + Engram memory + markitdown.

    Sub-agents receive nothing (their context comes from the goal suffix).
    Orchestrator receives all blocks concatenated, every turn.
    """
    from sdd_triage import pre_llm_call_hook as triage_hook
    from memory_protocol import build_memory_protocol_block
    from markitdown_protocol import build_markitdown_protocol_block

    triage = triage_hook(
        user_message=user_message,
        task_id=task_id,
        conversation_history=conversation_history,
        **kwargs,
    )
    memory = build_memory_protocol_block(task_id=task_id)
    markdown = build_markitdown_protocol_block(task_id=task_id)

    triage_ctx = (triage or {}).get("context", "") if isinstance(triage, dict) else ""
    parts = [p for p in (triage_ctx, memory, markdown) if p]
    if not parts:
        return None
    return {"context": "\n".join(parts)}


def register(ctx):
    """Plugin entry point - called by Hermes plugin loader."""
    global _registered
    if _registered:
        return
    _registered = True

    from compat import check_version, verify_patch_applied
    from router import load_presets
    from preset_tool import TOOL_NAME, TOOL_SCHEMA, handle_preset

    status = check_version()
    if status == "error":
        raise RuntimeError(
            "cobalt-routing: incompatible Hermes version (>= 1.0.0). "
            "Update cobalt-routing for your Hermes version."
        )
    if status == "warn":
        logger.warning(
            "cobalt-routing v%s: untested Hermes version (tested up to 0.12.x).",
            PLUGIN_VERSION,
        )

    patch_ok = verify_patch_applied()
    if not patch_ok:
        logger.warning(
            "cobalt-routing: source patch NOT detected in delegate_tool.py. "
            "Per-task routing will be INACTIVE. See README for patch instructions."
        )

    load_presets()

    ctx.register_tool(
        name=TOOL_NAME,
        toolset="cobalt",
        schema=TOOL_SCHEMA,
        handler=handle_preset,
        description="Manage cobalt-routing presets (list, get, set)",
        emoji="⚡",
    )

    ctx.register_hook("pre_tool_call", _pre_tool_call_hook)
    ctx.register_hook("pre_llm_call", _pre_llm_call_hook)

    if not _patch_delegate_schema():
        logger.info("cobalt-routing: schema patch deferred to first delegate_task call")

    from tool_guard import _guard_enabled, ORCHESTRATOR_ALLOWED
    logger.info(
        "cobalt-routing v%s loaded (patch=%s, guard=%s, skills=ON, timeout=DYNAMIC, allowed=%d tools)",
        PLUGIN_VERSION,
        "OK" if patch_ok else "MISSING",
        "ON" if _guard_enabled else "OFF",
        len(ORCHESTRATOR_ALLOWED),
    )
