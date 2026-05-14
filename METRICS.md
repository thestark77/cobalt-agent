# Test Metrics — cobalt-agent

Every test session is measured against these parameters. Results are stored in `logs/vX.Y.Z/`.
A metric that PASSES in version N and FAILS in version N+1 is a **regression**.

---

## Measurable Parameters

### 1. Cost Efficiency
| Metric | Target | How to measure |
|--------|--------|----------------|
| Orchestrator context usage | <30% after task | Check context report at end |
| Model routing compliance | 100% tasks routed correctly | agent.log: scout→flash, apply→mid, verify→pro |
| Orchestrator token ratio | <20% of total session tokens | Compare orchestrator vs sub-agent token usage |
| Unnecessary re-delegations | 0 | Count delegate_task calls that repeat same goal |

### 2. Time Efficiency
| Metric | Target | How to measure |
|--------|--------|----------------|
| Total execution time | <15min for simple, <30min for medium | Session duration in logs |
| Parallel utilization | Independent tasks bundled in same turn | Timestamps of delegate_task calls |
| Scout timeout | <300s per scout | agent.log duration per sub-agent |
| No idle gaps | <30s between delegations | Timestamp analysis |

### 3. SDD Compliance
| Metric | Target | How to measure |
|--------|--------|----------------|
| Triage present | 100% of turns | Look for "TASK:" or "CONVERSATION:" in first response line |
| Phases executed | All selected phases actually run | Match stated phases vs actual delegations |
| Propose before Apply | If Propose selected, user was consulted | Check for user interaction before apply delegations |
| Memory bookends | mem_search at start, mem_session_summary at end | agent.log tool calls |

### 4. Mechanical Enforcement
| Metric | Target | How to measure |
|--------|--------|----------------|
| Tool guard | 0 BLOCKED for sub-agents | agent.log grep "BLOCKED" |
| Tool guard | All forbidden calls blocked for orchestrator | Verify orchestrator never calls web_search etc |
| Skill injection | Correct skill matched when applicable | agent.log "skill instruction" entries |
| task_type inference | Correct type for each delegation | Compare inferred type vs goal content |
| Curation suffix | Present on scout/explore/verify goals | Check sub-agent goals in session JSON |

### 5. Quality
| Metric | Target | How to measure |
|--------|--------|----------------|
| Code runs | Script/code executes without errors | verify delegation result |
| Requirements met | All stated requirements in prompt satisfied | Manual check against prompt |
| Error handling | Graceful failures where specified | Test edge cases |
| Sub-agent response quality | Structured, concise, with Discarded section | Check sub-agent responses |

---

## Regression Detection

After every test:
1. Run metrics extraction (from agent.log + session JSON)
2. Compare against CHANGELOG WORKS section for current version
3. Compare against previous version metrics
4. If any WORKS item now fails → **REGRESSION** → must fix before next version

### Regression Severity
- **CRITICAL**: Tool guard bypass, orchestrator executing directly, wrong model used
- **HIGH**: SDD triage missing, phases skipped, no parallelism
- **MEDIUM**: Skill not injected, curation missing, memory not saved
- **LOW**: Suboptimal timing, verbose responses, minor routing misses

---

## Test Session Report Template

```markdown
# Test: vX.Y.Z — Session YYYYMMDD_HHMMSS

## Prompt
> (the test prompt)

## Metrics
| Parameter | Target | Actual | Status |
|-----------|--------|--------|--------|
| Context usage | <30% | X% | PASS/FAIL |
| Execution time | <Xmin | Ymin | PASS/FAIL |
| Triage present | yes | yes/no | PASS/FAIL |
| Phases stated | X,Y,Z | A,B,C | PASS/FAIL |
| Parallel scouts | bundled | yes/no | PASS/FAIL |
| Model routing | correct | X/Y correct | PASS/FAIL |
| Tool guard | 0 blocks for sa- | N blocks | PASS/FAIL |
| Skill injection | expected: X | got: Y | PASS/FAIL |
| Memory bookends | search+conclude | yes/no | PASS/FAIL |
| Code works | runs clean | yes/no | PASS/FAIL |

## Regressions (vs previous version)
- (list any WORKS items that now fail)

## New Issues Found
- (list any new problems discovered)

## Verdict: PASS / PARTIAL / FAIL
```

---

## How to Extract Metrics

From agent.log:
```bash
# Model routing
grep "cobalt-routing.*type=.*model=" agent.log

# Tool guard blocks
grep "BLOCKED" agent.log

# Skill injection
grep "skill instruction" agent.log

# Delegation timestamps (parallelism check)
grep "delegate_task" agent.log | awk '{print $1}'

# Sub-agent durations
grep "subagent.*completed\|sa-.*duration" agent.log
```

From session JSON:
```python
# Extract triage classification from first assistant response
# Extract task_types from delegate_task calls
# Extract goals to verify curation suffixes
# Count mem_search and mem_session_summary calls
```
