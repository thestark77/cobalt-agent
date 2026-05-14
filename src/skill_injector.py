"""Cobalt Routing — Skill discovery is delegated to Hermes native.

Historical note (v0.7.x and earlier):
This module used to keyword-match goal text against a static `_SKILL_ROUTES`
table and append `[SKILL REQUIRED: name]` riders to sub-agent goals.

Why it was removed (v0.8.0):
Hermes already implements Anthropic-style skill discovery natively. At every
system-prompt build (see `agent/prompt_builder.py:build_skills_system_prompt`
called from `run_agent.py`), it scans `~/.hermes/skills/**/SKILL.md`, extracts
`name + description` from frontmatter, and injects an `<available_skills>`
block into the system prompt with a MANDATORY instruction:

    "Before replying, scan the skills below. If a skill matches or is even
    partially relevant to your task, you MUST load it with skill_view(name)..."

This runs for orchestrator AND sub-agents, is cached (LRU + disk snapshot
invalidated by mtime), and uses rich descriptions instead of brittle keyword
matches. Adding cobalt's keyword router on top would:
  - duplicate work Hermes is already doing,
  - bloat sub-agent goal text with redundant riders,
  - risk conflicting with Hermes's natural skill-selection decision.

If you need to FORCE a specific skill on a delegation (rare), the orchestrator
should write the instruction directly in the goal text:

    "Before starting, call skill_view('frontend-design') and apply its rules."

The orchestrator sees the full `<available_skills>` catalog every turn so it
has the context to make this call.
"""

# Intentional: this module is empty by design. The keyword router is gone.
