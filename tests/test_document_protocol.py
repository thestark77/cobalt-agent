"""Unit tests for document_protocol.py (cobalt-document-wiring). Stdlib-only.

Strict TDD: tests written FIRST (RED) before any implementation.
setUp forces dp._CONFIGURED = True; tearDown resets to None for test isolation.
"""

import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import document_protocol as dp  # noqa: E402


class TestBuildDocumentIngestDirective(unittest.TestCase):
    """Tests for build_document_ingest_directive."""

    def setUp(self):
        dp._CONFIGURED = True

    def tearDown(self):
        dp._CONFIGURED = None

    # --- happy paths (REAL Hermes gateway note formats) ---
    # Image text-mode note (vision pre-run): description bracket + image_url hint.
    IMG_TEXT_NOTE = (
        "[The user sent an image. Here's what I can see:\n"
        "A utility payment receipt.]\n"
        "[If you need a closer look, use vision_analyze with "
        "image_url: /home/sebas/.hermes/cache/images/abc123.jpg]"
    )
    # Image native-mode hint (model gets pixels inline).
    IMG_NATIVE_NOTE = "[Image attached at: /home/sebas/.hermes/cache/images/def456.png]"
    # Document note: trailing "Ask the user ..." sentence after the path.
    PDF_NOTE = (
        "[The user sent a document: 'receipt.pdf'. The file is saved at: "
        "/home/sebas/.hermes/cache/documents/x_receipt.pdf. "
        "Ask the user what they'd like you to do with it.]"
    )
    DOCX_NOTE = (
        "[The user sent a document: 'contract.docx'. The file is saved at: "
        "/home/sebas/.hermes/cache/documents/y_contract.docx. "
        "Ask the user what they'd like you to do with it.]"
    )

    def test_image_text_mode_fires(self):
        """Real image text-mode note (image_url hint) triggers ingest."""
        result = dp.build_document_ingest_directive(self.IMG_TEXT_NOTE, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("vision", result)
        self.assertIn("mcp_iris_iris_ingest_document", result)

    def test_image_native_mode_fires(self):
        """Real image native-mode hint ('[Image attached at: ...]') triggers ingest."""
        result = dp.build_document_ingest_directive(self.IMG_NATIVE_NOTE, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("vision", result)
        self.assertIn("mcp_iris_iris_ingest_document", result)

    def test_pdf_fires(self):
        """Real document note (.pdf, trailing sentence) triggers ingest."""
        result = dp.build_document_ingest_directive(self.PDF_NOTE, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("convert_to_markdown", result)
        self.assertIn("mcp_iris_iris_ingest_document", result)

    def test_docx_fires(self):
        """Real document note (.docx, trailing sentence) triggers ingest."""
        result = dp.build_document_ingest_directive(self.DOCX_NOTE, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("convert_to_markdown", result)
        self.assertIn("mcp_iris_iris_ingest_document", result)

    def test_directive_has_ingest_header(self):
        """Ingest directive has the correct priority header."""
        result = dp.build_document_ingest_directive(self.PDF_NOTE, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("DOCUMENT INGEST", result)

    # --- guard failures → None ---

    def test_txt_text_document_returns_none(self):
        """Real text-document note (.txt, 'also saved at:') does NOT trigger."""
        msg = (
            "[The user sent a text document: 'notes.txt'. Its content has been "
            "included below. The file is also saved at: /tmp/notes.txt]"
        )
        result = dp.build_document_ingest_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_audio_note_returns_none(self):
        """Real audio note (.mp3, 'It is saved at:') does NOT trigger."""
        msg = (
            "[The user sent an audio file attachment: 'voice.mp3'. It is saved "
            "at: /tmp/voice.mp3. Ask the user what they'd like you to do with it.]"
        )
        result = dp.build_document_ingest_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_not_configured_returns_none(self):
        """When iris is not configured, no directive is returned."""
        dp._CONFIGURED = False
        result = dp.build_document_ingest_directive(self.PDF_NOTE, "orchestrator-main")
        self.assertIsNone(result)

    def test_subagent_sa_prefix_returns_none(self):
        """Sub-agent task_id with 'sa-' prefix returns None even with valid image note."""
        result = dp.build_document_ingest_directive(self.IMG_TEXT_NOTE, "sa-1")
        self.assertIsNone(result)

    def test_subagent_subagent_prefix_returns_none(self):
        """Sub-agent task_id with 'subagent-' prefix returns None."""
        result = dp.build_document_ingest_directive(self.DOCX_NOTE, "subagent-7")
        self.assertIsNone(result)

    def test_no_gateway_note_returns_none(self):
        """Plain conversation text without a gateway note returns None."""
        msg = "Hey, how is the project going? Can you review the plan?"
        result = dp.build_document_ingest_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_none_message_returns_none(self):
        """None message_text is handled gracefully."""
        result = dp.build_document_ingest_directive(None, "orchestrator-main")
        self.assertIsNone(result)


class TestBuildDocumentFindDirective(unittest.TestCase):
    """Tests for build_document_find_directive."""

    def setUp(self):
        dp._CONFIGURED = True

    def tearDown(self):
        dp._CONFIGURED = None

    # --- happy paths ---

    def test_es_phrase_fires(self):
        """Spanish retrieval phrase triggers a find directive."""
        msg = "pásame el recibo de gas de casabuga del mes pasado"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("mcp_iris_iris_find_document", result)

    def test_en_phrase_fires(self):
        """English retrieval phrase triggers a find directive."""
        msg = "find the lease contract from last year"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("mcp_iris_iris_find_document", result)

    def test_directive_has_find_header(self):
        """Find directive has the correct priority header."""
        msg = "find the invoice for last month"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNotNone(result)
        self.assertIn("DOCUMENT FIND", result)

    # --- guard failures → None ---

    def test_verb_only_returns_none(self):
        """Message with retrieval verb but no document noun returns None."""
        msg = "pásame eso"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_noun_only_returns_none(self):
        """Message with document noun but no retrieval verb returns None."""
        msg = "el recibo está acá en la mesa"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_chitchat_returns_none(self):
        """Generic chit-chat returns None."""
        msg = "how are you today?"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_not_configured_returns_none(self):
        """When iris is not configured, no directive is returned."""
        dp._CONFIGURED = False
        msg = "find the receipt"
        result = dp.build_document_find_directive(msg, "orchestrator-main")
        self.assertIsNone(result)

    def test_subagent_sa_prefix_returns_none(self):
        """Sub-agent task_id with 'sa-' prefix returns None."""
        msg = "find the receipt from last month"
        result = dp.build_document_find_directive(msg, "sa-5")
        self.assertIsNone(result)

    def test_subagent_subagent_prefix_returns_none(self):
        """Sub-agent task_id with 'subagent-' prefix returns None."""
        msg = "pásame el contrato de arriendo"
        result = dp.build_document_find_directive(msg, "subagent-7")
        self.assertIsNone(result)

    def test_none_message_returns_none(self):
        """None user_message is handled gracefully."""
        result = dp.build_document_find_directive(None, "orchestrator-main")
        self.assertIsNone(result)


class TestIrisConfiguredCache(unittest.TestCase):
    """Tests for the _CONFIGURED module cache behavior."""

    def setUp(self):
        dp._CONFIGURED = True

    def tearDown(self):
        dp._CONFIGURED = None

    def test_configured_true_no_fs_read(self):
        """When _CONFIGURED=True, _iris_configured() returns True without FS read."""
        dp._CONFIGURED = True
        self.assertTrue(dp._iris_configured())

    def test_configured_false_no_fs_read(self):
        """When _CONFIGURED=False, _iris_configured() returns False without FS read."""
        dp._CONFIGURED = False
        self.assertFalse(dp._iris_configured())


class TestWiringAssertions(unittest.TestCase):
    """Wiring assertions: tool_guard, incognito, iris_protocol."""

    def test_orchestrator_allowed_contains_ingest(self):
        """tool_guard.ORCHESTRATOR_ALLOWED contains mcp_iris_iris_ingest_document."""
        import tool_guard
        self.assertIn("mcp_iris_iris_ingest_document", tool_guard.ORCHESTRATOR_ALLOWED)

    def test_orchestrator_allowed_contains_find(self):
        """tool_guard.ORCHESTRATOR_ALLOWED contains mcp_iris_iris_find_document."""
        import tool_guard
        self.assertIn("mcp_iris_iris_find_document", tool_guard.ORCHESTRATOR_ALLOWED)

    def test_write_tools_contains_ingest(self):
        """incognito.WRITE_TOOLS contains mcp_iris_iris_ingest_document."""
        import incognito
        self.assertIn("mcp_iris_iris_ingest_document", incognito.WRITE_TOOLS)

    def test_write_tools_does_not_contain_find(self):
        """incognito.WRITE_TOOLS does NOT contain mcp_iris_iris_find_document (read-only)."""
        import incognito
        self.assertNotIn("mcp_iris_iris_find_document", incognito.WRITE_TOOLS)

    def test_iris_protocol_block_contains_ingest_document(self):
        """iris_protocol.IRIS_PROTOCOL_BLOCK mentions ingest_document."""
        import iris_protocol
        self.assertIn("ingest_document", iris_protocol.IRIS_PROTOCOL_BLOCK)

    def test_iris_protocol_block_contains_find_document(self):
        """iris_protocol.IRIS_PROTOCOL_BLOCK mentions find_document."""
        import iris_protocol
        self.assertIn("find_document", iris_protocol.IRIS_PROTOCOL_BLOCK)


class TestSoulMd(unittest.TestCase):
    """SOUL.md contains the cobalt:documents managed block."""

    def _soul_text(self):
        soul = Path(__file__).resolve().parent.parent / "SOUL.md"
        return soul.read_text(encoding="utf-8")

    def test_soul_contains_cobalt_documents(self):
        """SOUL.md contains 'cobalt:documents'."""
        self.assertIn("cobalt:documents", self._soul_text())

    def test_soul_contains_ingest_document(self):
        """SOUL.md contains 'ingest_document'."""
        self.assertIn("ingest_document", self._soul_text())

    def test_soul_contains_find_document(self):
        """SOUL.md contains 'find_document'."""
        self.assertIn("find_document", self._soul_text())
