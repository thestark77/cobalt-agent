"""Unit tests for the references (Karakeep) domain protocol (Phase 2). Stdlib-only.

Self-gated on whether the Karakeep MCP is wired in ~/.hermes/config.yaml; tests
force `_CONFIGURED` directly so they never touch the real config file.
"""

import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import karakeep_protocol as kp  # noqa: E402


class KarakeepProtocolTest(unittest.TestCase):
    def setUp(self):
        kp._CONFIGURED = True

    def tearDown(self):
        kp._CONFIGURED = None

    def test_block_present_when_configured(self):
        block = kp.build_karakeep_protocol_block(task_id="")
        self.assertIsNotNone(block)
        self.assertIn("REFERENCES PROTOCOL", block)

    def test_none_when_not_configured(self):
        kp._CONFIGURED = False
        self.assertIsNone(kp.build_karakeep_protocol_block(task_id=""))

    def test_subagent_skipped(self):
        self.assertIsNone(kp.build_karakeep_protocol_block(task_id="sa-1"))
        self.assertIsNone(kp.build_karakeep_protocol_block(task_id="subagent-9"))

    def test_block_encodes_key_decisions(self):
        b = kp.KARAKEEP_PROTOCOL_BLOCK
        self.assertIn("SOURCE OF TRUTH", b)
        self.assertIn("PRIVATE", b)
        # tagging/summarization is server-side, not the agent's job
        self.assertIn("server-side", b)
        # markitdown-first for files
        self.assertIn("markitdown", b)
        # proactive resurfacing OFF for now
        self.assertIn("resurfacing", b.lower())
        self.assertIn("OFF", b)

    def test_configured_is_cached(self):
        kp._CONFIGURED = True
        self.assertTrue(kp._karakeep_configured())
        kp._CONFIGURED = False
        self.assertFalse(kp._karakeep_configured())


if __name__ == "__main__":
    unittest.main()
