"""Unit tests for incognito mode (ADR-0014). Stdlib-only.

Uses COBALT_INCOGNITO_FILE to isolate state in a temp dir — never touches
~/.hermes. Deterministic: no reliance on wall-clock except the TTL-expiry test,
which writes an explicitly old timestamp into the state file.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import incognito as inc  # noqa: E402


class IncognitoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cobalt-inc-")
        self.state = os.path.join(self.dir, "state.json")
        os.environ["COBALT_INCOGNITO_FILE"] = self.state
        os.environ.pop("COBALT_INCOGNITO_TTL_SECONDS", None)
        inc.set_turn_incognito(False)

    def tearDown(self):
        os.environ.pop("COBALT_INCOGNITO_FILE", None)
        os.environ.pop("COBALT_INCOGNITO_TTL_SECONDS", None)
        inc.set_turn_incognito(False)

    # --- command parsing ---------------------------------------------------
    def test_parse_slash_commands(self):
        self.assertEqual(inc.parse_commands("/incognito")["toggle"], "toggle")
        self.assertEqual(inc.parse_commands("/incognito on")["toggle"], "on")
        self.assertEqual(inc.parse_commands("/incognito off")["toggle"], "off")
        self.assertIsNone(inc.parse_commands("/incognito status")["toggle"])
        self.assertTrue(inc.parse_commands("mira esto /secret")["secret"])
        self.assertFalse(inc.parse_commands("hola")["secret"])

    def test_parse_no_false_positive(self):
        # substrings must not trigger
        self.assertIsNone(inc.parse_commands("the secretariat won")["toggle"])
        self.assertFalse(inc.parse_commands("the secretariat won")["secret"])
        self.assertFalse(inc.parse_commands("/secretary general")["secret"])

    def test_parse_natural_language(self):
        self.assertEqual(inc.parse_commands("activar modo incógnito")["toggle"], "on")
        self.assertEqual(inc.parse_commands("desactivar modo incognito")["toggle"], "off")

    # --- sticky session ----------------------------------------------------
    def test_sticky_on_persists_across_turns(self):
        ti, note = inc.evaluate_turn("/incognito on")
        self.assertTrue(ti)
        self.assertIsNotNone(note)
        self.assertTrue(inc.is_session_active())
        # a plain next turn is still incognito (sticky)
        ti2, _ = inc.evaluate_turn("¿qué hora es?")
        self.assertTrue(ti2)

    def test_off_stops_persistence(self):
        inc.evaluate_turn("/incognito on")
        ti, note = inc.evaluate_turn("/incognito off")
        self.assertFalse(ti)
        self.assertIsNotNone(note)
        self.assertFalse(inc.is_session_active())
        ti2, _ = inc.evaluate_turn("hola")
        self.assertFalse(ti2)

    def test_toggle_flips(self):
        ti1, _ = inc.evaluate_turn("/incognito")
        self.assertTrue(ti1)
        ti2, _ = inc.evaluate_turn("/incognito")
        self.assertFalse(ti2)

    # --- /secret one-shot --------------------------------------------------
    def test_secret_is_one_shot(self):
        ti, _ = inc.evaluate_turn("un favor puntual /secret")
        self.assertTrue(ti)
        self.assertFalse(inc.is_session_active())  # NOT sticky
        ti2, _ = inc.evaluate_turn("siguiente mensaje normal")
        self.assertFalse(ti2)

    # --- write blocking ----------------------------------------------------
    def test_block_writes_when_session_active(self):
        inc.evaluate_turn("/incognito on")
        inc.set_turn_incognito(True)
        self.assertIsNotNone(inc.block_if_incognito("mcp_engram_mem_save"))
        self.assertIsNotNone(inc.block_if_incognito("mcp_iris_iris_remember"))
        # reads pass
        self.assertIsNone(inc.block_if_incognito("mcp_engram_mem_search"))
        self.assertIsNone(inc.block_if_incognito("mcp_iris_iris_get_context"))

    def test_block_via_secret_flag_without_session(self):
        # /secret sets the per-turn flag but not the session
        inc.set_turn_incognito(True)
        self.assertFalse(inc.is_session_active())
        self.assertIsNotNone(inc.block_if_incognito("mcp_engram_mem_save"))
        self.assertIsNone(inc.block_if_incognito("mcp_engram_mem_search"))

    def test_no_block_when_off(self):
        inc.set_turn_incognito(False)
        self.assertFalse(inc.is_session_active())
        self.assertIsNone(inc.block_if_incognito("mcp_engram_mem_save"))

    def test_session_blocks_even_if_turn_flag_unset(self):
        # robustness: sub-agent tool call (flag never set) but session active
        inc.set_session(True)
        inc.set_turn_incognito(False)
        self.assertIsNotNone(inc.block_if_incognito("mcp_engram_mem_save"))

    # --- TTL ---------------------------------------------------------------
    def test_ttl_auto_off(self):
        # write a state whose last_activity is 3h old; default TTL is 2h
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        Path(self.state).write_text(json.dumps(
            {"active": True, "since": old, "last_activity": old}), encoding="utf-8")
        self.assertFalse(inc.is_session_active())  # timed out -> off
        # and the state file was cleared
        self.assertFalse(Path(self.state).exists())

    def test_ttl_zero_disables_expiry(self):
        os.environ["COBALT_INCOGNITO_TTL_SECONDS"] = "0"
        old = (datetime.now(timezone.utc) - timedelta(hours=99)).isoformat()
        Path(self.state).write_text(json.dumps(
            {"active": True, "since": old, "last_activity": old}), encoding="utf-8")
        self.assertTrue(inc.is_session_active())  # TTL disabled -> still on

    def test_secret_url_no_false_positive(self):
        self.assertFalse(inc.parse_commands("mira https://api.example.com/secret aca")["secret"])
        self.assertFalse(inc.parse_commands("https://x.com/secret/path")["secret"])

    def test_incognito_url_no_false_positive(self):
        self.assertIsNone(inc.parse_commands("ver https://x.com/incognito/mode")["toggle"])

    # --- cross-process /secret marker -------------------------------------
    def test_secret_marker_lifecycle(self):
        inc.evaluate_turn("un favor /secret")
        self.assertTrue(inc._secret_marker_present())
        self.assertTrue(inc.is_incognito_effective())
        # next non-secret turn clears it
        inc.evaluate_turn("mensaje normal")
        self.assertFalse(inc._secret_marker_present())

    def test_effective_via_marker_only(self):
        # simulate a sub-agent process: no per-turn flag, no session, only the
        # file marker left by the orchestrator's /secret turn.
        inc.set_turn_incognito(False)
        self.assertFalse(inc.is_session_active())
        inc.set_secret_marker()
        self.assertTrue(inc.is_incognito_effective())
        self.assertIsNotNone(inc.block_if_incognito("mcp_engram_mem_save"))
        self.assertIsNone(inc.block_if_incognito("mcp_engram_mem_search"))

    # --- slash command handlers -------------------------------------------
    def test_incognito_command_on_off_toggle(self):
        out = inc.handle_incognito_command("on")
        self.assertIn("ON", out.upper())
        self.assertTrue(inc.is_session_active())
        inc.handle_incognito_command("off")
        self.assertFalse(inc.is_session_active())
        # bare toggles
        inc.handle_incognito_command("")
        self.assertTrue(inc.is_session_active())
        inc.handle_incognito_command("")
        self.assertFalse(inc.is_session_active())

    def test_secret_command_arms_next_message(self):
        out = inc.handle_secret_command("")
        self.assertIn("PRÓXIMO", out.upper())
        self.assertTrue(inc.is_armed())
        # next message (no /secret in it) is one-shot incognito, and consumes arm
        ti, _ = inc.evaluate_turn("este es mi contenido privado")
        self.assertTrue(ti)
        self.assertFalse(inc.is_armed())  # consumed
        # the turn AFTER is back to normal
        ti2, _ = inc.evaluate_turn("mensaje normal")
        self.assertFalse(ti2)

    # --- directive ---------------------------------------------------------
    def test_directive_present_when_incognito(self):
        out = inc.build_incognito_directive(True, None)
        self.assertIn("INCÓGNITO", out)
        self.assertIsNone(inc.build_incognito_directive(False, None))
        # note shown even when not incognito (e.g. the /incognito off turn)
        self.assertIn("avisá", inc.build_incognito_directive(False, "Modo incógnito desactivado."))


if __name__ == "__main__":
    unittest.main()
