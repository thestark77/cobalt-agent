"""Cobalt Routing - Skill Resolution for Sub-Agents.

The orchestrator does NOT read skill content. It only identifies which
skill(s) are relevant for a given goal and instructs the sub-agent to
load them via skill_view in its own independent context.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_SKILLS_PER_DELEGATION = 2
SKILLS_DIR = Path.home() / ".hermes" / "skills"

_SKILL_ROUTES: List[Tuple[List[str], str]] = [
    # Browser automation — playwright-cli runs alongside e2e-testing-patterns
    # when both are matched (MAX_SKILLS_PER_DELEGATION=2). Listed FIRST so a
    # goal mentioning "playwright" gets the CLI skill in addition to e2e patterns.
    (
        ["playwright cli", "playwright codegen", "playwright record",
         "browser automation", "automate browser", "headless browser",
         "browser test", "selector inspection", "screenshot script"],
        "playwright-cli",
    ),
    (
        ["e2e test", "playwright", "cypress", "testing framework",
         "spec file", "pruebas e2e", "integration test", "test suite",
         "pruebas de integracion", "end to end"],
        "e2e-testing-patterns",
    ),
    # Frontend / design — order matters. Specific design-quality skills BEFORE
    # the broad frontend-design fallback so they get picked when keywords match.
    (
        ["anti-slop", "premium frontend", "premium taste", "generic design",
         "boring design", "polish design", "design taste", "feel cheap",
         "feel premium", "high-end design", "tasteful", "buen gusto"],
        "gpt-tasteskill",
    ),
    (
        ["design language", "design system", "design tokens", "brand voice",
         "design coherence", "design consistency", "design refinement",
         "sistema de diseño", "lenguaje de diseño"],
        "impeccable",
    ),
    (
        ["html prototype", "hi-fi prototype", "high fidelity", "slide deck",
         "slideshow", "presentation", "html animation", "design demo",
         "prototype animation", "mockup html", "diseño html", "prototipo hi-fi",
         "presentacion html", "demo interactivo", "iphone mockup",
         "device mockup", "html slide"],
        "huashu-design",
    ),
    (
        ["ui design", "ux design", "ui-ux", "professional ui", "design intelligence",
         "design across platforms", "multi-platform design", "diseño ui",
         "diseño ux", "diseño profesional"],
        "ui-ux-pro-max",
    ),
    (
        ["frontend", "react", "vue", "svelte", "next.js", "nextjs", "component design",
         "landing page", "user interface", "interfaz de usuario",
         "tailwind", "css module", "html template", "layout design",
         "responsive design", "pagina web", "single page app", "spa ",
         "react app", "vue app", "next app", "nuxt app"],
        "frontend-design",
    ),
    (
        ["admin panel", "panel de admin", "backoffice", "saas interface",
         "tool interface", "app design", "dashboard design", "panel administrativo"],
        "interface-design",
    ),
    (
        ["error handling", "manejo de errores", "exception handling",
         "result type", "fallback strategy", "retry logic", "circuit breaker",
         "error boundary", "graceful degradation"],
        "error-handling-patterns",
    ),
    (
        ["postgres", "postgresql", "database schema", "migration",
         "table design", "index design", "constraint design",
         "sql schema", "base de datos", "db design", "esquema de base"],
        "postgresql",
    ),
    (
        ["prompt engineer", "system prompt", "few-shot", "chain of thought",
         "prompt template", "prompt optim", "prompt design"],
        "prompt-engineering-patterns",
    ),
    (
        ["pull request", "crear pr", "branch strategy", "merge request",
         "code review", "git flow", "branch naming"],
        "branch-pr",
    ),
    (
        ["knowledge graph", "grafo de conocimiento", "obsidian graph",
         "relaciones entre conceptos", "mapa conceptual", "graph database"],
        "knowledge-graph",
    ),
]

_TASK_TYPE_AFFINITY: Dict[str, List[str]] = {
    "design": ["prompt-engineering-patterns", "ui-ux-pro-max"],
    "spec": ["prompt-engineering-patterns"],
    "verify": ["e2e-testing-patterns", "playwright-cli"],
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
    if not skill_names:
        return ""
    if len(skill_names) == 1:
        return _SKILL_INSTRUCTION_TEMPLATE.format(name=skill_names[0])
    skill_list = "\n".join(f"- {name}" for name in skill_names)
    return _MULTI_SKILL_INSTRUCTION_TEMPLATE.format(skill_list=skill_list)


def inject_skill_instruction(task_dict: dict, task_type: Optional[str] = None) -> List[str]:
    goal = task_dict.get("goal", "")
    if not goal:
        return []
    matched = match_skills(goal, task_type)
    if not matched:
        return []
    instruction = build_skill_instruction(matched)
    task_dict["goal"] = goal + instruction
    logger.info("cobalt-routing: skill instruction -> %s for: %s", matched, goal[:60])
    return matched
