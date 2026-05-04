# cobalt-agent

Modular plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that extends orchestration capabilities without modifying core behavior.

## What it does

- **Model Routing** — Routes sub-agent tasks to cost-optimized models based on task type (scout→flash, apply→mid-tier, verify→pro)
- **Tool Guard** — Mechanically enforces orchestrator delegation pattern (blocks direct execution tools)
- **Skill Injection** — Tells sub-agents which skills to load for their task (they load independently via `skill_view`)
- **Curation Suffixes** — Injects response format instructions so sub-agents return structured, concise summaries
- **Memory Integration** — Honcho-based persistent memory with cross-session learning

## Design Principles

- **Non-invasive**: Single source patch + external plugin. Everything else is additive.
- **Update-resilient**: Detects breaking changes, warns clearly, degrades gracefully.
- **Replicable**: Automated install from this repo with minimal user interaction.

## Quick Start

```bash
# Clone
git clone https://github.com/thestark77/cobalt-agent.git
cd cobalt-agent

# Apply source patch (the only Hermes modification)
python patches/apply_routing_patch.py apply

# Copy plugin
cp -r src/ ~/.hermes/plugins/cobalt-routing/

# Install skills
# (see docs/FLOW.md section 7 for the full list)
```

## Structure

```
src/                  → Plugin source (deploys to ~/.hermes/plugins/cobalt-routing/)
patches/              → Source patch applicator (apply/verify/revert)
docs/FLOW.md          → Source of truth: complete expected behavior
CHANGELOG.md          → Version history with WORKS/BROKEN tracking
CHECKLIST.md          → Test checklist for verification
logs/                 → Test session logs per version
```

## Documentation

- [FLOW.md](docs/FLOW.md) — Complete system specification
- [CHANGELOG.md](CHANGELOG.md) — Version history
- [CHECKLIST.md](CHECKLIST.md) — Test verification checklist

## Current Version

**v0.4.0** — Model routing + Tool guard + Skill injection

## License

MIT
