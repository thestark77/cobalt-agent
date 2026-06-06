"""Cobalt Routing Plugin v0.9.0 - Model routing + Tool Guard + Auto-SDD Skill Routing for Hermes Agent.

Five enforcement mechanisms via hooks:
1. TOOL GUARD: Blocks forbidden tools at orchestrator level (pre_tool_call)
2. MODEL ROUTING: Injects _routed_model into delegate_task (pre_tool_call)
3. AUTO-SDD SKILL ROUTING: Orchestrator injects skill_view directives for SDD phases (pre_llm_call via sdd_triage)
4. SDD TRIAGE: Forces orchestrator to classify and select SDD phases (pre_llm_call)
5. DYNAMIC TIMEOUT: Sets per-task timeout via env var before each delegation (pre_tool_call)

Requires source patch in delegate_tool.py for _routed_model fields.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.9.0"

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


def _inject_routing(task_dict: dict, task_type: str) -> None:
    """Inject routing fields, curation, memory + markitdown riders, timeout.

    Skill discovery is delegated to Hermes's native `build_skills_system_prompt`
    (see src/skill_injector.py for the rationale). Cobalt no longer keyword-
    matches goals against a skill table — that work was redundant with the
    `<available_skills>` block Hermes already injects into every system prompt.
    """
    from router import resolve_routing, apply_dynamic_timeout
    # Riders are optional — degrade gracefully if a sibling file is missing
    # so a partial install doesn't break delegate_task entirely.
    try:
        from memory_protocol import subagent_memory_rider
    except ImportError:
        subagent_memory_rider = lambda: ""
    try:
        from markitdown_protocol import subagent_markitdown_rider
    except ImportError:
        subagent_markitdown_rider = lambda: ""

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

    suffix = _CURATION_SUFFIXES.get(task_type)
    if suffix:
        task_dict["goal"] = task_dict.get("goal", "") + suffix
        logger.info("cobalt-routing: curation suffix injected for task_type=%s", task_type)

    # Incognito (C2): never tell a delegated sub-agent to save. On incognito
    # turns inject the anti-rider instead of the memory rider — the sub-agent
    # runs in its own process, so this file-based check + the pre_tool_call
    # block are the real guarantee, not the in-memory flag.
    incog = False
    try:
        from incognito import is_incognito_effective
        incog = is_incognito_effective()
    except Exception:
        incog = False
    if incog:
        try:
            from incognito import SUBAGENT_INCOGNITO_RIDER
            task_dict["goal"] = task_dict.get("goal", "") + SUBAGENT_INCOGNITO_RIDER
        except Exception:
            pass
    else:
        task_dict["goal"] = task_dict.get("goal", "") + subagent_memory_rider()
    task_dict["goal"] = task_dict.get("goal", "") + subagent_markitdown_rider()


def _pre_tool_call_hook(tool_name: str, args: dict, **kwargs):
    """Unified pre_tool_call hook: firewall + guard + routing + timeout.

    1. FIREWALL: If the terminal tool is called, inspect the command against
       the irreversibility firewall rules. Blocks or warns depending on mode.
       Fail-open: any exception in firewall logic allows the command through.
    2. GUARD: If orchestrator tries a forbidden tool, return block directive.
    3. ROUTING: If delegate_task, inject _routed_model based on task_type.
    4. TIMEOUT: Set per-task timeout via env var.
    """
    task_id = kwargs.get("task_id", "")

    # --- Firewall check (command/code-bearing tools) ---
    # Inspect every tool that can run a shell command or arbitrary code, not just
    # `terminal`: a live red-team test showed an agent that, once `rm -rf` was
    # blocked on terminal, could fall back to `rm -r` or to execute_code. Map each
    # such tool to the arg key that carries its payload.
    #   terminal      -> args["command"]   (terminal_tool.py)
    #   execute_code  -> args["code"]       (code_execution_tool.py)
    #   process       -> args["command"]    (process_registry.py)
    _FW_TOOLS = {"terminal": "command", "execute_code": "code", "process": "command"}
    if tool_name in _FW_TOOLS:
        try:
            from firewall import evaluate
            from firewall_tool import load_firewall_config
            fw_enabled, fw_mode = load_firewall_config()
            if fw_enabled:
                key = _FW_TOOLS[tool_name]
                # Defensively extract the payload from multiple possible shapes.
                payload = None
                if isinstance(args, dict):
                    payload = args.get(key)
                    if payload is None:
                        tool_input = args.get("tool_input") or args.get("input") or {}
                        if isinstance(tool_input, dict):
                            payload = tool_input.get(key)
                if payload and isinstance(payload, str):
                    # execute_code/process carry arbitrary code → enable the
                    # code-only destructive-pattern safety net.
                    result = evaluate(payload, fw_mode, is_code=(tool_name != "terminal"))
                    if result.get("blocked"):
                        logger.warning(
                            "cobalt-firewall: BLOCKED %s (mode=%s, rules=%s)",
                            tool_name,
                            fw_mode,
                            [h["rule_id"] for h in result.get("hits", [])],
                        )
                        return {
                            "action": "block",
                            "message": result["message"],
                        }
                    elif result.get("hits"):
                        # warn mode: non-irreversible hits — log and allow
                        logger.warning(
                            "cobalt-firewall: WARN %s allowed but hit rules: %s",
                            tool_name,
                            [h["rule_id"] for h in result["hits"]],
                        )
        except Exception as exc:
            # Fail-open: firewall errors must never break normal operations
            logger.debug("cobalt-firewall: exception (fail-open): %s", exc)

    # --- incognito: block writes/persistence when the turn is incognito ---
    # Privacy before efficiency: if the session (or this /secret turn) is
    # incognito, no write/persistence tool may run. Fail-open.
    try:
        from incognito import block_if_incognito
        inc_block = block_if_incognito(tool_name)
        if inc_block is not None:
            return inc_block
    except Exception as exc:
        logger.debug("cobalt-incognito: block check failed (fail-open): %s", exc)

    # --- markitdown auto-conversion (deterministic redirect) ---
    # Block raw reads of convertible files (PDF/DOCX/XLSX/audio/...) and redirect
    # to convert_to_markdown. Runs for sub-agents too (they do the actual reads).
    # Fail-open: any error lets the read proceed normally.
    try:
        from markitdown_protocol import intercept_file_read
        md_block = intercept_file_read(tool_name, args)
        if md_block is not None:
            return md_block
    except Exception as exc:
        logger.debug("cobalt-markitdown: intercept failed (fail-open): %s", exc)

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
    """Composite pre_llm_call hook: SDD triage + Engram memory + markitdown + iris.

    Sub-agents receive nothing (their context comes from the goal suffix).
    Orchestrator receives all blocks concatenated, every turn.

    Imports are wrapped per-module so a missing sibling file (e.g. after a
    partial install or a stale __pycache__) degrades gracefully to the
    blocks that ARE available instead of raising ImportError and skipping
    every block — which left the model with zero protocol guidance and
    caused the timeout-loop observed on 2026-05-14 session
    20260514_113515_94636c.
    """
    triage_hook = None
    build_memory_protocol_block = None
    build_markitdown_protocol_block = None
    build_convert_first_directive = None
    note_user_message = None
    build_iris_protocol_block = None
    build_finance_protocol_block = None
    build_karakeep_protocol_block = None
    build_ghostfolio_protocol_block = None
    build_calendar_protocol_block = None
    build_context_block = None
    try:
        from sdd_triage import pre_llm_call_hook as triage_hook
    except ImportError as exc:
        logger.warning("cobalt-routing: sdd_triage import failed (%s)", exc)
    try:
        from memory_protocol import build_memory_protocol_block
    except ImportError as exc:
        logger.warning(
            "cobalt-routing: memory_protocol import failed (%s) — "
            "Engram protocol WILL NOT be injected this turn. Re-run install.sh.",
            exc,
        )
    try:
        from markitdown_protocol import (
            build_markitdown_protocol_block,
            build_convert_first_directive,
            note_user_message,
        )
    except ImportError as exc:
        logger.warning("cobalt-routing: markitdown_protocol import failed (%s)", exc)
    try:
        from iris_protocol import build_iris_protocol_block
    except ImportError as exc:
        logger.warning("cobalt-routing: iris_protocol import failed (%s)", exc)
    try:
        from finance_protocol import build_finance_protocol_block
    except ImportError as exc:
        logger.warning("cobalt-routing: finance_protocol import failed (%s)", exc)
    try:
        from karakeep_protocol import build_karakeep_protocol_block
    except ImportError as exc:
        logger.warning("cobalt-routing: karakeep_protocol import failed (%s)", exc)
    try:
        from ghostfolio_protocol import build_ghostfolio_protocol_block
    except ImportError as exc:
        logger.warning("cobalt-routing: ghostfolio_protocol import failed (%s)", exc)
    try:
        from calendar_protocol import build_calendar_protocol_block
    except ImportError as exc:
        logger.warning("cobalt-routing: calendar_protocol import failed (%s)", exc)
    iris_maybe_capture = None
    try:
        from iris_capture import maybe_capture as iris_maybe_capture
    except ImportError as exc:
        logger.warning("cobalt-routing: iris_capture import failed (%s)", exc)
    try:
        from context_loader import build_context_block
    except ImportError as exc:
        logger.warning("cobalt-routing: context_loader import failed (%s)", exc)

    # --- incognito evaluation (orchestrator only) ---
    # Detect /incognito and /secret, flip session state, and decide if THIS turn
    # is incognito. Sub-agents skip this (they inherit the orchestrator turn's
    # flag); their writes are still blocked via the persistent session check.
    turn_incognito = False
    incognito_note = None
    incognito_block = None
    from config import is_subagent as _is_subagent
    if not _is_subagent(task_id):
        try:
            from incognito import (
                evaluate_turn,
                set_turn_incognito,
                build_incognito_directive,
            )
            set_turn_incognito(False)  # safe default before eval: a throw must
            # never leave a stale True/False from the previous turn (W2).
            turn_incognito, incognito_note = evaluate_turn(user_message)
            set_turn_incognito(turn_incognito)
            incognito_block = build_incognito_directive(turn_incognito, incognito_note)
        except Exception as exc:
            logger.debug("cobalt-incognito: evaluate failed (fail-open): %s", exc)

    if triage_hook is None:
        triage = None
    else:
        triage = triage_hook(
            user_message=user_message,
            task_id=task_id,
            conversation_history=conversation_history,
            **kwargs,
        )
    memory = build_memory_protocol_block(task_id=task_id) if build_memory_protocol_block else None
    if turn_incognito:
        memory = None  # incognito: do not nudge the model to save anything
    markdown = build_markitdown_protocol_block(task_id=task_id) if build_markitdown_protocol_block else None
    # Record the human message so the pre_tool_call interception can honor a
    # per-turn "read it raw" opt-out, then build the proactive convert-first
    # directive (names any uploaded convertible file so it is converted before
    # any read attempt). Both are guarded so a failure here never drops the
    # other protocol blocks (memory/triage/iris) for this turn.
    if note_user_message is not None and not turn_incognito:
        try:
            note_user_message(user_message)
        except Exception as exc:
            logger.debug("cobalt-markitdown: note_user_message failed (%s)", exc)
    convert_first = None
    if build_convert_first_directive is not None:
        try:
            convert_first = build_convert_first_directive(
                user_message=user_message, task_id=task_id
            )
        except Exception as exc:
            logger.debug("cobalt-markitdown: convert_first failed (%s)", exc)
    # Incognito: suppress the iris protocol block too — it carries mandatory
    # write instructions (record_nudge_outcome / decide) that contradict "save
    # nothing" (C1). Reads stay available via the tools themselves.
    iris = None
    if build_iris_protocol_block and not turn_incognito:
        iris = build_iris_protocol_block(task_id=task_id)
    # Finance: same incognito rule as iris — the block carries write mandates
    # (record transactions in Firefly), which contradict "save nothing". It is
    # also self-gated: returns None unless the Firefly MCP is configured.
    finance = None
    if build_finance_protocol_block and not turn_incognito:
        try:
            finance = build_finance_protocol_block(task_id=task_id)
        except Exception as exc:
            logger.debug("cobalt-finance: build block failed (%s)", exc)
    # References (Karakeep): self-gated on the karakeep MCP; suppressed on
    # incognito turns (it carries a save mandate). Reads stay available.
    karakeep = None
    if build_karakeep_protocol_block and not turn_incognito:
        try:
            karakeep = build_karakeep_protocol_block(task_id=task_id)
        except Exception as exc:
            logger.debug("cobalt-karakeep: build block failed (%s)", exc)
    # Investments (Ghostfolio): self-gated on the ghostfolio MCP; suppressed on
    # incognito turns (it carries write/record mandates). Reads stay available.
    ghostfolio = None
    if build_ghostfolio_protocol_block and not turn_incognito:
        try:
            ghostfolio = build_ghostfolio_protocol_block(task_id=task_id)
        except Exception as exc:
            logger.debug("cobalt-ghostfolio: build block failed (%s)", exc)
    # Calendar: self-gated on a calendar MCP; suppressed on incognito turns (it
    # carries event-create mandates). Reads stay available.
    calendar = None
    if build_calendar_protocol_block and not turn_incognito:
        try:
            calendar = build_calendar_protocol_block(task_id=task_id)
        except Exception as exc:
            logger.debug("cobalt-calendar: build block failed (%s)", exc)
    session_id = kwargs.get("session_id", "")

    # Deterministic memory capture (fire-and-forget; no-op unless iris is
    # configured). Runs in a daemon thread, never blocks this turn, swallows
    # all errors. Decoupled from iris: writes to Engram, never calls iris.* .
    if iris_maybe_capture is not None and not turn_incognito:
        try:
            iris_maybe_capture(
                user_message=user_message, task_id=task_id, session_id=session_id
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("cobalt-routing: iris_capture call failed (%s)", exc)

    project_context = (
        build_context_block(task_id=task_id, session_id=session_id)
        if build_context_block else None
    )

    triage_ctx = (triage or {}).get("context", "") if isinstance(triage, dict) else ""
    if turn_incognito:
        triage_ctx = ""  # incognito: drop SDD triage (carries an archive-save mandate, W1)
    # Order matters: PROJECT CONTEXT goes first so the rules it carries are
    # in scope before the triage / memory blocks ask the model to act.
    parts = [p for p in (incognito_block, project_context, triage_ctx, memory, markdown, convert_first, iris, finance, karakeep, ghostfolio, calendar) if p]
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
    from firewall_tool import (
        TOOL_NAME as FW_TOOL_NAME,
        TOOL_SCHEMA as FW_TOOL_SCHEMA,
        handle_firewall,
    )

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

    try:
        ctx.register_tool(
            name=FW_TOOL_NAME,
            toolset="cobalt",
            schema=FW_TOOL_SCHEMA,
            handler=handle_firewall,
            description="Manage cobalt irreversibility firewall (status, set, enable, disable)",
            emoji="🛡",
        )
    except Exception as exc:
        logger.warning("cobalt-firewall: tool registration failed (continuing): %s", exc)

    try:
        from incognito import (
            TOOL_NAME as INC_TOOL_NAME,
            TOOL_SCHEMA as INC_TOOL_SCHEMA,
            handle_incognito,
        )
        ctx.register_tool(
            name=INC_TOOL_NAME,
            toolset="cobalt",
            schema=INC_TOOL_SCHEMA,
            handler=handle_incognito,
            description="Manage incognito mode (read-yes/write-no): status, on, off",
            emoji="🕶",
        )
    except Exception as exc:
        logger.warning("cobalt-incognito: tool registration failed (continuing): %s", exc)

    # Finance: deterministic statement reconciliation matcher. Pure-function
    # tool — the model passes parsed statement lines + reported Firefly entries
    # and gets match/new/ambiguous verdicts, so the anti-duplication decision is
    # made in code. Harmless when Firefly is not wired (the finance protocol
    # block that tells the model to reconcile is gated off in that case).
    try:
        from reconcile import (
            TOOL_NAME as REC_TOOL_NAME,
            TOOL_SCHEMA as REC_TOOL_SCHEMA,
            handle_reconcile,
        )
        ctx.register_tool(
            name=REC_TOOL_NAME,
            toolset="cobalt",
            schema=REC_TOOL_SCHEMA,
            handler=handle_reconcile,
            description="Reconcile statement lines vs reported expenses (match/new/ambiguous) — anti-duplication",
            emoji="🧾",
        )
    except Exception as exc:
        logger.warning("cobalt-finance: reconcile tool registration failed (continuing): %s", exc)

    # Slash commands: Hermes routes leading-slash messages to the gateway command
    # dispatcher BEFORE the LLM, so /incognito and /secret must be registered here
    # (otherwise they bounce as "Unknown command"). fn(raw_args) -> str.
    try:
        from incognito import handle_incognito_command, handle_secret_command
        ctx.register_command(
            "incognito", handle_incognito_command,
            description="Incognito sticky toggle (read-yes/write-no): /incognito [on|off|status]",
            args_hint="on|off|status",
        )
        ctx.register_command(
            "secret", handle_secret_command,
            description="Make your NEXT message private — processed but never persisted",
        )
    except Exception as exc:
        logger.warning("cobalt-incognito: slash command registration failed (continuing): %s", exc)

    ctx.register_hook("pre_tool_call", _pre_tool_call_hook)
    ctx.register_hook("pre_llm_call", _pre_llm_call_hook)

    if not _patch_delegate_schema():
        logger.info("cobalt-routing: schema patch deferred to first delegate_task call")

    from tool_guard import _guard_enabled, ORCHESTRATOR_ALLOWED
    try:
        from firewall_tool import load_firewall_config as _fw_cfg
        _fw_on, _fw_mode = _fw_cfg()
        _fw_status = f"{_fw_mode}" if _fw_on else "OFF"
    except Exception:
        _fw_status = "?"
    logger.info(
        "cobalt-routing v%s loaded (patch=%s, guard=%s, firewall=%s, skills=ON, timeout=DYNAMIC, allowed=%d tools)",
        PLUGIN_VERSION,
        "OK" if patch_ok else "MISSING",
        "ON" if _guard_enabled else "OFF",
        _fw_status,
        len(ORCHESTRATOR_ALLOWED),
    )
