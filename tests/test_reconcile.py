"""Unit tests for the deterministic reconciliation matcher. Stdlib-only.

Covers the decision policy: confident match, new, and every ambiguous path
(different day, fuzzy/contradicting merchant, multiple candidates, unparseable
date). Amounts use COP-style magnitudes; sign convention is intentionally mixed
to prove magnitude matching.
"""

import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import reconcile as rc  # noqa: E402
from reconcile import Txn  # noqa: E402


class ReconcileTest(unittest.TestCase):
    # --- MATCH ------------------------------------------------------------
    def test_same_amount_same_day_matches(self):
        reported = [Txn(35000, "2026-06-01", "Rappi")]
        line = Txn(-35000, "2026-06-01", "RAPPI COL")  # debit sign, noisy merchant
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.MATCH)
        self.assertIs(v.matched, reported[0])

    def test_same_day_missing_merchant_still_matches(self):
        reported = [Txn(120000, "2026-06-02", "")]
        line = Txn(120000, "2026-06-02", "")
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.MATCH)

    def test_near_date_strong_merchant_matches(self):
        reported = [Txn(50000, "2026-06-01", "Netflix")]
        line = Txn(50000, "2026-06-03", "NETFLIX")  # 2 days off, same merchant
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.MATCH)

    # --- NEW --------------------------------------------------------------
    def test_no_amount_match_is_new(self):
        reported = [Txn(35000, "2026-06-01", "Rappi")]
        line = Txn(9900, "2026-06-01", "Spotify")
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.NEW)
        self.assertTrue(v.should_create)

    def test_empty_reported_is_new(self):
        v = rc.classify_statement_line(Txn(1000, "2026-06-01", "x"), [])
        self.assertEqual(v.classification, rc.NEW)

    # --- AMBIGUOUS --------------------------------------------------------
    def test_same_amount_different_day_is_ambiguous(self):
        reported = [Txn(35000, "2026-06-01", "Rappi")]
        line = Txn(35000, "2026-06-20", "Rappi")  # same amount, far date
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.AMBIGUOUS)
        self.assertTrue(v.should_ask)
        self.assertEqual(len(v.candidates), 1)

    def test_same_day_contradicting_merchant_is_ambiguous(self):
        reported = [Txn(35000, "2026-06-01", "Rappi")]
        line = Txn(35000, "2026-06-01", "Banco de Bogota cuota manejo")
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.AMBIGUOUS)

    def test_multiple_same_day_same_amount_is_ambiguous(self):
        reported = [
            Txn(15000, "2026-06-01", "Tienda", id="a"),
            Txn(15000, "2026-06-01", "Cafe", id="b"),
        ]
        line = Txn(15000, "2026-06-01", "")
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.AMBIGUOUS)
        self.assertEqual(len(v.candidates), 2)

    def test_near_date_weak_merchant_is_ambiguous(self):
        reported = [Txn(40000, "2026-06-01", "Tienda Don Jose")]
        line = Txn(40000, "2026-06-03", "Supermercado Exito")  # near date, different merchant
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.AMBIGUOUS)

    def test_unparseable_date_with_amount_match_is_ambiguous(self):
        reported = [Txn(35000, "2026-06-01", "Rappi")]
        line = Txn(35000, "not-a-date", "Rappi")
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.AMBIGUOUS)

    # --- helpers / robustness --------------------------------------------
    def test_float_amounts_compare_by_minor_units(self):
        reported = [Txn(35000.00, "2026-06-01", "Rappi")]
        line = Txn(35000.004, "2026-06-01", "Rappi")  # rounds to same cents
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.MATCH)

    def test_date_formats_normalize(self):
        reported = [Txn(1000, "01/06/2026", "x")]   # dd/mm/yyyy
        line = Txn(1000, "2026-06-01", "x")          # iso
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.MATCH)

    def test_reconcile_statement_batch(self):
        reported = [Txn(35000, "2026-06-01", "Rappi")]
        lines = [
            Txn(35000, "2026-06-01", "Rappi"),   # match
            Txn(9900, "2026-06-02", "Spotify"),  # new
            Txn(35000, "2026-06-20", "Rappi"),   # ambiguous
        ]
        verdicts = rc.reconcile_statement(lines, reported)
        self.assertEqual([v.classification for v in verdicts],
                         [rc.MATCH, rc.NEW, rc.AMBIGUOUS])

    def test_tool_schema_has_function_definition_shape(self):
        # Regression: Hermes/the LLM provider needs {name, description, parameters}.
        # Passing a raw params object poisons the whole tools array (every turn 400s).
        self.assertEqual(rc.TOOL_SCHEMA.get("name"), rc.TOOL_NAME)
        self.assertIn("description", rc.TOOL_SCHEMA)
        params = rc.TOOL_SCHEMA.get("parameters")
        self.assertIsInstance(params, dict)
        self.assertEqual(params.get("type"), "object")
        self.assertIn("lines", params.get("properties", {}))
        # the raw schema fields must NOT be at the top level
        self.assertNotIn("properties", rc.TOOL_SCHEMA)
        self.assertNotIn("type", rc.TOOL_SCHEMA)

    def test_merchant_normalization_strips_noise(self):
        # "compra"/"pos"/digits are noise; core token must still match
        reported = [Txn(20000, "2026-06-01", "Exito")]
        line = Txn(20000, "2026-06-01", "COMPRA POS EXITO 1234")
        v = rc.classify_statement_line(line, reported)
        self.assertEqual(v.classification, rc.MATCH)


if __name__ == "__main__":
    unittest.main()
