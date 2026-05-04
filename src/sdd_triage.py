"""Cobalt Routing — Mechanical SDD Triage Injection.

Hooks into pre_llm_call to inject a mandatory triage block when the user
message appears to be an execution task. The orchestrator CANNOT skip this
because it arrives as injected context in the user message.

Detection heuristic:
- Action verbs (imperatives) in the first 100 chars → task
- Question marks dominant → conversation
- Short messages (<30 chars) → conversation
- Keywords like "necesito", "creá", "implementá", "hacé" → task
"""

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ACTION_INDICATORS_ES = [
    "necesito", "creá", "crea", "hacé", "hace falta", "implementá", "implementa",
    "escribí", "escribe", "construí", "construye", "agregá", "agrega",
    "modificá", "modifica", "eliminá", "elimina", "corregí", "corrige",
    "configurá", "configura", "instalá", "instala", "desplegá", "despliega",
    "refactorizá", "refactoriza", "optimizá", "optimiza", "migrá", "migra",
    "actualizá", "actualiza", "generá", "genera", "diseñá", "diseña",
]

_ACTION_INDICATORS_EN = [
    "i need", "create", "build", "implement", "write", "make",
    "add", "modify", "delete", "remove", "fix", "configure",
    "install", "deploy", "refactor", "optimize", "migrate",
    "update", "generate", "design", "set up", "develop",
]

_CONVERSATION_INDICATORS = [
    "?", "qué opinas", "qué pensás", "what do you think",
    "cómo ves", "how about", "could you explain", "explicame",
    "por qué", "why", "what is", "qué es", "recuerda que",
    "por cierto", "btw", "a propósito",
]

_TRIAGE_INJECTION = """
[MANDATORY TRIAGE — respond to this BEFORE any delegation or action]
Classify this request:
1. Is this a CONVERSATION (question/opinion/clarification)? → Respond directly.
2. Is this an EXECUTION TASK? → State which SDD phases you will apply:
   - Explore (research/read)
   - Propose (present plan, WAIT for approval)
   - Spec (define requirements)
   - Design (architecture)
   - Tasks (atomic breakdown)
   - Apply (write code)
   - Verify (test)
   - Archive (save learnings)

State your classification and selected phases in ONE line before proceeding.
Example: "TASK: Explore → Propose → Apply → Verify → Archive"
If unsure, ask the user which phases to apply.
"""


def _looks_like_task(message: str) -> bool:
    """Heuristic: does this message look like an execution task?"""
    if not message or len(message.strip()) < 30:
        return False

    msg_lower = message.lower().strip()
    first_100 = msg_lower[:100]

    # Strong conversation signals
    conversation_score = sum(1 for ind in _CONVERSATION_INDICATORS if ind in msg_lower)
    if conversation_score >= 2:
        return False

    # If message is mostly a question (>50% question marks vs periods)
    questions = msg_lower.count("?")
    periods = msg_lower.count(".")
    if questions > 0 and questions >= periods:
        return False

    # Action verb detection
    action_score = 0
    for verb in _ACTION_INDICATORS_ES:
        if verb in first_100:
            action_score += 2
            break
    for verb in _ACTION_INDICATORS_EN:
        if verb in first_100:
            action_score += 2
            break

    # Length-based: longer messages with multiple sentences are likely tasks
    if len(msg_lower) > 200 and periods >= 2:
        action_score += 1

    # Contains file paths or code references
    if re.search(r'[~/.][\w/]+\.\w+', message):
        action_score += 1

    return action_score >= 2


def pre_llm_call_hook(
    user_message: str = "",
    is_first_turn: bool = False,
    task_id: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """Inject triage block for orchestrator when message looks like a task.

    Only fires for the orchestrator (no task_id prefix = orchestrator).
    Sub-agents never get triage injection.
    """
    # Only inject for orchestrator, not sub-agents
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None

    if not _looks_like_task(user_message):
        return None

    logger.info("cobalt-routing: SDD triage injected for task-like message")
    return {"context": _TRIAGE_INJECTION}
