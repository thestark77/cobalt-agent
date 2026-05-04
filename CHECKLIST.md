# Test Checklist — cobalt-agent

Run this checklist after every version change. Mark PASS/FAIL with date.

---

## Infrastructure

- [ ] `python3 health_check.py` — OpenCode Go OK
- [ ] `python3 health_check.py` — Honcho OK
- [ ] `python3 health_check.py` — cobalt-routing OK
- [ ] `hermes doctor` — no errors
- [ ] `hermes plugins list` — cobalt-routing enabled

## Tool Guard

- [ ] Orchestrator CANNOT call web_search (gets BLOCKED message)
- [ ] Orchestrator CAN call delegate_task
- [ ] Orchestrator CAN call honcho_search / honcho_conclude
- [ ] Sub-agents CAN call any tool (web_search, write_file, etc.)
- [ ] No BLOCKED entries in agent.log for sub-agents (sa- prefix)

## Model Routing

- [ ] Scout delegation → deepseek-v4-flash (check agent.log)
- [ ] Explore delegation → deepseek-v4-flash
- [ ] Apply delegation → kimi-k2.6
- [ ] Verify delegation → deepseek-v4-pro
- [ ] Design delegation → deepseek-v4-pro

## task_type Inference

- [ ] "Buscar/encontrar/search" → scout
- [ ] "Crear/escribir/implementar/code" → apply
- [ ] "Verificar/testear/validate" → verify
- [ ] "Leer/analizar/read" → explore
- [ ] "Diseñar/architecture" → design

## Memory (Honcho)

- [ ] Prefetch fires automatically (check agent.log for "Honcho session")
- [ ] honcho_search returns relevant results
- [ ] honcho_conclude saves successfully
- [ ] Cross-language search works (save EN, search ES)

## Curation

- [ ] Scout sub-agent returns structured response (not raw dump)
- [ ] Sub-agent includes "## Discarded" section
- [ ] Response respects word limit (~400 words for scout)

## Parallelism

- [ ] 2+ independent scouts launch in same turn (check timestamps in log)
- [ ] Dependent tasks are sequential (scout → apply)
- [ ] Max 3 concurrent children respected

## Skill Injection (when implemented)

- [ ] Frontend goal gets frontend-design skill injected
- [ ] Test goal gets e2e-testing-patterns injected
- [ ] Max 2 skills per delegation
- [ ] No injection when no skill matches (graceful)

## Display/UX

- [ ] User sees tool names as they execute (tool_progress)
- [ ] User sees partial token output (streaming)
- [ ] Reasoning visible if enabled
- [ ] Error messages are clear and actionable

## Context Efficiency

- [ ] Orchestrator context stays below 30% after standard task
- [ ] No unnecessary compressions
- [ ] Sub-agent summaries are concise (curation working)

## SDD Phases (for complex tasks)

- [ ] Init: stack/conventions detected
- [ ] Explore: scouts gather necessary info
- [ ] Propose: orchestrator presents approach to user
- [ ] Spec: requirements written (Given/When/Then)
- [ ] Design: technical architecture documented
- [ ] Tasks: atomic breakdown via todo tool
- [ ] Apply: code written by sub-agent
- [ ] Verify: tests run and pass
- [ ] Archive: learnings saved to Honcho

---

## How to Report

After running checklist, create a file in `logs/vX.Y.Z/` with:
```
Date: YYYY-MM-DD
Session: session_id
Duration: Xmin Ys
Checklist: X/Y passed
Issues found: [list]
```
