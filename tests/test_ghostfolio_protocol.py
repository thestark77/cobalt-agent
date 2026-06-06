"""Unit tests for the investments (Ghostfolio) domain protocol (Phase 4). Stdlib-only.

Self-gated on whether the Ghostfolio MCP is wired in ~/.hermes/config.yaml; tests
force `_CONFIGURED` directly so they never touch the real config file.
"""

import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ghostfolio_protocol as gp  # noqa: E402


class GhostfolioProtocolTest(unittest.TestCase):
    def setUp(self):
        gp._CONFIGURED = True

    def tearDown(self):
        gp._CONFIGURED = None

    def test_block_present_when_configured(self):
        block = gp.build_ghostfolio_protocol_block(task_id="")
        self.assertIsNotNone(block)
        self.assertIn("INVESTMENTS PROTOCOL", block)

    def test_none_when_not_configured(self):
        gp._CONFIGURED = False
        self.assertIsNone(gp.build_ghostfolio_protocol_block(task_id=""))

    def test_subagent_skipped(self):
        self.assertIsNone(gp.build_ghostfolio_protocol_block(task_id="sa-1"))
        self.assertIsNone(gp.build_ghostfolio_protocol_block(task_id="subagent-9"))

    def test_block_encodes_key_decisions(self):
        b = gp.GHOSTFOLIO_PROTOCOL_BLOCK
        self.assertIn("SOURCE OF TRUTH", b)
        # ADR-0015: principles, not stock-picking
        self.assertIn("ADR-0015", b)
        self.assertIn("stock-picking", b)
        # anchor advice to real capacity / Firefly cross-reference
        self.assertIn("Firefly", b)
        # handle the empty-portfolio (deployed-ahead) case
        self.assertIn("EMPTY", b)

    def test_configured_is_cached(self):
        gp._CONFIGURED = True
        self.assertTrue(gp._ghostfolio_configured())
        gp._CONFIGURED = False
        self.assertFalse(gp._ghostfolio_configured())


if __name__ == "__main__":
    unittest.main()
