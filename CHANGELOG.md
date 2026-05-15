# Changelog — cobalt-agent

Format: Each version lists WORKS (must not regress), BROKEN (known issues), and CHANGES.

---

## v0.9.0 (2026-05-16) — OpenSpec SDD Skills + Auto-SDD Routing

### WORKS (must not regress from v0.8.0)
- [x] SDD triage injected on every orchestrator turn
- [x] Triage classification: CONVERSATION → direct response (no SDD), EXECUTION TASK → full SDD pipeline
- [x] Phases stated match actual delegations
- [x] Active plan detected → steering variant injected
- [x] Model routing works (no regression)
- [x] Tool guard works (no regression)
- [x] Memory protocol works (no regression)
- [x] OpenSpec skill directives in delegation goals → correct task_type via existing verb detection (no extra dict needed)

### NEW
- [x] 5 OpenSpec-compatible SDD skills installed to `~/.hermes/skills/`:
  `openspec-explore`, `openspec-propose`, `openspec-apply-change`,
  `openspec-verify-change`, `openspec-archive-change`
- [x] `sdd_triage.py`: SKILL ROUTING block — orchestrator automatically includes
  `skill_view('<openspec-*>')` directive in delegation goals for each SDD phase
- [x] `scripts/install_openspec_skills.sh` — standalone installer for SDD skills
  (idempotent, cache-invalidating, no npm/CLI dependency)

### BROKEN (known issues — pending v0.9.0 test run)
- [ ] OpenSpec skills reference `openspec/changes/<name>/` file structure — requires
  project to have that directory tree (created by openspec-propose); graceful if missing
- [ ] Skill routing directive in triage is prose-based — depends on orchestrator
  correctly reading `<available_skills>` block; no mechanical fallback

### CHANGES
- `src/router.py`: removed fragile `_OPENSPEC_SKILL_TO_TASK_TYPE` keyword dict; existing verb-based `_infer_task_type()` handles skill-enriched goals natively
- `src/sdd_triage.py`: added SKILL ROUTING block inside EXECUTION TASK branch
- `src/config.py`: new (from v0.8.0 — was missing from repo)
- `src/utils.py`: new (from v0.8.0 — was missing from repo)
- `src/__init__.py` + `src/plugin.yaml`: version bumped to 0.9.0
- `scripts/install_openspec_skills.sh`: new — installs adapted OpenSpec SDD skills

---

## v0.8.0 (2026-05-14) — Patch Drift Monitoring + Engram Memory Protocol

### CHANGES
- Replaced Honcho memory backend with Engram (MCP-based, 19 tools)
- Added patch drift monitoring: GitHub Action + VPS cron + Telegram alerts
- Added `config.py` and `utils.py` modules
- Memory protocol is now rule-based (deterministic, not LLM-decision-based)
- SOUL.md updated with Engram protocol and memory rules

---

## v0.5.0 (2026-05-04) — SDD Triage + Steering + Versioning + Metrics

### WORKS (to verify in test)
- [ ] SDD triage injected on every orchestrator turn
- [ ] Triage classification present in orchestrator's first response line
- [ ] Phases stated match actual delegations executed
- [ ] Active plan detected → steering variant injected
- [ ] Model routing still works (no regression from v0.4.0)
- [ ] Tool guard still works (no regression)
- [ ] Skill injection still works (no regression)
- [ ] Curation suffixes still present (no regression)
- [ ] Memory bookends (honcho_search + honcho_conclude)
- [ ] Version artifacts created (context/appVersions/)

### BROKEN (known issues — pending test)
- [ ] Steering detection relies on conversation_history kwarg (needs verification that Hermes passes it)
- [ ] Version manager not yet hooked into delegation flow (manual trigger only)
- [ ] appVersions dir creation depends on project root detection (may fail outside git repos)

### CHANGES
- Removed MIN_MESSAGE_LENGTH threshold from triage (always injects)
- Added steering variant when active plan detected
- Created version_manager.py (init, save_plan, append, close)
- Created METRICS.md (measurable parameters + regression detection)
- Updated SOUL.md with steering awareness
- Updated FLOW.md sections 13-15 (steering, versioning, metrics)
- All new modules use only stdlib (zero Hermes imports)

---

## v0.4.0 (2026-05-03) — Skill Injection + Native Install

### WORKS (confirmed functional)
- [x] All v0.3.0 WORKS items (no regressions)
- [x] Skill injection: pattern-matches goal → skill name(s)
- [x] Skill injection: appends instruction to sub-agent goal (not content)
- [x] Sub-agent loads full skill via skill_view in its own context
- [x] Orchestrator context stays clean (never reads skill content)
- [x] Max 2 skills per delegation enforced
- [x] task_type affinity: spec/design → prompt-engineering, verify → e2e-testing
- [x] Graceful: no match → no injection, missing skill → skipped
- [x] All 10 skills installed via native `do_install` with provenance tracking
- [x] .hub/lock.json tracks source, identifier, trust_level, content_hash
- [x] Skills updatable via `hermes skills install <id> --force`

### BROKEN (known issues)
- [ ] Orchestrator does NOT parallelize independent scouts (inherited from v0.3.0)
- [ ] Display shows nothing during execution (inherited from v0.3.0 — config ready but untested)
- [ ] K2.6 does not set task_type explicitly (inherited)
- [ ] Skill injection untested in live session (needs v0.4.0 test run)
- [ ] postgresql skill registered as "postgresql" not "postgresql-table-design" (naming mismatch in routes vs lock)

### CHANGES
- Created skill_injector.py (pattern matching + goal instruction)
- Integrated skill injection into _inject_routing pipeline
- Removed 10 statically-copied skills from ~/.hermes/skills/
- Reinstalled all 10 via native `do_install` (skills.sh source, security-scanned)
- Plugin version bumped to 0.4.0
- Skills installed: prompt-engineering-patterns, frontend-design, interface-design, e2e-testing-patterns, error-handling-patterns, postgresql, judgment-day, branch-pr, skill-creator, knowledge-graph

---

## v0.3.0 (2026-05-03) — First Real Test

### WORKS (confirmed functional)
- [x] Tool Guard blocks orchestrator from using execution tools
- [x] Tool Guard allows sub-agents (sa- prefix) unrestricted access
- [x] Model routing: scout→flash, explore→flash, verify→pro (confirmed in logs)
- [x] task_type auto-inference from goal keywords (fallback when not explicit)
- [x] Curation suffixes inject response format into sub-agent goals
- [x] Honcho memory: prefetch mechanical (auto before every turn)
- [x] Honcho memory: semantic search cross-language (EN↔ES)
- [x] Honcho memory: conclusions persist across sessions
- [x] honcho_search/conclude tools accessible to orchestrator
- [x] K2.6 respects delegation (uses delegate_task, not direct tools)
- [x] Context efficiency: 14% used after 43min task (no compressions)
- [x] health_check.py verifies OpenCode Go, Honcho, cobalt-routing
- [x] Plugin graceful degradation (remove plugin → Hermes works normal)
- [x] Source patch graceful (no _routed_model → uses default model)

### BROKEN (known issues)
- [ ] task_type inference maps "Crear/escribir" → scout instead of apply
- [ ] Orchestrator does NOT parallelize independent scouts (sequential despite SOUL.md rule)
- [ ] Scout timeout 900s too long (sub-agent got stuck 15min on web search)
- [ ] Display shows nothing during execution (streaming/progress disabled)
- [ ] Skills NOT injected into sub-agents (Hermes native gap)
- [ ] No adaptive timeout per task_type (all use child_timeout_seconds=900)
- [ ] K2.6 does not set task_type explicitly (relies on auto-inference)
- [ ] hermes binary not in PATH for health_check.py CLI calls

### CHANGES (what was done)
- Created cobalt-routing plugin v0.3.0 (__init__.py, tool_guard.py, router.py, presets.yaml, compat.py)
- Applied source patch to delegate_tool.py (5 fields: model, provider, base_url, api_key, api_mode)
- Fixed tool_guard sa- prefix (was "subagent-", now ("sa-", "subagent-"))
- Configured Honcho memory provider (honcho.json + config.yaml provider: honcho)
- Created SOUL.md v3 with absolute rules + procedure
- Added Honcho tools to ORCHESTRATOR_ALLOWED (5 tools)
- Created health_check.py (421 lines, stdlib only)

### TEST SESSION: 20260503_212442_c5c190
- Duration: 43min 44sec
- Delegations: 5 (scout, explore-timeout, verify-retry, apply, verify-final)
- Context: 37.1k/262.1k (14%) — no compressions
- Models used: K2.6 (orchestrator), flash (scout/explore), pro (verify)
- Outcome: Script created and working (3/3 checks pass when hermes in PATH)

---

## v0.2.1 (2026-05-03) — Tool Guard Fix

### CHANGES
- Fixed critical bug: sub-agents blocked by tool guard
- Root cause: task_id prefix was "subagent-" but Hermes uses "sa-{index}-{uuid}"
- Added ("sa-", "subagent-") tuple for prefix detection

---

## v0.2.0 (2026-05-03) — Tool Guard Added

### CHANGES
- Added tool_guard.py with mechanical enforcement
- Unified pre_tool_call hook (guard + routing)
- SOUL.md v2 with ABSOLUTE RULE section

### BROKEN
- Sub-agents were blocked (prefix bug) — fixed in v0.2.1

---

## v0.1.0 (2026-05-03) — Initial Plugin

### CHANGES
- Created cobalt-routing plugin structure
- Implemented model routing via presets (economy/balanced/quality)
- Source patch applied to delegate_tool.py
- K2.6 as orchestrator, flash for scouts, pro for reasoning
