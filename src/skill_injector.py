"""Cobalt Routing — Skill Resolution for Sub-Agents.

The orchestrator does NOT read skill content. It only identifies which
skill(s) are relevant for a given goal and instructs the sub-agent to
load them via skill_view in its own independent context.

Rules:
- Max 2 skills per delegation
- Only reference skills that are installed in ~/.hermes/skills/
- Graceful: if no match, no injection
- The instruction goes into the goal suffix (not context)
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_SKILLS_PER_DELEGATION = 2
SKILLS_DIR = Path.home() / ".hermes" / "skills"

# Pattern → skill name mapping
# Order matters: first match wins for each pattern group
_SKILL_ROUTES: List[Tuple[List[str], str]] = [
    (
        ["frontend", "react", "vue", "svelte", "next", "component", "landing",
         "dashboard", "ui ", "interfaz", "pagina web", "web page",
         "tailwind", "css", "html", "layout", "responsive", "webapp"],
        "frontend-design",
    ),
    (
        ["admin panel", "panel de admin", "backoffice", "saas interface",
         "tool interface", "app design", "dashboard design"],
        "interface-design",
    ),
    (
        ["test", "tests", "e2e", "playwright", "cypress", "testing",
         "spec file", "pruebas e2e", "integration test", "test suite"],
        "e2e-testing-patterns",
    ),
    (
        ["error", "exception", "error handling", "manejo de errores",
         "result type", "fallback", "retry", "circuit breaker",
         "error boundary", "try catch"],
        "error-handling-patterns",
    ),
    (
        ["postgres", "postgresql", "database schema", "migration",
         "tabla", "table design", "index", "constraint", "sql schema",
         "base de datos", "db design"],
        "postgresql",
    ),
    (
        ["prompt engineer", "system prompt", "few-shot", "chain of thought",
         "prompt template", "prompt optim", "prompt design"],
        "prompt-engineering-patterns",
    ),
    (
        ["pull request", "pr ", "branch", "merge request", "rama",
         "crear pr", "code review", "git flow", "open pr"],
        "branch-pr",
    ),
    (
        ["knowledge graph", "grafo de conocimiento", "obsidian graph",
         "relaciones entre conceptos", "mapa conceptual"],
        "knowledge-graph",
    ),
]

# task_type → skills that are always relevant for that type
_TASK_TYPE_AFFINITY: Dict[str, List[str]] = {
    "design": ["prompt-engineering-patterns"],
    "spec": ["prompt-engineering-patterns"],
    "verify": ["e2e-testing-patterns"],
}

_SKILL_INSTRUCTION_TEMPLATE = (
    "\n\n[SKILL REQUIRED] Before starting, load and follow the skill '{name}' "
    "by calling skill_view(\"{name}\"). Apply its patterns and guidelines to this task."
)

_MULTI_SKILL_INSTRUCTION_TEMPLATE = (
    "\n\n[SKILLS REQUIRED] Before starting, load these skills and follow their patterns:\n"
    "{skill_list}\n"
    "Call skill_view(name) for each one and apply their guidelines to this task."
)


def _skill_exists(name: str) -> bool:
    return (SKILLS_DIR / name / "SKILL.md").exists()


def match_skills(goal: str, task_type: Optional[str] = None) -> List[str]:
    """Return up to MAX_SKILLS_PER_DELEGATION skill names matching the goal."""
    goal_lower = goal.lower()
    matched: List[str] = []

    for patterns, skill_name in _SKILL_ROUTES:
        if any(p in goal_lower for p in patterns):
            if skill_name not in matched and _skill_exists(skill_name):
                matched.append(skill_name)
        if len(matched) >= MAX_SKILLS_PER_DELEGATION:
            break

    if task_type and len(matched) < MAX_SKILLS_PER_DELEGATION:
        for skill_name in _TASK_TYPE_AFFINITY.get(task_type, []):
            if skill_name not in matched and _skill_exists(skill_name):
                matched.append(skill_name)
            if len(matched) >= MAX_SKILLS_PER_DELEGATION:
                break

    return matched


def build_skill_instruction(skill_names: List[str]) -> str:
    """Build the goal suffix telling the sub-agent which skills to load."""
    if not skill_names:
        return ""

    if len(skill_names) == 1:
        return _SKILL_INSTRUCTION_TEMPLATE.format(name=skill_names[0])

    skill_list = "\n".join(f"- {name}" for name in skill_names)
    return _MULTI_SKILL_INSTRUCTION_TEMPLATE.format(skill_list=skill_list)


def inject_skill_instruction(task_dict: dict, task_type: Optional[str] = None) -> List[str]:
    """Append skill instruction to task goal. Returns injected skill names."""
    goal = task_dict.get("goal", "")
    if not goal:
        return []

    matched = match_skills(goal, task_type)
    if not matched:
        return []

    instruction = build_skill_instruction(matched)
    task_dict["goal"] = goal + instruction

    logger.info("cobalt-routing: skill instruction → %s for: %s", matched, goal[:60])
    return matched
