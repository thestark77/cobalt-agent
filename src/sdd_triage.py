"""Cobalt Routing — Mechanical SDD Triage Injection.

Hooks into pre_llm_call to inject a mandatory triage block on EVERY
orchestrator turn. No exceptions, no heuristics, no threshold.

The orchestrator must classify the request and state which SDD phases
apply before taking any action. If there is an active plan (todo items
in progress), the triage switches to STEERING mode.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TRIAGE_INJECTION = """
[MANDATORY TRIAGE — respond to this BEFORE any delegation or action]
Classify this request:
1. CONVERSATION (question/opinion/clarification/feedback) → Respond directly, no delegation needed.
2. EXECUTION TASK → State which SDD phases you will apply:
   - Explore (research/read existing code or docs)
   - Propose (present plan to user, WAIT for approval)
   - Spec (define requirements/acceptance criteria)
   - Design (architecture/technical decisions)
   - Tasks (atomic breakdown via todo)
   - Apply (write/modify code)
   - Verify (test/validate)
   - Archive (save learnings to memory)

State your classification in ONE line before proceeding.
Format: "TASK: Explore → Apply → Verify → Archive" or "CONVERSATION: [respond directly]"
If the scope is unclear or complex, ask the user which phases to apply.
Bias: apply MORE phases rather than fewer for any non-trivial task.
"""

_STEERING_INJECTION = """
[MANDATORY TRIAGE — ACTIVE PLAN DETECTED]
There is work in progress. Classify this new message:
1. MODIFIES the current plan (changes requirements, scope, or approach) → State what changes, RE-PRIORITIZE tasks, RESUME.
2. EXTENDS the current plan (adds new requirements without changing existing) → Add to task queue, continue.
3. OVERRIDES the current plan (contradicts or supersedes it) → STOP current execution, replan from scratch.
4. UNRELATED (question, feedback, or new topic) → ANSWER directly, then RESUME current plan.

State your classification in ONE line, then act accordingly.
Format: "STEERING: MODIFIES — [what changes]" or "STEERING: EXTENDS — [what's added]" or "STEERING: UNRELATED — [answer]"
"""


def pre_llm_call_hook(
    user_message: str = "",
    task_id: str = "",
    conversation_history: list = None,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Inject triage or steering block on every orchestrator turn.

    - Sub-agents: never injected
    - Orchestrator with active plan: STEERING variant
    - Orchestrator without active plan: TRIAGE variant
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None

    if not user_message:
        return None

    if _has_active_plan(conversation_history):
        logger.info("cobalt-routing: SDD steering injected (active plan detected)")
        return {"context": _STEERING_INJECTION}

    logger.info("cobalt-routing: SDD triage injected")
    return {"context": _TRIAGE_INJECTION}


def _has_active_plan(conversation_history: list = None) -> bool:
    """Detect if there's an active SDD plan by checking recent assistant messages.

    Looks for evidence of:
    - A triage classification that started a TASK
    - delegate_task calls in recent history
    - todo tool usage (task breakdown in progress)
    """
    if not conversation_history:
        return False

    recent = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history

    for msg in reversed(recent):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "tool_use":
                        name = part.get("name", "")
                        if name in ("delegate_task", "todo"):
                            return True
                        if name == "honcho_conclude":
                            return False
        elif isinstance(content, str):
            if "TASK:" in content and "→" in content:
                if "Archive" not in content.split("→")[-1]:
                    return True

    return False
