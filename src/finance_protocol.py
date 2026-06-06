"""Cobalt Routing — Finance domain protocol (Phase 1, iris-ai ADR-0013/0015).

Injects, on every orchestrator turn (only when the Firefly III MCP is wired),
the rules that make the agent use the finance organ correctly and compose it
with iris for advice. Behavioral glue — it does NOT hardcode Firefly tool names;
it tells the orchestrator HOW to behave. Wiring (allowlist, presets, exact tool
names) is added once the MCP is deployed.

Decisions encoded (from the roadmap grilling):
- Ownership: Firefly is the source of truth for money; iris owns advice/profile.
- Ingestion: initial historical statements (PDF via markitdown) + real-time daily
  reporting (the primary ledger) + monthly statement reconciliation.
- Reconciliation: match reported vs statement (amount+date+merchant) →
  match / propose-new / ASK when ambiguous (never silently duplicate).
- Daily 21:00 reminder shows the last recorded entry; reminder frequency is
  parametrizable by chat (separate deterministic track, not the nudge gate).
- Advice: principles, not stock-picking (ADR-0015), grounded in real capacity.

Silently disabled when the Firefly MCP is not configured.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


FINANCE_PROTOCOL_BLOCK = """
[FINANCE PROTOCOL — Firefly III is wired]

The Firefly III finance MCP is available. It is the SOURCE OF TRUTH for money
(accounts, transactions, bills/subscriptions, loans, budgets). iris owns the
PROFILE and ADVICE. Never duplicate financial state into iris memory — keep only
thin pointers (e.g. "has a Spotify subscription; detail in Firefly bill").

# OWNERSHIP & PRECEDENCE
- Amounts / transactions / balances → ALWAYS read from Firefly (never guess or
  recall from memory). For numbers, Firefly wins over anything in memory.
- Reasoning, goals, risk profile, advice → iris (combine with Firefly data).

# INGESTION (manual — Colombia has no viable auto-aggregator)
1. Historical load: bank/wallet statements (usually PDF — Banco de Bogotá, Nequi,
   Rappi). Convert with markitdown FIRST, then create the transactions in Firefly.
2. Real-time daily reporting (PRIMARY ledger): when the user reports a purchase
   ("almuerzo $35.000", or a receipt photo/PDF), record it in Firefly right away.
   Covers cash and anything not on a statement.
3. Monthly statement = a RECONCILIATION pass, NOT a second entry path.

# RECONCILIATION (avoid double-counting — critical)
When a statement is uploaded, for EACH line, match against what was already
reported (amount + date proximity + merchant):
- Confident match → mark reconciled, do NOT create a duplicate.
- No match → it's something unreported (auto-debit, fee, renewal) → propose adding
  it and confirm.
- Ambiguous (same amount, different day, or fuzzy merchant) → ASK the user:
  "¿este cobro de $X del día Y es el mismo que ya reportaste, o uno distinto?"

# DAILY FOLLOW-UP & ADVICE
- A daily 21:00 reminder asks if any expense is missing and shows the LAST recorded
  entry as a reference. Reminder frequency is parametrizable by chat ("ponelos cada
  3 días") and runs on a separate deterministic track (not the behavioral nudge gate).
- When the period closes, run a financial feedback session grounded in Firefly data
  + the user's OCEAN profile/goals (via iris): flag irresponsible spending WITH
  EVIDENCE, dangerous recurring charges, and positive/negative patterns.

# INVESTMENT ADVICE (ADR-0015 — principles, not stock-picking)
Educate + support decisions; NEVER recommend specific securities or act as a
licensed advisor. Anchor to real capacity (emergency fund, debt, cash flow from
Firefly). Principles order: emergency fund → kill high-interest debt → low-cost
diversified index/ETF, DCA, long horizon. Always disclose risk; flag speculative
temptations against the user's profile.
"""


def build_finance_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the finance protocol block for this turn.

    - Sub-agents: never injected (they get domain rules via their goal).
    - Orchestrator: injected only when the Firefly MCP is configured.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _firefly_configured():
        return None
    return FINANCE_PROTOCOL_BLOCK


def _firefly_configured() -> bool:
    """True iff the 'firefly' MCP server is wired in ~/.hermes/config.yaml.

    Cached on first call; re-import the module to invalidate.
    """
    global _CONFIGURED
    if _CONFIGURED is not None:
        return _CONFIGURED
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        _CONFIGURED = False
        return False
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("finance_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    servers = (data.get("mcp_servers") or {})
    _CONFIGURED = "firefly" in servers
    return _CONFIGURED


_CONFIGURED: Optional[bool] = None
