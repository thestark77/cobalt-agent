"""Unit tests for the finance domain protocol (Phase 1, ADR-0013/0015). Stdlib-only.

The block is self-gating on whether the Firefly MCP is wired in
~/.hermes/config.yaml. Tests force `_CONFIGURED` directly so they never touch
the real config file and stay deterministic.
"""

import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import finance_protocol as fp  # noqa: E402


class FinanceProtocolTest(unittest.TestCase):
    def setUp(self):
        # default: pretend Firefly IS configured unless a test says otherwise
        fp._CONFIGURED = True

    def tearDown(self):
        fp._CONFIGURED = None

    # --- gating -----------------------------------------------------------
    def test_block_present_when_configured(self):
        block = fp.build_finance_protocol_block(task_id="")
        self.assertIsNotNone(block)
        self.assertIn("FINANCE PROTOCOL", block)

    def test_none_when_not_configured(self):
        fp._CONFIGURED = False
        self.assertIsNone(fp.build_finance_protocol_block(task_id=""))

    def test_subagent_skipped(self):
        # sub-agents get domain rules via their goal, not this block
        self.assertIsNone(fp.build_finance_protocol_block(task_id="sa-123"))
        self.assertIsNone(fp.build_finance_protocol_block(task_id="subagent-7"))

    def test_orchestrator_blank_task_id_gets_block(self):
        self.assertIsNotNone(fp.build_finance_protocol_block(task_id=""))

    # --- content invariants (the decisions that must survive edits) -------
    def test_block_encodes_ownership_and_reconciliation(self):
        block = fp.FINANCE_PROTOCOL_BLOCK
        # Firefly is the source of truth for money
        self.assertIn("SOURCE OF TRUTH", block)
        # never silently duplicate — reconciliation must ask when ambiguous
        self.assertIn("RECONCILIATION", block)
        self.assertIn("ASK", block)
        # daily reminder + parametrizable frequency on a separate track
        self.assertIn("21:00", block)
        self.assertIn("parametrizable", block)
        # ADR-0015: principles, not stock-picking
        self.assertIn("ADR-0015", block)
        self.assertIn("stock-picking", block)

    # --- caching ----------------------------------------------------------
    def test_configured_is_cached(self):
        # when already set, _firefly_configured returns the cached value and
        # never reads the filesystem
        fp._CONFIGURED = True
        self.assertTrue(fp._firefly_configured())
        fp._CONFIGURED = False
        self.assertFalse(fp._firefly_configured())


if __name__ == "__main__":
    unittest.main()
