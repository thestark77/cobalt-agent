"""Deterministic statement reconciliation matcher (Phase 1, ADR-0013).

PURE LOGIC — no I/O, no Firefly tool names, no LLM. The orchestrator (guided by
finance_protocol) feeds it (a) the line from an uploaded statement and (b) the
expenses already reported/recorded for the period, and uses the verdict to
decide whether to skip (already there), create (unreported), or ASK the user
(ambiguous). This keeps the "never silently duplicate" rule in code, not in a
prompt the model might forget.

Decision policy (from the roadmap grilling):
- Confident MATCH  → same amount AND same day (merchant must not contradict).
- NEW              → no reported expense with this amount.
- AMBIGUOUS (ASK)  → same amount but: different day, fuzzy/contradicting
                     merchant, or several equally-plausible candidates.

Amounts are compared by magnitude (abs) in integer minor units, so a statement's
sign convention (debits negative, etc.) never breaks a match. Merchant strings
are normalized and compared with stdlib difflib — no external deps.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import List, Optional, Sequence, Union

# Verdict labels
MATCH = "match"
NEW = "new"
AMBIGUOUS = "ambiguous"

# Tunables (deliberate defaults; override per call if a bank needs it)
DEFAULT_DATE_WINDOW_DAYS = 3
MERCHANT_STRONG = 0.85   # near-date single candidate is auto-matched at/above this
MERCHANT_WEAK = 0.55     # below this on a same-day amount match → downgrade to ASK

# Noise tokens banks prepend/append that carry no identity signal.
_MERCHANT_NOISE = {
    "compra", "pago", "pagos", "pse", "tx", "trx", "transaccion", "transferencia",
    "debito", "credito", "abono", "cargo", "cobro", "automatico", "recurrente",
    "col", "co", "bogota", "medellin", "pos", "ref", "no",
}
_NONWORD = re.compile(r"[^a-z0-9\s]+")
_SPACES = re.compile(r"\s+")


@dataclass
class Txn:
    """A normalized transaction (reported entry or statement line).

    `amount` is any number; only its magnitude matters and it is rounded to
    integer minor units (2 decimals) before comparison. `txn_date` accepts an
    ISO string ('2026-06-01') or a date/datetime. `merchant` may be empty.
    """
    amount: float
    txn_date: Union[str, date, datetime]
    merchant: str = ""
    id: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class Verdict:
    classification: str                       # MATCH | NEW | AMBIGUOUS
    reason: str
    matched: Optional[Txn] = None             # set only for MATCH
    candidates: List[Txn] = field(default_factory=list)  # set for AMBIGUOUS

    @property
    def should_create(self) -> bool:
        return self.classification == NEW

    @property
    def should_ask(self) -> bool:
        return self.classification == AMBIGUOUS


def _minor_units(amount: float) -> int:
    """Magnitude in integer minor units (rounds away float drift)."""
    return abs(int(round(float(amount) * 100)))


def _as_date(d: Union[str, date, datetime]) -> Optional[date]:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        s = d.strip()
        # tolerate a trailing time component
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s[:10] if fmt.startswith("%Y") else s[:10], fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            return None
    return None


def _norm_merchant(m: str) -> str:
    if not m:
        return ""
    # strip accents
    m = unicodedata.normalize("NFKD", m)
    m = "".join(c for c in m if not unicodedata.combining(c))
    m = m.lower()
    m = _NONWORD.sub(" ", m)
    m = _SPACES.sub(" ", m).strip()
    tokens = [t for t in m.split(" ") if t and t not in _MERCHANT_NOISE and not t.isdigit()]
    return " ".join(tokens)


def _merchant_sim(a: str, b: str) -> Optional[float]:
    """Similarity in [0,1], or None if either side has no usable merchant text."""
    na, nb = _norm_merchant(a), _norm_merchant(b)
    if not na or not nb:
        return None
    return SequenceMatcher(None, na, nb).ratio()


def classify_statement_line(
    line: Txn,
    reported: Sequence[Txn],
    *,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
    merchant_strong: float = MERCHANT_STRONG,
    merchant_weak: float = MERCHANT_WEAK,
) -> Verdict:
    """Classify one statement line against already-reported expenses.

    Returns a Verdict; the caller maps MATCH→skip, NEW→create, AMBIGUOUS→ASK.
    """
    line_units = _minor_units(line.amount)
    line_date = _as_date(line.txn_date)

    amount_matches = [r for r in reported if _minor_units(r.amount) == line_units]
    if not amount_matches:
        return Verdict(NEW, reason="no reported expense with this amount")

    # If we can't parse the line's date, any amount match is at best ambiguous.
    if line_date is None:
        if len(amount_matches) == 1:
            return Verdict(
                AMBIGUOUS, reason="amount matches one entry but statement date is unparseable",
                candidates=list(amount_matches),
            )
        return Verdict(
            AMBIGUOUS, reason="amount matches several entries; statement date is unparseable",
            candidates=list(amount_matches),
        )

    same_day = [r for r in amount_matches if _as_date(r.txn_date) == line_date]
    if len(same_day) == 1:
        r = same_day[0]
        sim = _merchant_sim(r.merchant, line.merchant)
        if sim is None or sim >= merchant_weak:
            return Verdict(MATCH, reason="same amount and date", matched=r)
        return Verdict(
            AMBIGUOUS,
            reason="same amount and date but merchant differs",
            candidates=[r],
        )
    if len(same_day) > 1:
        return Verdict(
            AMBIGUOUS,
            reason="several reported expenses share this amount and date",
            candidates=list(same_day),
        )

    # No same-day candidate: look within the date window.
    near = []
    for r in amount_matches:
        rd = _as_date(r.txn_date)
        if rd is not None and abs((rd - line_date).days) <= date_window_days:
            near.append(r)
    if near:
        strong = [r for r in near if (_merchant_sim(r.merchant, line.merchant) or 0.0) >= merchant_strong]
        if len(strong) == 1:
            return Verdict(
                MATCH,
                reason="same amount, near date, strong merchant match",
                matched=strong[0],
            )
        return Verdict(
            AMBIGUOUS,
            reason="same amount but a different day — could be the same purchase or a new one",
            candidates=list(near),
        )

    # Amount matches exist but all dates are far apart.
    return Verdict(
        AMBIGUOUS,
        reason="same amount but dates are far apart",
        candidates=list(amount_matches),
    )


def reconcile_statement(
    lines: Sequence[Txn],
    reported: Sequence[Txn],
    **kwargs,
) -> List[Verdict]:
    """Classify every line of a statement. Convenience wrapper over the per-line
    classifier; the caller still drives the create/skip/ask actions."""
    return [classify_statement_line(line, reported, **kwargs) for line in lines]


# --------------------------------------------------------------------------
# Tool surface — exposes the deterministic matcher to the orchestrator so the
# anti-duplication decision is made in code, not by the model. The model parses
# the statement (via markitdown) and fetches reported entries (from Firefly),
# passes both here as JSON arrays, and acts on the verdicts:
#   match → skip (already recorded)   new → create in Firefly   ambiguous → ASK
# --------------------------------------------------------------------------

TOOL_NAME = "finance_reconcile"

_RECONCILE_PARAMS = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "description": "Statement lines to reconcile (parsed from the uploaded statement).",
            "items": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Transaction amount (sign ignored; magnitude is matched)."},
                    "date": {"type": "string", "description": "ISO date (YYYY-MM-DD) or dd/mm/yyyy."},
                    "merchant": {"type": "string"},
                    "id": {"type": "string", "description": "Optional source id for traceability."},
                },
                "required": ["amount", "date"],
            },
        },
        "reported": {
            "type": "array",
            "description": "Expenses already recorded in Firefly for the period.",
            "items": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "date": {"type": "string"},
                    "merchant": {"type": "string"},
                    "id": {"type": "string", "description": "Firefly transaction id."},
                },
                "required": ["amount", "date"],
            },
        },
        "date_window_days": {
            "type": "integer",
            "description": f"Max day gap for a near-date match (default {DEFAULT_DATE_WINDOW_DAYS}).",
        },
    },
    "required": ["lines", "reported"],
}

# Hermes expects the full function-definition shape (name + description +
# parameters), the same as the other cobalt tools. Passing the raw parameters
# object instead puts type/properties/required directly under `function`, which
# the LLM provider rejects ("Extra inputs are not permitted, field
# 'function.type'") and that poisons the WHOLE tools array — every turn 400s.
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Reconcile uploaded statement lines against already-reported expenses, "
        "deterministically classifying each as match (already recorded) / new "
        "(create it) / ambiguous (ask the user). Never silently duplicates."
    ),
    "parameters": _RECONCILE_PARAMS,
}


def _to_txn(d: dict) -> Txn:
    return Txn(
        amount=d.get("amount", 0),
        txn_date=d.get("date", ""),
        merchant=d.get("merchant", "") or "",
        id=d.get("id"),
        raw=d,
    )


def handle_reconcile(args: dict) -> str:
    """Tool handler: classify each line deterministically and return a summary.

    Returns a human+machine readable text block: a per-line verdict list plus
    counts, so the orchestrator can act and also explain its actions.
    """
    import json

    if not isinstance(args, dict):
        return "finance_reconcile: invalid args (expected object)."
    lines = [_to_txn(x) for x in (args.get("lines") or []) if isinstance(x, dict)]
    reported = [_to_txn(x) for x in (args.get("reported") or []) if isinstance(x, dict)]
    if not lines:
        return "finance_reconcile: no statement lines provided."

    kwargs = {}
    if isinstance(args.get("date_window_days"), int):
        kwargs["date_window_days"] = args["date_window_days"]

    verdicts = reconcile_statement(lines, reported, **kwargs)

    out = []
    counts = {MATCH: 0, NEW: 0, AMBIGUOUS: 0}
    for line, v in zip(lines, verdicts):
        counts[v.classification] = counts.get(v.classification, 0) + 1
        entry = {
            "line": {"amount": line.amount, "date": str(line.txn_date), "merchant": line.merchant, "id": line.id},
            "verdict": v.classification,
            "reason": v.reason,
        }
        if v.matched is not None:
            entry["matched_id"] = v.matched.id
        if v.candidates:
            entry["candidate_ids"] = [c.id for c in v.candidates]
        out.append(entry)

    summary = {
        "action_map": {"match": "skip (already recorded)", "new": "create in Firefly", "ambiguous": "ASK the user"},
        "counts": counts,
        "results": out,
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)
