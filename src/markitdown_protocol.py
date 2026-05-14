"""Cobalt Routing — Markitdown File-Conversion Protocol.

Injects a mandatory rule on every orchestrator turn: any binary / office /
audio / image file MUST be converted to Markdown via the `convert_to_markdown`
MCP tool (markitdown-mcp, Microsoft official) BEFORE being read.

Why: LLMs that read PDFs / DOCX / XLSX directly burn tokens on encoded /
binary content. Markitdown converts to clean Markdown that LLMs understand
natively, saving 60-90% of tokens for the same information.

This is rule-based, not LLM-decision-based — the rule appears on EVERY
orchestrator turn so the model cannot "forget" to apply it. Cobalt does
not actually intercept the tool call (file paths can be obscured behind
relative paths, terminal commands, etc.), so enforcement relies on the
strict instruction.

When markitdown-mcp is not installed (env opted out), the protocol is
silently disabled — the rule is only injected if the MCP server is wired
in config.yaml.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Extensions that should always be routed through convert_to_markdown.
BINARY_EXTENSIONS = (
    # Office
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    # E-books
    ".epub",
    # Images (EXIF + OCR)
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff",
    # Audio (transcription)
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    # Structured data
    ".csv", ".xml",
    # Archives
    ".zip",
)


MARKITDOWN_PROTOCOL_BLOCK = """
[MANDATORY FILE-CONVERSION PROTOCOL — markitdown]

Microsoft's markitdown MCP server is wired into this Hermes install. It
exposes `convert_to_markdown(uri)` which accepts file://, http://, https://,
or data: URIs and returns clean Markdown.

# WHEN TO USE (mandatory)
For ANY file matching these formats, you MUST call `convert_to_markdown`
BEFORE reading via read_file / terminal / any other tool:
- Office:  .pdf .docx .doc .pptx .ppt .xlsx .xls
- Images:  .png .jpg .jpeg .webp .gif .bmp .tiff (EXIF + OCR)
- Audio:   .mp3 .wav .m4a .ogg .flac (speech transcription)
- E-books: .epub
- Data:    .csv .xml .zip
- URLs:    YouTube links, web pages with heavy formatting

# CALL PATTERN (deterministic)
For a local file:
    convert_to_markdown(uri="file:///absolute/path/to/document.pdf")
For a remote URL:
    convert_to_markdown(uri="https://example.com/report.pdf")

The tool returns the file content as Markdown. Read THAT, not the original.

# WHY THIS IS MANDATORY
Reading a 100-page PDF directly burns ~150k tokens on encoded content
that the model cannot understand. Converting via markitdown first yields
clean Markdown that costs ~10-20k tokens for the same information — and
the model can actually parse it.

# EXCEPTIONS
- Plain text files (.txt, .md, .py, .ts, .json, .yaml, etc.) — read directly
- HTML — markitdown still helps strip noise; prefer it when the page is
  heavy (modern web pages with JS noise, ads, navigation).

The orchestrator does NOT auto-intercept file reads — this rule's
enforcement depends on you applying it consistently.
"""


def build_markitdown_protocol_block(task_id: str = "") -> Optional[str]:
    """Return the protocol block to inject on this turn.

    - Sub-agents: never injected (they get the rule via goal suffix elsewhere)
    - Orchestrator: always injected when markitdown is configured
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _markitdown_configured():
        return None
    return MARKITDOWN_PROTOCOL_BLOCK


def _markitdown_configured() -> bool:
    """Return True iff markitdown MCP server is wired in config.yaml.

    Cached on first call. Re-import the module to invalidate.
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
        logger.debug("markitdown_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    servers = (data.get("mcp_servers") or {})
    _CONFIGURED = "markitdown" in servers
    return _CONFIGURED


_CONFIGURED: Optional[bool] = None


SUBAGENT_MARKITDOWN_RIDER = (
    "\n\n[FILE CONVERSION — sub-agent rule]\n"
    "If your task involves reading files in formats other than plain "
    "text/code/markdown (PDF, DOCX, XLSX, PPTX, PNG, JPG, MP3, WAV, "
    "EPUB, CSV, XML, ZIP, or heavy HTML), you MUST call "
    "`convert_to_markdown(uri=\"file:///<absolute-path>\")` first and "
    "read the returned Markdown instead. Reading binary content directly "
    "burns tokens for no information gain."
)


def subagent_markitdown_rider() -> str:
    """Suffix appended to sub-agent goals when markitdown is configured."""
    if not _markitdown_configured():
        return ""
    return SUBAGENT_MARKITDOWN_RIDER
