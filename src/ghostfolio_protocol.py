"""Cobalt Routing — Investments domain protocol (Phase 4, iris-ai ADR-0013/0015).

Injects, on every orchestrator turn (only when the Ghostfolio MCP is wired), the
rules that make the agent use the investments organ correctly and compose it with
iris for advice. Behavioral glue — it does NOT hardcode Ghostfolio tool names; it
tells the orchestrator HOW to behave. Wiring (allowlist, exact tool names) is
added once the MCP is deployed.

Decisions encoded (roadmap grilling + ADR-0015):
- Ownership: Ghostfolio is the source of truth for the investment portfolio
  (accounts, holdings, orders, performance). iris owns ADVICE.
- Advice is PRINCIPLES, NOT stock-picking: educate and support decisions; never
  recommend specific securities or act as a licensed advisor.
- Deployed ready even before the user actively invests — so it stays read-mostly
  until there is a portfolio, and advice is anchored to real capacity (emergency
  fund / debt / cash flow, cross-referencing Firefly when available).

Silently disabled when the Ghostfolio MCP is not configured.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


GHOSTFOLIO_PROTOCOL_BLOCK = """
[INVESTMENTS PROTOCOL — Ghostfolio is wired]

The Ghostfolio investments MCP is available. It is the SOURCE OF TRUTH for the
investment PORTFOLIO (accounts, holdings, orders, performance, allocations).
iris owns ADVICE. Never duplicate portfolio state into iris memory — read it from
Ghostfolio on demand.

# OWNERSHIP & PRECEDENCE
- Holdings / positions / performance / returns → ALWAYS read from Ghostfolio
  (never guess or recall from memory). For numbers, Ghostfolio wins.
- Reasoning, goals, risk profile, education → iris (combine with Ghostfolio data).

# ADVICE (ADR-0015 — principles, NOT stock-picking)
- Educate and support the user's own decisions; NEVER recommend specific
  securities/tickers, time the market, or act as a licensed advisor. Always
  disclose risk and flag speculative temptations against the user's profile.
- Principles order: emergency fund → kill high-interest debt → low-cost
  diversified index/ETF, dollar-cost averaging, long horizon.
- Anchor every suggestion to REAL CAPACITY: cross-reference Firefly (cash flow,
  debt, emergency fund) when available before discussing investing.

# STATE-AWARE BEHAVIOR
- The portfolio may be EMPTY (deployed ahead of actually investing). If so, do
  not invent holdings; focus on readiness (fund, debt, plan) and only record real
  accounts/orders the user confirms.
- Recording an order/account is a deliberate WRITE — confirm with the user first;
  never fabricate transactions.
"""


def build_ghostfolio_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the investments protocol block for this turn.

    - Sub-agents: never injected (they get domain rules via their goal).
    - Orchestrator: injected only when the Ghostfolio MCP is configured.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _ghostfolio_configured():
        return None
    return GHOSTFOLIO_PROTOCOL_BLOCK


def _ghostfolio_configured() -> bool:
    """True iff the 'ghostfolio' MCP server is wired in ~/.hermes/config.yaml.

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
        logger.debug("ghostfolio_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    servers = (data.get("mcp_servers") or {})
    _CONFIGURED = "ghostfolio" in servers
    return _CONFIGURED


_CONFIGURED: Optional[bool] = None
