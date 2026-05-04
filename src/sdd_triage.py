"""Cobalt Routing — Mechanical SDD Triage Injection.

Hooks into pre_llm_call to inject a mandatory triage block on EVERY
orchestrator turn. The orchestrator must classify the request and state
which SDD phases apply before taking any action.

This is NOT optional and NOT heuristic-based. It fires on every turn
except trivial acknowledgments (<20 chars). The orchestrator itself
decides if it's a conversation or task — but it MUST make that decision
explicitly every time.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MIN_MESSAGE_LENGTH = 20

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


def pre_llm_call_hook(
    user_message: str = "",
    task_id: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Inject triage block on every orchestrator turn.

    Only skips for:
    - Sub-agents (task_id starts with sa- or subagent-)
    - Trivial messages under MIN_MESSAGE_LENGTH chars
    """
    # Never inject for sub-agents
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None

    # Skip trivial acknowledgments
    if not user_message or len(user_message.strip()) < MIN_MESSAGE_LENGTH:
        return None

    logger.info("cobalt-routing: SDD triage injected")
    return {"context": _TRIAGE_INJECTION}
