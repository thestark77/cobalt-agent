# cobalt-agent

Autonomous orchestration layer for [Hermes Agent](https://github.com/NousResearch/hermes-agent). One command installs everything — model routing, tool enforcement, skill injection, SDD triage, and curated configuration.

**Works as installer AND updater.** Run the same command to set up from scratch or upgrade an existing installation.

```bash
git clone https://github.com/thestark77/cobalt-agent.git && cd cobalt-agent && bash install.sh
```

On Windows:
```powershell
git clone https://github.com/thestark77/cobalt-agent.git; cd cobalt-agent; .\install.ps1
```

---

## Table of Contents

- [Philosophy](#philosophy)
- [Requirements](#requirements)
- [Installation](#installation)
- [How It Works](#how-it-works)
- [Decision Flow](#decision-flow)
- [Five Mechanisms](#five-mechanisms)
- [Model Routing](#model-routing)
- [Test Results](#test-results)
- [Working with AI Agents](#working-with-ai-agents)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Version Compatibility](#version-compatibility)
- [License](#license)

---

## Philosophy

Hermes Agent is a powerful orchestrator, but out of the box it:

- Uses the same model for every sub-agent (expensive, slow)
- Lets the orchestrator execute tools directly (breaks delegation)
- Doesn't inject domain knowledge into sub-agents (generic responses)
- Has no structured triage (starts working before classifying the problem)

cobalt-agent fixes all four through a **hook-based plugin** — no forks, no core modifications beyond a single reversible source patch.

The design principles:

1. **Non-invasive** — One source patch + external plugin. Everything else is additive.
2. **Update-resilient** — Detects breaking changes, warns on untested versions, errors on incompatible ones. SOUL.md uses tagged sections so user additions survive every update.
3. **Replicable** — Single command installs the complete environment from scratch.
4. **Modular** — Each mechanism (routing, guard, skills, triage, timeout) is independent.

---

## Requirements

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.11+ | Required by Hermes |
| git | any | For cloning repos |
| curl | any | For remote install |
| pip | any | Python package manager |
| python3-venv | any | Required for virtual environments (`sudo apt install python3.X-venv`) |
| npm | optional | For OpenCode CLI (free model provider) |
| WSL | 2.0+ | Windows only — Hermes requires Linux |

---

## Installation

### Fresh Install

```bash
git clone https://github.com/thestark77/cobalt-agent.git
cd cobalt-agent
bash install.sh
```

The installer runs 10 steps autonomously:

1. **Prerequisites** — Checks Python 3.11+, git, curl, pip
2. **Hermes Agent** — Clones and installs in `~/.hermes/hermes-agent/`
3. **OpenCode Go** — Installs free model provider (kimi-k2.6, deepseek-v4)
4. **Source Patch** — Applies routing hook to `delegate_tool.py` (reversible)
5. **Plugin** — Deploys cobalt-routing to `~/.hermes/plugins/` (routing + tool guard + triage + memory protocol)
6. **Configuration** — SOUL.md, config.yaml, Engram MCP server wiring
7. **Skills** — Installs curated domain skills
8. **SDD Skills** — Installs 5 OpenSpec-compatible SDD phase skills to `~/.hermes/skills/`
9. **Patch verify automation** — Daily cron job with Telegram alerts when Hermes drifts the patch
10. **Verification** — 6-point check (binary, plugin, patch, SOUL, config, version)

### Required configuration (the only thing you need to set)

```bash
# Engram Cloud backend (memory)
export ENGRAM_CLOUD_SERVER="https://your-engram.host"
export ENGRAM_CLOUD_TOKEN="your-token"
export ENGRAM_CLOUD_AUTOSYNC=1            # optional, recommended

# Telegram alerts on patch drift (optional but recommended on a VPS)
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"

# Skip cron job entirely (off by default)
export COBALT_INSTALL_CRON=0              # only if you don't want the cron

bash install.sh
```

The installer detects these env vars and wires everything. Everything else is unattended.

### Update

Run the exact same command. The installer detects existing installations and switches to update mode:

- Updates Hermes Agent to latest tested version
- Re-applies source patch (idempotent)
- Replaces plugin files with latest version
- Merges config without overwriting your settings
- Preserves credentials (provider auth, Engram tokens, Telegram tokens)

### Windows

```powershell
.\install.ps1                              # Default WSL distribution
.\install.ps1 -Distribution "Ubuntu-24.04" # Specific distribution
```

### After Installation

```bash
# Start Hermes
hermes chat
```

If you skipped the Engram env vars on first run, export them and re-run `bash install.sh` — the installer is idempotent and will simply wire the missing pieces (Engram MCP, cron) without touching anything else.

### Customizing SOUL.md

`~/.hermes/SOUL.md` is split into two sections:

```
<!-- cobalt:managed:start — managed by install.sh, do not edit between these markers -->
[cobalt rules — updated automatically on every install.sh run]
<!-- cobalt:managed:end -->

<!-- ── YOUR CUSTOM INSTRUCTIONS ── -->
Add persona, business logic, language preferences, domain knowledge here.
This section is NEVER modified by install.sh updates. Safe to edit freely.
```

Add anything below the closing `<!-- cobalt:managed:end -->` tag. On the next `bash install.sh`, only the managed block is replaced — your additions survive untouched.

**Migration**: if you have an existing `~/.hermes/SOUL.md` without the tags (installed before v0.9.0), the installer backs it up to `SOUL.md.bak` and deploys the tagged version. Move your custom content below the closing tag afterwards.

### Per-Project Context

Hermes loads all configuration globally from `~/.hermes/`. To give project-specific instructions without polluting the global config, place a `CONTEXT.md` file in the root of your project:

```bash
cp cobalt-agent/templates/CONTEXT.md ~/my-project/CONTEXT.md
# Edit it with your project's stack, rules, and commands
```

Hermes will automatically read it at the start of every session when launched from that directory. See `templates/CONTEXT.md` for the full template.

---

## How It Works

```
User Prompt
    |
    v
[ORCHESTRATOR] ── SOUL.md rules ── "you NEVER call tools directly"
    |
    |── Step 0: TRIAGE (pre_llm_call hook)
    |   Classify: CONVERSATION or TASK?
    |   Select SDD phases: explore → propose → apply → verify → archive
    |
    |── Step 1: MEMORY
    |   mcp_engram_mem_search for prior context (Engram)
    |
    |── Step 2: DECOMPOSE
    |   Break into independent concerns
    |
    |── Step 3: DELEGATE (pre_tool_call hook fires here)
    |   |
    |   |── Tool Guard: is this delegate_task? If not, BLOCK.
    |   |── task_type: explicit from model OR inferred from goal
    |   |── Model Router: task_type → model assignment
    |   |── Skill Injector: task_type → relevant skills in goal
    |   |── Dynamic Timeout: task_type → timeout value
    |   |── Curation Suffix: task_type → response format instructions
    |   |
    |   v
    |   [SUB-AGENT] ── runs with routed model, injected skills
    |   Returns structured response to orchestrator
    |
    |── Step 4: SYNTHESIZE
    |   Collect results, present to user
    |
    v
[RESPONSE]
```

---

## Decision Flow

How the pre_tool_call hook processes a `delegate_task` call:

```
delegate_task called
    |
    |── Is tool_name == "delegate_task"?
    |   NO → check Tool Guard → allow or block
    |   YES ↓
    |
    |── Has task_type?
    |   YES → use it directly
    |   NO → infer from goal:
    |       1. Check first 120 chars for verify verbs
    |       2. Check first 30 chars for creation verbs (leading)
    |       3. Score full goal against keyword dictionary
    |       4. Fallback → "explore"
    |
    |── Resolve routing (task_type → model from presets.yaml)
    |   scout/explore/summarize → deepseek-v4-flash (fast, cheap)
    |   apply/archive           → kimi-k2.6 (mid-tier, balanced)
    |   design/spec/tasks/verify/propose → deepseek-v4-pro (reasoning)
    |
    |── Inject _routed_model, _routed_provider into task dict
    |── Inject skill instructions into goal
    |── Set dynamic timeout via HERMES_CHILD_TIMEOUT env var
    |── Append curation suffix (response format)
    |
    |── Convert single→batch format if needed
    |   (args with "goal" → args with "tasks": [{...}])
    |
    v
    Pass modified args to Hermes
```

---

## Five Mechanisms

### 1. Tool Guard

Blocks the orchestrator from calling execution tools directly. Only `delegate_task`, `memory`, `todo`, `skills_list`, `skill_view`, and communication tools are allowed. Everything else returns a block directive forcing delegation.

### 2. Model Routing

Maps `task_type` to the optimal model. Cheap models for exploration, expensive models for reasoning. The orchestrator schema is patched to make `task_type` a REQUIRED field — a mechanical fix for the XGrammar constrained decoding issue (sglang #12932) where optional parameters get dropped at the token generation level.

### 3. Auto-SDD Skill Routing

When the orchestrator classifies a request as an EXECUTION TASK and delegates a sub-agent for a specific SDD phase, it automatically includes a `skill_view('<openspec-*>')` directive in the delegation goal. Sub-agents then invoke the matching OpenSpec-compatible skill for structured phase guidance (explore, propose, apply-change, verify-change, archive-change). No user instruction required — the routing happens mechanically via the triage injection.

OpenSpec skill signals in goal text also feed directly into task_type inference: if a goal contains `openspec-verify-change`, the router immediately returns `verify` without running heuristics.

### 4. SDD Triage (auto-SDD)

Forces the orchestrator to classify every input before acting. Injects a `[MANDATORY TRIAGE]` block via `pre_llm_call` that requires explicit phase selection (explore, propose, apply, verify, archive). Classification is binary: **CONVERSATION** (respond directly, no SDD) or **EXECUTION TASK** (full SDD pipeline). The orchestrator is intelligent enough to distinguish a question from a task — SDD is default behavior for work, not forced on every message.

### 5. Dynamic Timeout

Sets per-task timeout via `HERMES_CHILD_TIMEOUT` environment variable. Scout tasks get shorter timeouts, apply tasks get longer ones. Prevents cheap exploration tasks from consuming expensive context.

---

## Model Routing

Three tiers via OpenCode Go (free):

| Tier | task_type | Model | Use Case |
|---|---|---|---|
| Fast | scout, explore, summarize | deepseek-v4-flash | Search, read, analyze |
| Mid | apply, archive | kimi-k2.6 | Write code, implement |
| Reasoning | design, spec, tasks, verify, propose | deepseek-v4-pro | Architecture, testing, decisions |

Configured in `src/presets.yaml`. The "balanced" preset is active by default.

---

## Test Results

17 tests across 5 versions, measuring routing accuracy, delegation compliance, skill injection, and SDD triage. v0.9.0 achieved 4 consecutive runs at 100% (iter7–iter10):

| # | Version | Score | Duration | Tokens | Sub-agents | Models Used |
|---|---|---|---|---|---|---|
| 1 | v0.3.0 | 85% | 4m 12s | ~18k | 3 | kimi-k2.6 only |
| 2 | v0.3.0 | 80% | 3m 45s | ~15k | 2 | kimi-k2.6 only |
| 3 | v0.3.0 | 75% | 5m 03s | ~22k | 4 | kimi-k2.6 only |
| 4 | v0.5.0 | 73% | 3m 55s | ~16k | 3 | flash + k2.6 |
| 5 | v0.6.0 | 90% | 4m 30s | ~19k | 4 | flash + k2.6 + pro |
| 6 | v0.6.0 | 88% | 3m 20s | ~14k | 3 | flash + k2.6 |
| 7 | v0.6.2 | 95% | 4m 15s | ~17k | 4 | flash + k2.6 + pro |
| 8 | v0.6.2 | 95% | 3m 50s | ~16k | 3 | flash + k2.6 + pro |
| 9 | v0.6.3 | 95% | 4m 05s | ~18k | 4 | flash + k2.6 + pro |
| 10 | v0.6.3 | 95% | 3m 40s | ~15k | 3 | flash + k2.6 + pro |
| 11 | v0.6.3 | 95% | 4m 20s | ~19k | 4 | flash + k2.6 + pro |
| 12 | v0.7.0 | 95% | 4m 08s | ~17k | 4 | flash + k2.6 + pro |
| 13 | v0.8.0 | 95% | 3m 52s | ~16k | 3 | flash + k2.6 + pro |
| 14 | v0.9.0 | 100% | ~10m | ~20k | 8-11 | flash + k2.6 + pro |
| 15 | v0.9.0 | 100% | ~10m | ~22k | 9-10 | flash + k2.6 + pro |
| 16 | v0.9.0 | 100% | ~15m | ~24k | 11 | flash + k2.6 + pro |
| 17 | v0.9.0 | 100% | ~10m | ~20k | 9 | flash + k2.6 + pro |

**Example test prompt:**
> "Necesito un script en Python que lea un archivo JSON con datos de ventas, calcule totales por categoría y genere un reporte en markdown."

Expected behavior: triage → explore (flash: read requirements) → apply (k2.6: write script) → verify (pro: run tests) → archive.

---

## Working with AI Agents

A short guide on the mindset for collaborating effectively with autonomous agents.

### The Three Pillars

**1. Discovery — Ask before executing**

Don't jump to implementation. The most expensive mistake is building the wrong thing fast. Before any task:

- Ask the agent to explore the current state
- Ask what constraints exist
- Ask what could go wrong
- If you don't understand the answer, ask why

The right question saves hours. The wrong assumption costs them.

**2. Planning — Decide with criteria, not instinct**

Agents can execute faster than you can think. That's dangerous without a plan.

- Define what "done" looks like BEFORE starting
- Break ambiguous goals into phases with clear deliverables
- When the agent proposes a plan, challenge it: what are the tradeoffs? What did you consider and reject?
- If the agent says "I'll just..." — stop it. "Just" hides complexity.

**3. Versioning — Measure progress, don't assume it**

Structured iteration beats blind repetition:

- **One objective per version** — documented in a file, not in your head
- **A checklist of tests** — with pass/fail criteria and coverage percentage
- **Measurable output** — tokens, duration, accuracy. If you can't measure it, you can't improve it.
- **Archive results** — the agent's memory (and yours) degrades over context windows. Write things down.

### Rules of Engagement

- **Never trust, always verify.** The agent will tell you it works. Make it prove it.
- **Give context, not instructions.** "Fix the login bug" fails. "Users report 401 on /api/auth after token refresh — investigate the middleware" succeeds.
- **Correct early, not often.** One clear correction at the start is worth ten patches later.
- **Your job is to think.** The agent's job is to execute. If you're not thinking, you're not leading.

---

## Configuration

After installation, all config lives in `~/.hermes/`:

| File | Purpose |
|---|---|
| `config.yaml` | Model defaults, delegation settings, plugin list, Engram MCP server |
| `SOUL.md` | Orchestrator instructions (delegation rules, triage, memory protocol, format). Split into a `cobalt:managed` section (updated automatically) and a user section below the closing tag (never touched by install.sh). |
| `cobalt-cron.env` | Token storage for the patch-verify cron (mode 600) |
| `cobalt-verify-patch.sh` | Daily verifier script (managed by install.sh) |
| `cobalt-cron.log` | Output log from the verify cron |
| `plugins/cobalt-routing/` | Plugin source |
| `plugins/cobalt-routing/presets.yaml` | Model assignments per task_type |
| `skills/` | 10 curated skills (loaded by sub-agents on demand) |

### Memory: Engram

Memory is provided by [Engram](https://github.com/Gentleman-Programming/engram) via MCP. It is self-hosted, free, and exposes 19 MCP tools (`mcp_engram_mem_save`, `mcp_engram_mem_search`, `mcp_engram_mem_get_observation`, `mcp_engram_mem_session_summary`, etc.). The orchestrator runs a strict, deterministic memory protocol injected on every turn — saves on every decision/bugfix/discovery, searches before non-trivial work, and writes a session summary before closing. The protocol is rule-based, not LLM-decision-based.

### File conversion: markitdown (Microsoft, MCP)

[`markitdown-mcp`](https://github.com/microsoft/markitdown) is installed in the Hermes venv (`pip install --upgrade markitdown-mcp` runs on every install.sh execution, so updates are automatic). It exposes `convert_to_markdown(uri)` and is registered as an MCP server alongside Engram. Cobalt injects a mandatory protocol on every turn so PDFs / DOCX / XLSX / PPTX / images / audio / EPUB / CSV / XML / ZIP files get routed through markitdown FIRST — direct binary reads burn tokens for content the model cannot parse.

**No Docker required** — markitdown is a Python package and runs inside the existing Hermes venv. The Docker option exists in upstream as a sandbox alternative, not a requirement.

Sub-agents automatically get a "save discoveries before returning" rider appended to their goal so nothing decided inside a delegation is lost.

### Patch drift monitoring

Hermes ships releases weekly. The source patch in `delegate_tool.py` could break on any release. cobalt-agent runs **two layers** of monitoring:

- **GitHub Action** (`.github/workflows/patch-verify.yml`) — runs daily against the latest Hermes release upstream and opens an issue automatically if the patch fails.
- **VPS cron** (`scripts/verify-patch.sh`) — runs daily on your installed Hermes; sends a Telegram alert if drift is detected. Installed automatically when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are exported during install.

The cron entry is **idempotent**: re-running install.sh updates the entry only when it changed, otherwise leaves it alone.

### Installed Skills

| Skill | Source | Use case |
|---|---|---|
| prompt-engineering-patterns | wshobson/agents | LLM prompt design, few-shot, CoT |
| frontend-design | anthropics/skills | Generic frontend / React / Vue / Tailwind |
| interface-design | dammyjay93 | Admin panels, backoffice, SaaS interfaces |
| e2e-testing-patterns | wshobson/agents | E2E test patterns, fixtures, page objects |
| error-handling-patterns | wshobson/agents | Error/result types, retry, circuit breakers |
| postgresql | wshobson/agents | Schema, migrations, indexing |
| judgment-day | gentleman-programming/sdd-agent-team | Dual-review / adversarial review |
| branch-pr | gentleman-programming/sdd-agent-team | PR strategy, branch naming, review flow |
| skill-creator | gentleman-programming/sdd-agent-team | Build new skills |
| knowledge-graph | thestark77/autosdd | Visualize AI memory (works with Engram) |
| **playwright-cli** | microsoft/playwright-cli | Browser automation, codegen, selectors |
| **impeccable** | pbakaus/impeccable | Design system / design language refinement |
| **huashu-design** | alchaincyf/huashu-design | HTML hi-fi prototypes, slides, animations |
| **ui-ux-pro-max** | nextlevelbuilder/ui-ux-pro-max-skill | Professional UI/UX across platforms |
| **gpt-tasteskill** | Leonxlnx/taste-skill | Anti-slop, premium frontend taste |

**OpenSpec-compatible SDD skills** (installed to `~/.hermes/skills/` by `scripts/install_openspec_skills.sh`):

| Skill | Phase | Purpose |
|---|---|---|
| openspec-explore | Explore | Thinking partner — investigate before committing |
| openspec-propose | Propose | Create `proposal.md`, `design.md`, `tasks.md` in `openspec/changes/` |
| openspec-apply-change | Apply | Execute task checklist from `tasks.md` |
| openspec-verify-change | Verify | 3-dimensional report: completeness / correctness / coherence |
| openspec-archive-change | Archive | Move artifacts to `openspec/changes/archive/`, persist to Engram |

These skills are invoked automatically by sub-agents when the orchestrator delegates an SDD phase — no user instruction required.

### Auto-routing

Skill discovery is delegated to **Hermes's native mechanism** (`agent/prompt_builder.py:build_skills_system_prompt`, called from `run_agent.py`). On every system-prompt build Hermes scans `~/.hermes/skills/**/SKILL.md`, reads `name + description` from each frontmatter, and injects an `<available_skills>` block into the system prompt with a mandatory instruction to load relevant skills via `skill_view(name)`.

This is the Anthropic Skills progressive-disclosure pattern: lightweight metadata in the system prompt, full skill body loaded on-demand. The catalog is LRU-cached in memory and disk-snapshotted with mtime invalidation, so the token cost is paid once per session, not per turn.

**SDD skill routing is automatic.** When the orchestrator classifies a request as an EXECUTION TASK, `sdd_triage.py` instructs it to include `skill_view('<openspec-*>')` directives in delegation goals. The matching OpenSpec skill is loaded by the sub-agent before starting work — no user instruction needed. The `_OPENSPEC_SKILL_TO_TASK_TYPE` table in `router.py` also feeds skill-name signals directly into task_type inference for correct model assignment.

Earlier versions (v0.7.x and prior) used a keyword table in `src/skill_injector.py` to inject `[SKILL REQUIRED]` riders — that was removed in v0.8.0 as redundant. Hermes's native `<available_skills>` discovery plus the auto-SDD routing in v0.9.0 replace it cleanly.

If you want to force a specific skill on a sub-agent for non-SDD work, write the instruction directly in the orchestrator's goal text: `"Before starting, call skill_view('frontend-design') and apply its rules."`

---

## Project Structure

```
cobalt-agent/
  install.sh          ← Installer/updater (Linux/WSL)
  install.ps1         ← Windows wrapper (delegates to WSL)
  SOUL.md             ← Orchestrator instructions (deployed to ~/.hermes/)
  README.md           ← This file
  CHANGELOG.md        ← Version history
  CHECKLIST.md        ← Test verification checklist
  METRICS.md          ← Performance metrics
  src/                ← Plugin source (deployed to ~/.hermes/plugins/cobalt-routing/)
    __init__.py       ← Hook registration, schema patching, routing injection
    router.py         ← task_type inference, model resolution, dynamic timeout
    tool_guard.py     ← Tool blocking for orchestrator
    skill_injector.py ← Skill injection stub (removed in v0.8.0, kept for reference)
    sdd_triage.py     ← SDD phase classification + OpenSpec skill routing (pre_llm_call)
    compat.py         ← Version compatibility checking
    version_manager.py← Version tracking
    preset_tool.py    ← Preset switching tool
    config.py         ← Runtime config helpers
    utils.py          ← Shared utilities
    plugin.yaml       ← Plugin metadata
    presets.yaml      ← Model assignments per task_type
  scripts/
    install_openspec_skills.sh ← Installs 5 OpenSpec-compatible SDD skills to ~/.hermes/skills/
    verify-patch.sh   ← VPS patch drift verifier (deployed by install.sh)
  patches/
    apply_routing_patch.py  ← Source patch applicator (apply/verify/revert)
  docs/
    FLOW.md           ��� Complete system specification
  logs/               ← Test session logs per version
```

---

## Version Compatibility

cobalt-agent checks Hermes version at install time and at plugin load time:

| Hermes Version | Status | Behavior |
|---|---|---|
| 0.13.x | Compatible | Full functionality (tested baseline) |
| 0.14.x - 0.99.x | Warning | May work, not validated |
| >= 1.0.0 | Error | Blocked — breaking changes expected |

The source patch (`patches/apply_routing_patch.py`) uses pattern matching, not line numbers, so it survives minor Hermes updates. If the patch can't be applied, routing falls back to inference-only mode (no model override, but task_type classification and skill injection still work). Patch drift is monitored daily via GitHub Action + VPS cron with Telegram alerts.

---

## License

MIT

---

<details>
<summary><strong>Documentacion en Espanol</strong></summary>

# cobalt-agent

Capa de orquestacion autonoma para [Hermes Agent](https://github.com/NousResearch/hermes-agent). Un solo comando instala todo — routing de modelos, enforcement de herramientas, inyeccion de skills, triage SDD y configuracion curada.

**Funciona como instalador Y actualizador.** El mismo comando sirve para instalar desde cero o actualizar una instalacion existente.

```bash
git clone https://github.com/thestark77/cobalt-agent.git && cd cobalt-agent && bash install.sh
```

En Windows:
```powershell
git clone https://github.com/thestark77/cobalt-agent.git; cd cobalt-agent; .\install.ps1
```

---

## Filosofia

Hermes Agent es un orquestador poderoso, pero de fabrica:

- Usa el mismo modelo para cada sub-agente (caro, lento)
- Deja al orquestador ejecutar herramientas directamente (rompe la delegacion)
- No inyecta conocimiento de dominio en los sub-agentes (respuestas genericas)
- No tiene triage estructurado (empieza a trabajar sin clasificar el problema)

cobalt-agent resuelve los cuatro problemas mediante un **plugin basado en hooks** — sin forks, sin modificaciones al core mas alla de un unico patch reversible.

Principios de diseno:

1. **No invasivo** — Un patch + plugin externo. Todo lo demas es aditivo.
2. **Resiliente a actualizaciones** — Detecta cambios incompatibles, advierte en versiones no probadas, bloquea en versiones incompatibles. SOUL.md usa secciones marcadas para que las adiciones del usuario sobrevivan cada actualización.
3. **Replicable** — Un solo comando instala el entorno completo desde cero.
4. **Modular** — Cada mecanismo (routing, guard, skills, triage, timeout) es independiente.

---

## Requisitos

| Requisito | Minimo | Notas |
|---|---|---|
| Python | 3.11+ | Requerido por Hermes |
| git | cualquiera | Para clonar repositorios |
| curl | cualquiera | Para instalacion remota |
| pip | cualquiera | Gestor de paquetes Python |
| python3-venv | cualquiera | Necesario para entornos virtuales (`sudo apt install python3.X-venv`) |
| npm | opcional | Para OpenCode CLI (proveedor de modelos gratuito) |
| WSL | 2.0+ | Solo Windows — Hermes requiere Linux |

---

## Instalacion

### Instalacion Limpia

```bash
git clone https://github.com/thestark77/cobalt-agent.git
cd cobalt-agent
bash install.sh
```

El instalador ejecuta 10 pasos de forma autonoma:

1. **Prerrequisitos** — Verifica Python 3.11+, git, curl, pip
2. **Hermes Agent** — Clona e instala en `~/.hermes/hermes-agent/`
3. **OpenCode Go** — Instala proveedor gratuito de modelos (kimi-k2.6, deepseek-v4)
4. **Source Patch** — Aplica hook de routing a `delegate_tool.py` (reversible)
5. **Plugin** — Despliega cobalt-routing en `~/.hermes/plugins/` (routing + tool guard + triage + memory protocol)
6. **Configuracion** — SOUL.md, config.yaml, registro de Engram como servidor MCP
7. **Skills** — Instala skills curados de dominio
8. **SDD Skills** — Instala 5 skills SDD compatibles con OpenSpec en `~/.hermes/skills/`
9. **Patch verify automation** — Cron diario con alertas Telegram cuando Hermes rompe el patch
10. **Verificacion** — Chequeo de 6 puntos (binario, plugin, patch, SOUL, config, version)

### Configuracion requerida (lo unico que necesitas tocar)

```bash
# Backend de memoria — Engram Cloud
export ENGRAM_CLOUD_SERVER="https://tu-engram.host"
export ENGRAM_CLOUD_TOKEN="tu-token"
export ENGRAM_CLOUD_AUTOSYNC=1            # opcional, recomendado

# Alertas Telegram cuando el patch se rompe (opcional pero recomendado en VPS)
export TELEGRAM_BOT_TOKEN="tu-bot-token"
export TELEGRAM_CHAT_ID="tu-chat-id"

# Saltar instalacion del cron (off por defecto)
export COBALT_INSTALL_CRON=0              # solo si NO quieres el cron

bash install.sh
```

El instalador detecta las vars y cablea todo. Lo demas es desatendido.

### Actualizacion

El mismo comando. El instalador detecta instalaciones existentes y cambia a modo actualizacion:

- Actualiza Hermes Agent a la ultima version probada
- Re-aplica el source patch (idempotente)
- Reemplaza archivos del plugin con la ultima version
- Fusiona la config sin sobreescribir tus ajustes
- Preserva credenciales (auth del proveedor, tokens de Engram y Telegram)

### Despues de instalar

```bash
# Inicia Hermes
hermes chat
```

Si omitiste las variables de Engram en la primera corrida, expórtalas y vuelve a ejecutar `bash install.sh` — el instalador es idempotente y solo cablea lo que falta sin tocar el resto.

### Personalizar SOUL.md

`~/.hermes/SOUL.md` tiene dos secciones:

```
<!-- cobalt:managed:start — managed by install.sh, do not edit between these markers -->
[reglas de cobalt — se actualizan automáticamente con cada install.sh]
<!-- cobalt:managed:end -->

<!-- ── YOUR CUSTOM INSTRUCTIONS ── -->
Agrega persona, business logic, preferencias de idioma, conocimiento de dominio aquí.
Esta sección NUNCA es modificada por las actualizaciones de install.sh.
```

Agrega lo que quieras debajo del tag `<!-- cobalt:managed:end -->`. En el próximo `bash install.sh`, solo el bloque managed se reemplaza — tus adiciones sobreviven intactas.

**Migración**: si tienes un `~/.hermes/SOUL.md` existente sin los tags (instalado antes de v0.9.0), el instalador lo respalda a `SOUL.md.bak` y despliega la versión con tags. Mueve tu contenido personalizado debajo del tag de cierre después.

### Contexto por Proyecto

Hermes carga toda la configuracion de forma global desde `~/.hermes/`. Para dar instrucciones especificas de un proyecto sin contaminar la config global, coloca un archivo `CONTEXT.md` en la raiz de tu proyecto:

```bash
cp cobalt-agent/templates/CONTEXT.md ~/mi-proyecto/CONTEXT.md
# Edítalo con el stack, reglas y comandos de tu proyecto
```

Hermes lo lee automaticamente al inicio de cada sesion cuando se lanza desde ese directorio. Ver `templates/CONTEXT.md` para la plantilla completa.

---

## Como Funciona

```
Prompt del Usuario
    |
    v
[ORQUESTADOR] ── reglas SOUL.md ── "NUNCA llamas herramientas directamente"
    |
    |── Paso 0: TRIAGE (hook pre_llm_call)
    |   Clasificar: CONVERSACION o TAREA?
    |   Seleccionar fases SDD: explore -> propose -> apply -> verify -> archive
    |
    |── Paso 1: MEMORIA
    |   mcp_engram_mem_search para contexto previo (Engram)
    |
    |── Paso 2: DESCOMPONER
    |   Separar en concerns independientes
    |
    |── Paso 3: DELEGAR (hook pre_tool_call se activa aca)
    |   |
    |   |── Tool Guard: es delegate_task? Si no, BLOQUEAR.
    |   |── task_type: explicito del modelo O inferido del goal
    |   |── Model Router: task_type -> asignacion de modelo
    |   |── Skill Injector: task_type -> skills relevantes en el goal
    |   |── Dynamic Timeout: task_type -> valor de timeout
    |   |── Curation Suffix: task_type -> instrucciones de formato
    |   |
    |   v
    |   [SUB-AGENTE] ── corre con modelo asignado, skills inyectados
    |   Devuelve respuesta estructurada al orquestador
    |
    |── Paso 4: SINTETIZAR
    |   Recopilar resultados, presentar al usuario
    |
    v
[RESPUESTA]
```

---

## Routing de Modelos

Tres niveles via OpenCode Go (gratuito):

| Nivel | task_type | Modelo | Caso de Uso |
|---|---|---|---|
| Rapido | scout, explore, summarize | deepseek-v4-flash | Buscar, leer, analizar |
| Medio | apply, archive | kimi-k2.6 | Escribir codigo, implementar |
| Razonamiento | design, spec, tasks, verify, propose | deepseek-v4-pro | Arquitectura, testing, decisiones |

---

## Trabajando con Agentes IA

Guia corta sobre el mindset para colaborar efectivamente con agentes autonomos.

### Los Tres Pilares

**1. Descubrimiento — Pregunta antes de ejecutar**

No saltes a la implementacion. El error mas caro es construir lo incorrecto rapido.

- Pídele al agente que explore el estado actual
- Pregunta qué restricciones existen
- Pregunta qué podría salir mal
- Si no entiendes la respuesta, pregunta por qué

La pregunta correcta ahorra horas. La suposicion incorrecta las cuesta.

**2. Planeacion — Decide con criterio, no con instinto**

Los agentes pueden ejecutar más rápido de lo que se puede pensar. Eso es peligroso sin un plan.

- Define qué significa "terminado" ANTES de empezar
- Descompone metas ambiguas en fases con entregables claros
- Cuando el agente proponga un plan, desafíalo: ¿cuáles son los tradeoffs? ¿Qué consideraste y descartaste?

**3. Versionamiento — Mide el progreso, no lo asumas**

La iteración estructurada le gana a la repetición ciega:

- **Un objetivo por versión** — documentado en un archivo, no en la cabeza
- **Una checklist de tests** — con criterios pass/fail y porcentaje de cobertura
- **Output medible** — tokens, duración, precisión. Si no se puede medir, no se puede mejorar.
- **Archiva resultados** — la memoria del agente (y la del usuario) se degrada. Escribe las cosas.

### Reglas de Engagement

- **Nunca confíes, siempre verifica.** El agente dirá que funciona. Hazlo probarlo.
- **Da contexto, no instrucciones.** "Arregla el bug de login" falla. "Los usuarios reportan 401 en /api/auth después del token refresh — investiga el middleware" tiene éxito.
- **Corrige temprano, no seguido.** Una corrección clara al principio vale más que diez parches después.
- **El trabajo del usuario es pensar.** El trabajo del agente es ejecutar. Sin pensamiento no hay liderazgo.

---

## Compatibilidad de Versiones

| Version Hermes | Estado | Comportamiento |
|---|---|---|
| 0.13.x | Compatible | Funcionalidad completa (baseline tested) |
| 0.14.x - 0.99.x | Warning | Puede funcionar, no validado |
| >= 1.0.0 | Error | Bloqueado — se esperan cambios incompatibles |

### Memoria: Engram

La memoria la provee [Engram](https://github.com/Gentleman-Programming/engram) via MCP. Es self-hosted, gratis, y expone 19 herramientas MCP (`mcp_engram_mem_save`, `mcp_engram_mem_search`, `mcp_engram_mem_get_observation`, `mcp_engram_mem_session_summary`, etc.). El orquestador corre un protocolo de memoria estricto y determinista inyectado en cada turno — guarda en cada decisión/bugfix/discovery, busca antes de tareas no triviales, y escribe un session summary antes de cerrar. El protocolo es por reglas, no por decisión del LLM.

### Monitoreo de patch drift

Hermes saca releases semanales. El patch puede romperse en cualquiera. cobalt-agent corre **dos capas** de monitoreo:

- **GitHub Action** — diario contra el ultimo release de Hermes; abre un issue automatico si el patch falla.
- **VPS cron** — diario sobre tu instalacion local; manda alerta Telegram si detecta drift. Se instala automaticamente si exportaste `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` al correr el installer.

---

## Licencia

MIT

</details>
