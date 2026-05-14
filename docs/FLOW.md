# Cobalt Agent — Expected Flow (Source of Truth)

> This document describes the COMPLETE expected behavior of the cobalt-agent system.
> Every component, mechanism, and rule is documented here.
> Updated: 2026-05-03 | Plugin version: 0.3.0

---

## 1. Philosophy

- The orchestrator PLANS, DELEGATES, COORDINATES, and SYNTHESIZES. It never executes.
- Everything critical is MECHANICAL (enforced by hooks), not dependent on model compliance.
- Sub-agents are disposable workers — they get focused goals, curated context, and skill guidance.
- Memory is persistent and cross-session. Knowledge compounds.
- Cost is optimized: cheap models for discovery, mid-tier for execution, premium for reasoning.
- Parallelism is maximized: independent tasks always run concurrently (up to max_concurrent_children=3).

---

## 2. SDD Phases (9 phases mapped to Hermes)

| # | Phase | Hermes Implementation | task_type | Who |
|---|-------|-----------------------|-----------|-----|
| 1 | Init | Skill `sdd-init` — detect stack, frameworks, conventions | — | Orchestrator |
| 2 | Explore | `delegate_task` — discover existing code, docs, APIs | scout/explore | Sub-agent (flash) |
| 3 | Propose | Orchestrator synthesizes findings, proposes approach to user | — | Orchestrator |
| 4 | Spec | Skill `sdd-spec` — Given/When/Then requirements | spec | Sub-agent (pro) |
| 5 | Design | Skill `sdd-design` — technical architecture document | design | Sub-agent (pro) |
| 6 | Tasks | `todo` tool — atomic task breakdown | tasks | Orchestrator |
| 7 | Apply | `delegate_task` — implement each task | apply | Sub-agent (K2.6) |
| 8 | Verify | `delegate_task` — run tests, validate output | verify | Sub-agent (pro) |
| 9 | Archive | `mcp_engram_mem_session_summary` / `mcp_engram_mem_save` (Engram) — save learnings, decisions, outcomes | archive | Orchestrator |

---

## 3. Model Routing (economy preset)

| task_type | Model | Cost/M input | Use case |
|-----------|-------|-------------|----------|
| scout | deepseek-v4-flash | $0.14 | Web search, find info |
| explore | deepseek-v4-flash | $0.14 | Read/analyze code |
| summarize | deepseek-v4-flash | $0.14 | Condense information |
| apply | kimi-k2.6 | $0.32 | Write/modify code |
| archive | kimi-k2.6 | $0.32 | Cleanup, documentation |
| design | deepseek-v4-pro | $1.74 | Architecture decisions |
| spec | deepseek-v4-pro | $1.74 | Requirements definition |
| tasks | deepseek-v4-pro | $1.74 | Task decomposition |
| verify | deepseek-v4-pro | $1.74 | Test validation |
| propose | deepseek-v4-pro | $1.74 | Evaluate tradeoffs |

---

## 4. Tool Guard (Mechanical Enforcement)

The orchestrator is BLOCKED from calling execution tools directly. Returns error forcing delegation.

**Allowed tools:**
delegate_task, memory, cobalt_preset, clarify, todo, skills_list, skill_view, skill_manage, send_message, session_search, cronjob, mcp_engram_mem_save, mcp_engram_mem_search, mcp_engram_mem_get_observation, mcp_engram_mem_context, mcp_engram_mem_session_summary, mcp_engram_mem_save_prompt, mcp_engram_mem_suggest_topic_key, mcp_engram_mem_current_project, mcp_engram_mem_update

**Sub-agents:** Unrestricted (detected by task_id prefix "sa-")

---

## 5. Memory System (Engram via MCP)

Memory is provided by [Engram](https://github.com/Gentleman-Programming/engram), a self-hosted MCP server. The orchestrator follows a strict, rule-based protocol injected on every turn (see `src/memory_protocol.py`).

### Orchestrator-initiated (via tools):
- `mcp_engram_mem_search` — ALWAYS before delegating (avoid redundant scouts)
- `mcp_engram_mem_context` — fast recent-history lookup before falling back to mcp_engram_mem_search
- `mcp_engram_mem_get_observation` — get full untruncated content of a search result
- `mcp_engram_mem_save` — IMMEDIATELY after any decision, bugfix, discovery, pattern, or convention
- `mcp_engram_mem_session_summary` — MANDATORY before saying "done"/"listo"; persists Goal / Discoveries / Accomplished / Next Steps / Relevant Files

### Sub-agent rider (automatic):
Every delegated goal is suffixed with a "[MEMORY — sub-agent rule]" block telling the sub-agent to `mcp_engram_mem_save` discoveries before returning. The orchestrator never sees sub-agent context, so the sub-agent persists or it is lost.

### Cross-language:
Confirmed working: save in English, search in Spanish (and vice versa).

---

## 6. Curation Suffixes (Sub-Agent Response Format)

Injected automatically into sub-agent goals by task_type:

**scout/explore/summarize/verify** get:
- `[RESPONSE FORMAT]` — specific structure (key findings, max words, etc.)
- `[DISCARDED INFO]` — must list what they found but excluded (topic + 1-line reason)

The orchestrator uses discarded info to decide if follow-up queries are needed.

---

## 7. Skill Injection (IMPLEMENTED — v0.4.0)

### Problem:
Hermes does NOT inject skills into sub-agents natively. Sub-agents work blind.

### Solution:
cobalt-routing's `skill_injector.py` pattern-matches goal/task_type to relevant
skill names and appends an instruction to the sub-agent's goal telling it to
call `skill_view("skill-name")` before starting work. The sub-agent loads the
full skill in its OWN context — the orchestrator never reads skill content.

### How it works:
1. Plugin intercepts `delegate_task` via pre_tool_call hook
2. `inject_skill_instruction(task_dict, task_type)` runs after routing
3. Goal keywords are matched against `_SKILL_ROUTES` patterns
4. If match found → goal gets appended: `[SKILL REQUIRED] ... skill_view("name")`
5. Sub-agent sees the instruction, calls skill_view, loads full skill independently

### Routing rules:
- Frontend/React/UI/component/landing/tailwind → `frontend-design`
- Admin panel/backoffice/saas/tool interface → `interface-design`
- Test/e2e/playwright/cypress/spec file → `e2e-testing-patterns`
- Error/exception/retry/circuit breaker → `error-handling-patterns`
- Postgres/database schema/migration/table → `postgresql`
- Prompt engineering/system prompt/few-shot → `prompt-engineering-patterns`
- PR/branch/merge/code review → `branch-pr`
- Knowledge graph/grafo/obsidian graph → `knowledge-graph`

### Task-type affinity (automatic, no keywords needed):
- design/spec → `prompt-engineering-patterns`
- verify → `e2e-testing-patterns`

### Constraints:
- Max 2 skills per delegation (context budget)
- Only reference skills that exist in ~/.hermes/skills/
- Graceful: if no match, no injection (sub-agent works without)
- Orchestrator NEVER reads skill content (stays lean)

---

## 8. Parallelism Rules

- Independent tasks ALWAYS run in parallel (up to 3 concurrent)
- "Independent" = result of task A is NOT needed to start task B
- Dependent tasks run sequentially (scout BEFORE apply)
- If >3 independent tasks: batch in groups of 3

### Examples:
- "Search API docs" + "Read local config" + "Check installed packages" → 3 parallel scouts
- "Search docs" → THEN "Write script using those docs" → sequential (depends on scout result)

---

## 9. Timeouts (per task_type)

| task_type | Timeout | Rationale |
|-----------|---------|-----------|
| scout/explore/summarize | 300s | Discovery shouldn't take long |
| apply/archive | 600s | Code writing needs more time |
| design/spec/tasks/verify/propose | 900s | Reasoning needs full budget |

---

## 10. SOUL.md (Orchestrator Identity)

Located at `~/.hermes/SOUL.md`. Loaded every turn. Must be <800 tokens.

Key rules:
1. ABSOLUTE RULE: Never call execution tools directly
2. MANDATORY TRIAGE (Step 0): Before ANY work, decide if SDD applies and WHICH phases
3. ALWAYS mcp_engram_mem_search before delegating (Engram)
4. ALWAYS set task_type on every delegation
5. ALWAYS maximize parallelism for independent tasks
6. ALWAYS include WHY in goals (sub-agent needs context to curate response)
7. ALWAYS mcp_engram_mem_session_summary after completing work
8. Respond in user's language

### SDD Triage Rules:
- Conversation/question/opinion → respond directly, no SDD
- Execution task → apply SDD with selected phases
- Simple (single file, clear requirements): Explore → Apply → Verify → Archive
- Medium (multiple concerns, unclear): Explore → Propose → Tasks → Apply → Verify → Archive
- Complex (architecture, multi-file, ambiguous): ALL 9 phases
- If unsure → ASK the user which phases before starting
- Bias: apply MORE phases rather than fewer
- Propose = present plan and WAIT for user approval before executing

---

## 11. Display Configuration

For real-time feedback during execution:
- streaming: true (see tokens as generated)
- tool_progress: "all" (see what tool is running)
- tool_preview_length: 80 (see tool arguments)
- show_reasoning: true (see chain-of-thought)

---

## 12. Modularity & Update Resilience

### Principle:
cobalt-routing is a PLUGIN — it must be isolated from Hermes internals and
survive non-breaking updates. If a breaking change occurs, it must WARN clearly
rather than silently fail.

### Dependency map:
| Dependency | Used for | Risk level | Fallback |
|------------|----------|------------|----------|
| `pre_tool_call` hook | Guard + routing + skills | LOW (stable plugin API) | Plugin won't load (loud error) |
| `ctx.register_tool` | cobalt_preset tool | LOW (stable plugin API) | Tool unavailable |
| `tools.registry` | Schema patching (task_type field) | MEDIUM (internal) | Schema unpatched, model infers without hint |
| `hermes_constants.get_hermes_home()` | Path resolution | LOW (utility) | Fallback to `~/.hermes` |
| `hermes_cli.runtime_provider` | Provider credential resolution | MEDIUM (internal) | Uses env vars / presets.yaml directly |
| **Source patch in delegate_tool.py** | _routed_model passthrough | **HIGH (invasive)** | Routing INACTIVE, uses default model |

### Guard rails:
1. `compat.py` checks Hermes version at load time → warns if untested, errors if incompatible
2. `verify_patch_applied()` checks source patch → logs WARNING if missing (graceful degradation)
3. All imports from Hermes internals are wrapped in try/except with meaningful fallbacks
4. Plugin uses only the public plugin API (hooks + register_tool) for core functionality
5. The source patch is the ONLY file modification to Hermes — everything else is additive

### On Hermes update:
- **Non-breaking**: Plugin continues working (hooks/API stable)
- **delegate_tool.py changed**: `verify_patch_applied()` detects it → WARNING logged → routing falls back to default model
- **Plugin API changed**: `register()` fails → Hermes logs error → agent works without plugin
- **New skill system**: If Hermes adds native skill injection for sub-agents, cobalt-routing's skill_injector becomes redundant (remove it)

### Design rule:
NEVER modify Hermes source files beyond the single delegate_tool.py patch.
All behavior is achieved via hooks, registered tools, and the plugin lifecycle.

---

## 13. Mid-Execution Steering (v0.5.0)

### Problem:
User sends a new message while an SDD plan is being executed. Without handling,
the orchestrator may create a second parallel plan or ignore the new instruction.

### Solution:
The `sdd_triage.py` hook detects if there's an active plan (by checking recent
conversation history for delegate_task or todo calls). If active:

- Injects STEERING variant instead of normal TRIAGE
- Forces classification: MODIFIES / EXTENDS / OVERRIDES / UNRELATED
- Orchestrator must explicitly state how the new message affects the plan
- Then: ANSWER → RE-PRIORITIZE → RESUME

### How it works with Hermes:
- Hermes native `/busy steer` delivers user messages mid-execution
- The message arrives as a new user turn after the next tool call completes
- Our pre_llm_call hook sees the new turn, detects active plan, injects steering
- Orchestrator classifies and adjusts — no new SDD cycle created unless OVERRIDES

### Detection of "active plan":
Checks last 10 messages in conversation_history for:
- `delegate_task` tool_use calls (execution in progress)
- `todo` tool_use calls (task breakdown active)
- NOT triggered if last action was `mcp_engram_mem_session_summary` (plan already closed)

---

## 14. Automatic App Versioning (v0.5.0)

### Purpose:
Track every execution cycle with artifacts for traceability and learning.

### Structure:
```
{project_root}/context/appVersions/
├── v0.1.0/
│   ├── original_prompt.md   ← Raw user prompt
│   ├── plan.md              ← SDD phases + tasks
│   └── changelog.md         ← Generated at close
├── v0.2.0/
│   └── ...
```

### Lifecycle:
1. **Init**: On first Apply delegation of an SDD cycle, `version_manager.init_version()` creates the folder and saves the original prompt
2. **Plan**: After triage + decomposition, save phases and tasks to plan.md
3. **Mid-session additions**: New user instructions appended to plan.md with timestamp
4. **Close**: After mcp_engram_mem_session_summary, generate changelog from completed tasks

### Version numbering:
- Auto-increments patch from latest existing version
- Major/minor bumps are manual (user specifies)
- First version: v0.1.0

### Integration:
- `version_manager.py` is pure stdlib (Path, datetime, re) — zero Hermes dependencies
- Does NOT replace Hermes `todo` tool — complements it with persistent artifacts
- Does NOT use PROGRESS.md — `todo` serves as the live state anchor

---

## 15. Metrics & Regression Control (v0.5.0)

Every test session measures:
- Context usage (<30% target)
- Model routing compliance (100%)
- Parallel utilization (independent tasks bundled)
- SDD triage present (every turn)
- Phases actually executed vs stated
- Tool guard compliance (0 sub-agent blocks)
- Skill injection accuracy
- Memory bookends (search at start, conclude at end)
- Code quality (runs without errors)

Regression = any WORKS item from the CHANGELOG that fails in a new version.
See `METRICS.md` for full parameter definitions and extraction methods.

---

## 16. Installation (Planned)

Automated script that:
1. Detects/installs Hermes Agent (tested version)
2. Applies source patch via `patches/apply_routing_patch.py apply`
3. Copies plugin to ~/.hermes/plugins/cobalt-routing/
4. Installs skills via native `hermes skills install`
5. Writes SOUL.md
6. Prompts user for: API keys, provider selection, platform
7. Writes config.yaml with Engram MCP server (when ENGRAM_CLOUD_* env vars present)
8. Runs verification

No other interaction needed. Fully unattended after API key input.
