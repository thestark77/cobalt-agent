# Plan v0.5.0 — Steering + Versioning + Control

## Changes from v0.4.0

### 1. SDD Triage: Remove threshold, always inject
- Remove `MIN_MESSAGE_LENGTH` check
- Triage fires on EVERY orchestrator turn, no exceptions
- Sub-agents still excluded

### 2. Mid-Execution Steering
- Detect if there's an active plan (via `todo` tool items in-progress)
- If active plan exists AND new message arrives → inject STEERING variant:
  "There is an active plan. Does this message: (a) modify it, (b) extend it, (c) override it? ANSWER → RE-PRIORITIZE → RESUME"
- Leverages Hermes native `/busy steer` for message delivery
- No PROGRESS.md — uses `todo` as state anchor

### 3. Automatic App Versioning (appVersions)
- On first SDD Apply phase: create `context/appVersions/vX.Y.Z/`
- Store: `original_prompt.md` (raw user prompt), `plan.md` (phases + tasks), `changelog.md` (generated at close)
- Version auto-increments from last known version
- Close version: sub-agent generates changelog from completed tasks
- Mid-session additions: append to plan.md with timestamp

### 4. Metrics & Regression Control
- METRICS.md defines measurable parameters per test
- Test report template with PASS/FAIL per metric
- Regression = any WORKS item from previous version that now fails
- Log extraction commands documented

---

## Implementation Order

| Step | What | Risk | Verification |
|------|------|------|-------------|
| 1 | Remove triage threshold | None | All messages get triage |
| 2 | Add steering detection to triage | Low | If todo has items → steering variant |
| 3 | Version init script | None | Creates folder structure |
| 4 | Hook version init into SDD flow | Medium | Triggers on first apply |
| 5 | Version close (changelog gen) | Low | Sub-agent writes changelog |
| 6 | Update FLOW.md, CHANGELOG, SOUL.md | None | Documentation |
| 7 | Push to repo | None | — |
| 8 | Run test prompt | — | Full metrics extraction |

---

## What NOT to do
- No PROGRESS.md (Hermes has `todo`)
- No feedback.md generation (adds complexity, defer to v0.6.0)
- No screenshots/ (not relevant for CLI)
- No compaction hook (Hermes manages this natively, we use honcho for persistence)
- No knowledge-graph integration (defer)

---

## Files to create/modify

| File | Action |
|------|--------|
| `src/sdd_triage.py` | Remove threshold, add steering variant |
| `src/version_manager.py` | NEW: version init, close, artifact storage |
| `src/__init__.py` | Register version hooks |
| `docs/FLOW.md` | Add sections 14 (steering) and 15 (versioning) |
| `CHANGELOG.md` | Add v0.5.0 entry |
| `METRICS.md` | Already created |
| `SOUL.md` | Add steering awareness |
