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
2. **Update-resilient** — Detects breaking changes, warns on untested versions, errors on incompatible ones.
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

The installer runs 9 steps autonomously:

1. **Prerequisites** — Checks Python 3.11+, git, curl, pip
2. **Hermes Agent** — Clones and installs in `~/.hermes/hermes-agent/`
3. **OpenCode Go** — Installs free model provider (kimi-k2.6, deepseek-v4)
4. **Source Patch** — Applies routing hook to `delegate_tool.py` (reversible)
5. **Plugin** — Deploys cobalt-routing to `~/.hermes/plugins/` (routing + tool guard + skills + memory protocol)
6. **Configuration** — SOUL.md, config.yaml, Engram MCP server wiring
7. **Skills** — Installs 10 curated skills
8. **Patch verify automation** — Daily cron job with Telegram alerts when Hermes drifts the patch
9. **Verification** — 6-point check (binary, plugin, patch, SOUL, config, version)

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
- Preserves credentials (honcho.json, provider auth)

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
    |   honcho_search for prior context
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

### 3. Skill Injection

Injects `skill_view` instructions into the sub-agent's goal so it loads relevant skills from the curated set. The orchestrator resolves which skills are relevant based on task_type and keywords.

### 4. SDD Triage

Forces the orchestrator to classify every input before acting. Injects a `[MANDATORY TRIAGE]` block via `pre_llm_call` that requires explicit phase selection (explore, propose, apply, verify, archive).

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

12 tests across 4 versions, measuring routing accuracy, delegation compliance, skill injection, and SDD triage:

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
| 12 | v0.7.0 | pending | — | — | — | — |

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
| `SOUL.md` | Orchestrator instructions (delegation rules, triage, memory protocol, format) |
| `cobalt-cron.env` | Token storage for the patch-verify cron (mode 600) |
| `cobalt-verify-patch.sh` | Daily verifier script (managed by install.sh) |
| `cobalt-cron.log` | Output log from the verify cron |
| `plugins/cobalt-routing/` | Plugin source |
| `plugins/cobalt-routing/presets.yaml` | Model assignments per task_type |
| `skills/` | 10 curated skills (loaded by sub-agents on demand) |

### Memory: Engram (no Honcho)

Memory is provided by [Engram](https://github.com/Gentleman-Programming/engram) via MCP. It is self-hosted, free, and exposes 19 MCP tools (`mem_save`, `mem_search`, `mem_get_observation`, `mem_session_summary`, etc.). The orchestrator runs a strict, deterministic memory protocol injected on every turn — saves on every decision/bugfix/discovery, searches before non-trivial work, and writes a session summary before closing. The protocol is rule-based, not LLM-decision-based.

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
| postgresql-table-design | wshobson/agents | Schema, migrations, indexing |
| judgment-day | gentleman-programming/sdd-agent-team | Dual-review / adversarial review |
| branch-pr | gentleman-programming/sdd-agent-team | PR strategy, branch naming, review flow |
| skill-creator | gentleman-programming/sdd-agent-team | Build new skills |
| knowledge-graph | thestark77/autosdd | Visualize AI memory (works with Engram) |
| **playwright-cli** | microsoft/playwright-cli | Browser automation, codegen, selectors |
| **impeccable** | pbakaus/impeccable | Design system / design language refinement |
| **huashu-design** | alchaincyf/huashu-design | HTML hi-fi prototypes, slides, animations |
| **ui-ux-pro-max** | nextlevelbuilder/ui-ux-pro-max-skill | Professional UI/UX across platforms |
| **gpt-tasteskill** | Leonxlnx/taste-skill | Anti-slop, premium frontend taste |

### Auto-routing

Skill discovery is delegated to **Hermes's native mechanism** (`agent/prompt_builder.py:build_skills_system_prompt`, called from `run_agent.py`). On every system-prompt build Hermes scans `~/.hermes/skills/**/SKILL.md`, reads `name + description` from each frontmatter, and injects an `<available_skills>` block into the system prompt with a mandatory instruction to load relevant skills via `skill_view(name)`.

This is the Anthropic Skills progressive-disclosure pattern: lightweight metadata in the system prompt, full skill body loaded on-demand. The catalog is LRU-cached in memory and disk-snapshotted with mtime invalidation, so the token cost is paid once per session, not per turn.

**Cobalt does NOT layer a second skill router on top.** Earlier versions (v0.7.x and prior) used a keyword table in `src/skill_injector.py` to inject `[SKILL REQUIRED]` riders into sub-agent goals — that was redundant with Hermes's native discovery and was removed in v0.8.0. Rich description-based selection by the model strictly beats brittle keyword matching, and skipping the cobalt rider saves tokens on every delegation.

If you want to force a specific skill on a sub-agent, write the instruction directly in the orchestrator's goal text: `"Before starting, call skill_view('frontend-design') and apply its rules."` The orchestrator already sees the `<available_skills>` catalog every turn and has enough context to make this call.

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
    skill_injector.py ← Skill instruction injection
    sdd_triage.py     ← SDD phase classification (pre_llm_call)
    compat.py         ← Version compatibility checking
    version_manager.py← Version tracking
    preset_tool.py    ← Preset switching tool
    plugin.yaml       ← Plugin metadata
    presets.yaml      ← Model assignments per task_type
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
2. **Resiliente a actualizaciones** — Detecta cambios incompatibles, advierte en versiones no probadas, bloquea en versiones incompatibles.
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

El instalador ejecuta 9 pasos de forma autonoma:

1. **Prerrequisitos** — Verifica Python 3.11+, git, curl, pip
2. **Hermes Agent** — Clona e instala en `~/.hermes/hermes-agent/`
3. **OpenCode Go** — Instala proveedor gratuito de modelos (kimi-k2.6, deepseek-v4)
4. **Source Patch** — Aplica hook de routing a `delegate_tool.py` (reversible)
5. **Plugin** — Despliega cobalt-routing en `~/.hermes/plugins/` (routing + tool guard + skills + memory protocol)
6. **Configuracion** — SOUL.md, config.yaml, registro de Engram como servidor MCP
7. **Skills** — Instala 10 skills curados
8. **Patch verify automation** — Cron diario con alertas Telegram cuando Hermes rompe el patch
9. **Verificacion** — Chequeo de 6 puntos (binario, plugin, patch, SOUL, config, version)

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
export COBALT_INSTALL_CRON=0              # solo si NO queres el cron

bash install.sh
```

El instalador detecta las vars y cablea todo. Lo demas es desatendido.

### Actualizacion

El mismo comando. El instalador detecta instalaciones existentes y cambia a modo actualizacion:

- Actualiza Hermes Agent a la ultima version probada
- Re-aplica el source patch (idempotente)
- Reemplaza archivos del plugin con la ultima version
- Fusiona la config sin sobreescribir tus ajustes
- Preserva credenciales (honcho.json, auth del proveedor)

### Despues de instalar

```bash
# Inicia Hermes
hermes chat
```

Si saltaste las vars de Engram en la primera corrida, exportalas y volve a correr `bash install.sh` — el instalador es idempotente y solo cablea lo que falta sin tocar el resto.

### Contexto por Proyecto

Hermes carga toda la configuracion de forma global desde `~/.hermes/`. Para dar instrucciones especificas de un proyecto sin contaminar la config global, coloca un archivo `CONTEXT.md` en la raiz de tu proyecto:

```bash
cp cobalt-agent/templates/CONTEXT.md ~/mi-proyecto/CONTEXT.md
# Editalo con el stack, reglas y comandos de tu proyecto
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
    |   honcho_search para contexto previo
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

- Pedile al agente que explore el estado actual
- Pregunta que restricciones existen
- Pregunta que podria salir mal
- Si no entendes la respuesta, pregunta por que

La pregunta correcta ahorra horas. La suposicion incorrecta las cuesta.

**2. Planeacion — Decide con criterio, no con instinto**

Los agentes pueden ejecutar mas rapido de lo que podes pensar. Eso es peligroso sin un plan.

- Defini que significa "terminado" ANTES de empezar
- Descompone metas ambiguas en fases con entregables claros
- Cuando el agente proponga un plan, desafialo: cuales son los tradeoffs? Que consideraste y descartaste?

**3. Versionamiento — Medi el progreso, no lo asumas**

Iteracion estructurada le gana a la repeticion ciega:

- **Un objetivo por version** — documentado en un archivo, no en tu cabeza
- **Una checklist de tests** — con criterios pass/fail y porcentaje de cobertura
- **Output medible** — tokens, duracion, precision. Si no lo podes medir, no lo podes mejorar.
- **Archiva resultados** — la memoria del agente (y la tuya) se degrada. Escribi las cosas.

### Reglas de Engagement

- **Nunca confies, siempre verifica.** El agente te va a decir que funciona. Hacelo probarlo.
- **Da contexto, no instrucciones.** "Arregla el bug de login" falla. "Los usuarios reportan 401 en /api/auth despues del token refresh — investiga el middleware" tiene exito.
- **Corregi temprano, no seguido.** Una correccion clara al principio vale mas que diez parches despues.
- **Tu trabajo es pensar.** El trabajo del agente es ejecutar. Si no estas pensando, no estas liderando.

---

## Compatibilidad de Versiones

| Version Hermes | Estado | Comportamiento |
|---|---|---|
| 0.13.x | Compatible | Funcionalidad completa (baseline tested) |
| 0.14.x - 0.99.x | Warning | Puede funcionar, no validado |
| >= 1.0.0 | Error | Bloqueado — se esperan cambios incompatibles |

### Memoria: Engram (sin Honcho)

La memoria la provee [Engram](https://github.com/Gentleman-Programming/engram) via MCP. Es self-hosted, gratis, y expone 19 herramientas MCP (`mem_save`, `mem_search`, `mem_get_observation`, `mem_session_summary`, etc.). El orquestador corre un protocolo de memoria estricto y determinista inyectado en cada turno — guarda en cada decisión/bugfix/discovery, busca antes de tareas no triviales, y escribe un session summary antes de cerrar. El protocolo es por reglas, no por decisión del LLM.

### Monitoreo de patch drift

Hermes saca releases semanales. El patch puede romperse en cualquiera. cobalt-agent corre **dos capas** de monitoreo:

- **GitHub Action** — diario contra el ultimo release de Hermes; abre un issue automatico si el patch falla.
- **VPS cron** — diario sobre tu instalacion local; manda alerta Telegram si detecta drift. Se instala automaticamente si exportaste `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` al correr el installer.

---

## Licencia

MIT

</details>
