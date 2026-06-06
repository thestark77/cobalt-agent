"""Unit tests for the calendar domain protocol (Phase 4). Stdlib-only.

Self-gated on whether a calendar MCP is wired in ~/.hermes/config.yaml; tests
force `_CONFIGURED` directly so they never touch the real config file.
"""

import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import calendar_protocol as cp  # noqa: E402


class CalendarProtocolTest(unittest.TestCase):
    def setUp(self):
        cp._CONFIGURED = True

    def tearDown(self):
        cp._CONFIGURED = None

    def test_block_present_when_configured(self):
        block = cp.build_calendar_protocol_block(task_id="")
        self.assertIsNotNone(block)
        self.assertIn("CALENDAR PROTOCOL", block)

    def test_none_when_not_configured(self):
        cp._CONFIGURED = False
        self.assertIsNone(cp.build_calendar_protocol_block(task_id=""))

    def test_subagent_skipped(self):
        self.assertIsNone(cp.build_calendar_protocol_block(task_id="sa-1"))
        self.assertIsNone(cp.build_calendar_protocol_block(task_id="subagent-9"))

    def test_block_encodes_key_decisions(self):
        b = cp.CALENDAR_PROTOCOL_BLOCK
        # writes only on the agent's OWN calendar; user's are read-only
        self.assertIn("OWN calendar", b)
        self.assertIn("READ-ONLY", b)
        # work calendar is free/busy only
        self.assertIn("FREE/BUSY", b)
        # respect busy blocks when scheduling
        self.assertIn("busy", b.lower())

    def test_configured_is_cached(self):
        cp._CONFIGURED = True
        self.assertTrue(cp._calendar_configured())
        cp._CONFIGURED = False
        self.assertFalse(cp._calendar_configured())


if __name__ == "__main__":
    unittest.main()
