"""Unit tests for the automatic markitdown interception (pre_tool_call).

Stdlib-only (unittest) so it runs anywhere with `python3 -m unittest`. The
plugin sources live flat under src/, so we add that to sys.path. Conversion
gating on `_markitdown_configured()` is forced True here to exercise the logic
without a real ~/.hermes/config.yaml.
"""

import os
import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import markitdown_protocol as md  # noqa: E402


class MarkitdownInterceptTest(unittest.TestCase):
    def setUp(self):
        # Force "markitdown configured" and a clean opt-out state per test.
        md._CONFIGURED = True
        md.note_user_message("")
        os.environ.pop("COBALT_MARKITDOWN_AUTO", None)

    # --- read_file ---------------------------------------------------------
    def test_read_file_pdf_is_blocked_and_redirected(self):
        block = md.intercept_file_read("read_file", {"path": "/tmp/factura.pdf"})
        self.assertIsNotNone(block)
        self.assertEqual(block["action"], "block")
        self.assertIn("convert_to_markdown", block["message"])
        self.assertIn("file:///tmp/factura.pdf", block["message"])

    def test_read_file_docx_xlsx_audio_blocked(self):
        for p in ("/a/b.docx", "~/x.xlsx", "rec.mp3", "deck.pptx", "book.epub"):
            self.assertIsNotNone(
                md.intercept_file_read("read_file", {"path": p}), p
            )

    def test_read_file_plaintext_passes(self):
        for p in ("notes.md", "main.py", "data.csv", "feed.xml", "a.txt", "c.json"):
            self.assertIsNone(
                md.intercept_file_read("read_file", {"path": p}), p
            )

    def test_read_file_image_not_hard_intercepted(self):
        # Images go through Hermes vision_analyze, not markitdown.
        self.assertIsNone(md.intercept_file_read("read_file", {"path": "photo.png"}))

    # --- terminal ----------------------------------------------------------
    def test_terminal_cat_pdf_blocked(self):
        block = md.intercept_file_read("terminal", {"command": "cat /tmp/report.pdf"})
        self.assertIsNotNone(block)
        self.assertIn("convert_to_markdown", block["message"])

    def test_terminal_non_read_verbs_pass(self):
        for cmd in ("ls *.pdf", "mv a.pdf b.pdf", "rm old.docx",
                    "pdftotext a.pdf out.txt", "echo hello",
                    # substring false-positive guards
                    "concatenate the logs", "category.docx is the name"):
            self.assertIsNone(
                md.intercept_file_read("terminal", {"command": cmd}), cmd
            )

    def test_terminal_glob_is_not_intercepted(self):
        # Unexpanded shell glob cannot resolve to a real file statically.
        self.assertIsNone(md.intercept_file_read("terminal", {"command": "less *.pdf"}))
        self.assertIsNone(md.intercept_file_read("terminal", {"command": "cat report?.pdf"}))

    def test_double_extension_not_misrouted(self):
        # report.pdf.bak must NOT be redirected to report.pdf.
        self.assertIsNone(md.intercept_file_read("read_file", {"path": "report.pdf.bak"}))

    def test_terminal_head_xlsx_blocked(self):
        self.assertIsNotNone(
            md.intercept_file_read("terminal", {"command": "head -c 200 ~/books/q1.xlsx"})
        )

    # --- opt-out -----------------------------------------------------------
    def test_per_turn_optout_lifts_block(self):
        md.note_user_message("Por favor léelo sin convertir, lo necesito crudo")
        self.assertIsNone(md.intercept_file_read("read_file", {"path": "/tmp/x.pdf"}))

    def test_optout_is_lifted_on_next_clean_message(self):
        md.note_user_message("léelo sin convertir")
        self.assertIsNone(md.intercept_file_read("read_file", {"path": "/tmp/x.pdf"}))
        # Next turn's message has no opt-out -> enforcement restored.
        md.note_user_message("ahora sí, resumime el archivo")
        self.assertIsNotNone(md.intercept_file_read("read_file", {"path": "/tmp/x.pdf"}))

    def test_env_optout_disables(self):
        os.environ["COBALT_MARKITDOWN_AUTO"] = "0"
        self.assertIsNone(md.intercept_file_read("read_file", {"path": "/tmp/x.pdf"}))

    def test_not_configured_disables(self):
        md._CONFIGURED = False
        self.assertIsNone(md.intercept_file_read("read_file", {"path": "/tmp/x.pdf"}))

    # --- proactive directive ----------------------------------------------
    def test_convert_first_directive_names_path(self):
        out = md.build_convert_first_directive(
            user_message="mira esta factura ~/docs/factura.pdf y resumila", task_id=""
        )
        self.assertIsNotNone(out)
        self.assertIn("convert_to_markdown", out)
        self.assertIn("factura.pdf", out)

    def test_convert_first_ignores_bare_filename(self):
        # No path separator -> not treated as an upload reference.
        out = md.build_convert_first_directive(
            user_message="el archivo report.pdf que te mande ayer", task_id=""
        )
        self.assertIsNone(out)

    def test_convert_first_noop_for_subagent(self):
        out = md.build_convert_first_directive(
            user_message="convierte ~/x.pdf", task_id="sa-1-abc"
        )
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
