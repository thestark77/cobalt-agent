#!/usr/bin/env bash
# Installs OpenSpec-compatible SDD skills into Hermes global skills directory.
# Skills are adapted for Hermes (no openspec CLI required).
# Run this after updating cobalt-routing to activate auto-SDD skill routing.

set -euo pipefail

SKILLS_DIR="${HOME}/.hermes/skills"
OPENSPEC_COBALT_VERSION="0.9.0"

install_skill() {
  local name="$1"
  local description="$2"
  local body="$3"
  local dir="${SKILLS_DIR}/${name}"

  mkdir -p "${dir}"
  cat > "${dir}/SKILL.md" << FRONTMATTER
---
name: ${name}
description: ${description}
license: MIT
compatibility: Hermes Agent with cobalt-routing plugin
metadata:
  author: openspec (adapted for Hermes by cobalt-agent)
  version: "${OPENSPEC_COBALT_VERSION}"
---

${body}
FRONTMATTER
  echo "  ✓ ${name}"
}

echo "Installing OpenSpec SDD skills for Hermes (cobalt-agent v${OPENSPEC_COBALT_VERSION})..."

# ─── openspec-explore ────────────────────────────────────────────────────────

install_skill "openspec-explore" \
  "Execute the EXPLORE phase of an auto-SDD task. Think deeply, investigate the codebase, surface risks and unknowns. Never implement in this phase." \
  'You have been delegated to execute the **EXPLORE phase** of an SDD task.

Your role: thinking partner and codebase investigator. Read files, trace code, run tools —
but NEVER write code or modify files in explore mode. Capturing artifacts is allowed.

## Steps

1. **Recover prior context**
   Call `mcp_engram_mem_search` with the topic from your goal to find prior exploration,
   decisions, or related work. If found, build on it instead of starting from scratch.

2. **Investigate the codebase**
   - Map the relevant architecture (files, modules, boundaries)
   - Identify integration points and existing patterns
   - Surface hidden complexity, edge cases, and coupling
   - Use ASCII diagrams to visualize structure — they compress information better than prose

3. **Compare options (if the goal involves a decision)**
   Build comparison tables. State tradeoffs explicitly.
   Lead with a recommendation if one clearly emerges.

4. **Surface risks and unknowns**
   - What could break?
   - What is unclear or under-specified?
   - What needs a spike or investigation before implementation?

## Artifacts

When insights crystallize into a clear implementation path, create:
- `openspec/changes/<change-name>/proposal.md` — what to build and why (markdown)

Derive the change name from your goal (kebab-case). Use it consistently.

## Output format

```
## Exploration: <topic>

### Architecture map
[ASCII diagram or file list]

### Key findings
- ...

### Risks and unknowns
- ...

### Recommendation
[One clear path, with rationale]

### Next step
→ Ready to proceed to PROPOSE phase? [yes/offer to proceed]
```

**Guardrail**: If the user'"'"'s request is already scoped (change already proposed),
skip to reading `openspec/changes/<name>/proposal.md` for context before continuing.'

# ─── openspec-propose ────────────────────────────────────────────────────────

install_skill "openspec-propose" \
  "Execute the PROPOSE phase of an auto-SDD task. Create proposal.md, design.md, and tasks.md in openspec/changes/<name>/. Present to user and wait for approval." \
  'You have been delegated to execute the **PROPOSE phase** of an SDD task.

Your role: formalize exploration findings into concrete, reviewable artifacts.
Do NOT start implementing. Present to the user and wait for explicit approval.

## Steps

1. **Determine the change name**
   Extract from your goal or derive a kebab-case name from the task description.

2. **Read prior context**
   - Check engram: `mcp_engram_mem_search` with the change name
   - Read `openspec/changes/<change-name>/` if it exists (prior exploration artifacts)

3. **Create the artifact directory**
   ```bash
   mkdir -p openspec/changes/<change-name>/specs
   ```

4. **Write `openspec/changes/<change-name>/proposal.md`**
   Structure:
   ```markdown
   # Proposal: <change-name>

   ## Problem
   [What problem this solves and why it matters]

   ## Proposed solution
   [What will be built — scope, approach, non-goals]

   ## Success criteria
   [How we know this is done]
   ```

5. **Write `openspec/changes/<change-name>/design.md`**
   Structure:
   ```markdown
   # Design: <change-name>

   ## Architecture
   [Key components, their responsibilities, and how they interact]

   ## Key decisions
   | Decision | Choice | Rationale |
   |----------|--------|-----------|
   | ...      | ...    | ...       |

   ## Data flow
   [ASCII diagram if relevant]
   ```

6. **Write `openspec/changes/<change-name>/tasks.md`**
   Structure:
   ```markdown
   # Tasks: <change-name>

   ## Implementation tasks
   - [ ] Task 1 — [short description]
   - [ ] Task 2 — [short description]
   ...

   ## Verify tasks
   - [ ] Verify: [what to check]
   ```
   Keep tasks atomic (one file or one concern per task). No mega-tasks.

7. **Save to engram**
   Call `mcp_engram_mem_save` with:
   - title: "Proposal: <change-name>"
   - type: "architecture"
   - content: summary of proposal + artifact paths

8. **Present to user and WAIT**
   Summarize the three artifacts. Ask: "Does this proposal look right? Proceed with implementation?"
   Do NOT delegate the apply phase until the user confirms.

## Guardrails
- WAIT for user approval before proceeding to Apply
- Keep tasks.md granular — if a task takes more than ~30 min, split it
- If the goal is a bugfix (no design needed), create only proposal.md + tasks.md'

# ─── openspec-apply-change ───────────────────────────────────────────────────

install_skill "openspec-apply-change" \
  "Execute the APPLY phase of an auto-SDD task. Read tasks.md, implement each task, mark checkboxes as complete. Never verify in this phase." \
  'You have been delegated to execute the **APPLY phase** of an SDD task.

Your role: implement tasks from `tasks.md`, one at a time, marking each complete.
Do NOT run the test suite or verify in this phase — that is a separate delegation.

## Steps

1. **Identify the change**
   Extract the change name from your goal. If unclear, search engram:
   `mcp_engram_mem_search` for recent proposals.

2. **Read context artifacts** (in this order)
   - `openspec/changes/<change-name>/proposal.md` — what we'"'"'re building
   - `openspec/changes/<change-name>/design.md` — architecture decisions (if exists)
   - `openspec/changes/<change-name>/specs/` — any spec files (if exists)
   - `openspec/changes/<change-name>/tasks.md` — THE TASK LIST

3. **Check for prior apply progress**
   Search engram: `mcp_engram_mem_search` with "apply-progress <change-name>".
   If found, read which tasks are already done and resume from the next pending one.

4. **Implement tasks (loop)**
   For each `- [ ]` task in tasks.md:
   - Announce: "Working on: [task description]"
   - Implement the change (edit files, write code)
   - After completing: update the checkbox to `- [x]` in tasks.md
   - Pause if: task is ambiguous, a blocker appears, or implementation reveals a design issue

5. **Save progress to engram** after each task or on pause
   `mcp_engram_mem_save` with type "discovery", topic_key "apply-progress/<change-name>"

6. **On completion**
   Report: "N/N tasks complete. Ready for VERIFY phase."
   Do NOT run tests — that is the verify sub-agent'"'"'s responsibility.

## Output format
```
## Applying: <change-name>

▶ Task 1/N: <description>
  [changes made]
  ✓ Done

▶ Task 2/N: <description>
  ...

## Summary
- N/N tasks complete
- Files modified: [list]
→ Ready for VERIFY phase
```

## Guardrails
- One task at a time — do not batch
- Mark the checkbox IMMEDIATELY after completing the task, before starting the next
- If a task conflicts with the design.md, PAUSE and surface the conflict
- Never skip tasks — if stuck, surface the blocker'

# ─── openspec-verify-change ──────────────────────────────────────────────────

install_skill "openspec-verify-change" \
  "Execute the VERIFY phase of an auto-SDD task. Check completeness, correctness against specs, and design coherence. Report CRITICAL/WARNING/SUGGESTION." \
  'You have been delegated to execute the **VERIFY phase** of an SDD task.

Your role: validate that implementation matches the change artifacts. Report issues
in three tiers. Never implement fixes in this phase — report and let the user decide.

## Steps

1. **Load all artifacts**
   Read from `openspec/changes/<change-name>/`:
   - `proposal.md` — success criteria
   - `design.md` — architecture decisions (if exists)
   - `specs/` — any spec files (if exist)
   - `tasks.md` — task list with checkboxes

2. **Dimension 1: Completeness**
   - Count `- [ ]` vs `- [x]` in tasks.md
   - If any incomplete tasks: CRITICAL issue for each
   - Check that every requirement in specs/ has corresponding implementation evidence

3. **Dimension 2: Correctness**
   - For each requirement in specs/ (if present): search codebase for implementation
   - If requirement appears unimplemented: CRITICAL issue
   - If implementation diverges from spec intent: WARNING issue

4. **Dimension 3: Coherence**
   - If design.md exists: check that key decisions were followed in implementation
   - If design decision was contradicted: WARNING issue
   - Check for obvious pattern inconsistencies vs the rest of the project: SUGGESTION

5. **Generate report**

## Output format

```
## Verification Report: <change-name>

| Dimension    | Status                |
|--------------|-----------------------|
| Completeness | N/M tasks, K reqs     |
| Correctness  | M/N reqs implemented  |
| Coherence    | Followed / N warnings |

### CRITICAL (must fix before archive)
- [ ] [issue description] — Recommendation: [specific action]

### WARNING (should fix)
- [ ] [issue description] — Recommendation: [specific action]

### SUGGESTION (consider)
- [ ] [issue description] — Recommendation: [specific action]

### Assessment
[PASS / NEEDS FIXES] — [one line summary]
```

## Guardrails
- Every issue must have a specific recommendation with file:line where possible
- When uncertain: prefer SUGGESTION over WARNING, WARNING over CRITICAL
- If only tasks.md exists (no specs/design): verify completeness only, note what was skipped
- Do NOT implement fixes — report them'

# ─── openspec-archive-change ─────────────────────────────────────────────────

install_skill "openspec-archive-change" \
  "Execute the ARCHIVE phase of an auto-SDD task. Confirm completion, move artifacts to archive/, persist session summary to engram." \
  'You have been delegated to execute the **ARCHIVE phase** of an SDD task.

Your role: close the change. Verify completion, move artifacts to the archive directory,
and persist the session summary to engram.

## Steps

1. **Confirm all tasks are complete**
   Read `openspec/changes/<change-name>/tasks.md`.
   Count incomplete tasks (`- [ ]`). If any remain, WARN and ask for confirmation.

2. **Check verify status**
   Search engram: `mcp_engram_mem_search` for "verify <change-name>".
   If no verification was run, WARN: "VERIFY phase was not run. Archive anyway?"
   Wait for confirmation before continuing.

3. **Archive the artifacts**
   ```bash
   # Create archive dir if needed
   mkdir -p openspec/changes/archive

   # Archive with date prefix
   DATE=$(date +%Y-%m-%d)
   mv openspec/changes/<change-name> openspec/changes/archive/${DATE}-<change-name>
   ```

   If target already exists (duplicate date), append `-2`, `-3`, etc.

4. **Persist session summary to engram**
   Call `mcp_engram_mem_session_summary` with:
   - Goal: what was built
   - Accomplished: completed tasks summary
   - Relevant files: key files modified
   - Next steps: any known follow-up work

5. **Confirm to user**

## Output format
```
## Archive Complete: <change-name>

**Archived to:** openspec/changes/archive/YYYY-MM-DD-<change-name>/
**Tasks:** N/N complete
**Verify:** [ran / skipped with confirmation]
**Memory:** session summary saved to engram

Change closed. ✓
```

## Guardrails
- Always confirm incomplete tasks or missing verify BEFORE archiving
- Never delete — always move (mv, not rm)
- mcp_engram_mem_session_summary is MANDATORY before closing'

# ─── Invalidate Hermes skill cache ───────────────────────────────────────────

CACHE_FILE="${HOME}/.hermes/.skills_prompt_snapshot.json"
if [ -f "${CACHE_FILE}" ]; then
  rm -f "${CACHE_FILE}"
  echo "  ✓ Hermes skill cache invalidated"
fi

echo ""
echo "OpenSpec SDD skills installed successfully."
echo "Skills available after next Hermes session start:"
echo "  openspec-explore, openspec-propose, openspec-apply-change,"
echo "  openspec-verify-change, openspec-archive-change"
