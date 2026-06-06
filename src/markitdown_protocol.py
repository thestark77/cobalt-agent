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
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

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


# Subset that is HARD-intercepted at the pre_tool_call hook (deterministic
# redirect to convert_to_markdown). Scope is deliberately narrower than the
# soft-instruction list above:
#   - Office / e-book / audio / archive: `read_file` refuses these outright
#     ("Cannot read images or binary files") and a terminal `cat` burns tokens
#     on raw bytes — markitdown is unambiguously the right tool.
#   - Images are NOT hard-intercepted: Hermes ships a dedicated `vision_analyze`
#     tool that beats OCR for general images. markitdown stays the recommended
#     path for OCR/EXIF via the soft instruction only.
#   - .csv / .xml / .txt are NOT hard-intercepted: they are plain text, cheap to
#     read directly, and converting them adds latency for no token saving.
AUTO_CONVERT_EXTENSIONS = (
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".epub",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".zip",
)

# Matches a path-like token ending in a convertible extension (used to scan
# terminal commands / user messages). The trailing lookahead `(?=[^.\w]|$)`
# replaces a plain `\b` so that `report.pdf.bak` does NOT match as `report.pdf`
# (the extension must be the real, final one).
_EXT_ALT = "|".join(ext.lstrip(".") for ext in AUTO_CONVERT_EXTENSIONS)
_PATH_TOKEN_RE = re.compile(
    r"""(?P<path>(?:~|\.{0,2}/)?[^\s'"|;&><]+\.(?:""" + _EXT_ALT + r"""))(?=[^.\w]|$)""",
    re.IGNORECASE,
)

# Terminal verbs that DUMP raw file bytes into the context (token waste). A
# command only triggers interception when one of these reads a convertible
# file; `ls *.pdf`, `mv a.pdf b.pdf`, `pdftotext a.pdf` (already a conversion)
# are intentionally left alone. Both anchors are strict on purpose: the left
# `(?:^|[\s;|&(])` and the right `(?:\s|$)` together prevent substring false
# positives like `concatenate`, `category`, `wildcat`, or `cat_tool`.
_READ_VERB_RE = re.compile(
    r"(?:^|[\s;|&(])(?:cat|bat|head|tail|less|more|nl|strings|xxd|od|hexdump)(?:\s|$)",
    re.IGNORECASE,
)

# Explicit per-turn opt-out phrases ("read it raw / don't convert"). Kept small
# and specific so an unrelated message never disables conversion by accident.
_OPTOUT_PHRASES = (
    "sin convertir", "no conviertas", "no convertir", "sin markitdown",
    "no markitdown", "no uses markitdown", "léelo crudo", "leelo crudo",
    "léelo en crudo", "leelo en crudo", "archivo crudo", "sin conversión",
    "sin conversion", "without converting", "don't convert", "do not convert",
    "read it raw", "read raw", "no conversion",
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
- Plain text files (.txt, .md, .py, .ts, .json, .yaml, .csv, .xml, etc.) —
  read directly; converting them saves no tokens.
- Images (.png .jpg ...) — Hermes' `vision_analyze` is preferred for general
  images; use convert_to_markdown only when you specifically need OCR/EXIF text.
- HTML — markitdown still helps strip noise; prefer it when the page is
  heavy (modern web pages with JS noise, ads, navigation).

# ENFORCEMENT (automatic)
This is NOT advisory for office/e-book/audio/archive files: cobalt-routing
intercepts raw reads of them at the tool-call layer and redirects you to
convert_to_markdown deterministically. You cannot bypass it by reading the
file with `cat`/`read_file` — the call is blocked with a redirect message.
The only way to read such a file raw is when the user explicitly asks for it
(e.g. "léelo sin convertir" / "read it raw").
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


# ---------------------------------------------------------------------------
# Automatic interception (deterministic redirect to convert_to_markdown).
#
# The DocumentHandler that ingests an uploaded file lives in Hermes core, which
# this plugin does not modify. The plugin's only deterministic lever is the
# pre_tool_call hook, so "automatic" is enforced at READ time: whenever any
# agent tries to read a convertible file (read_file, or a `cat`-style terminal
# command), the call is blocked and the agent is told to call
# convert_to_markdown instead. This does not depend on model compliance.
# ---------------------------------------------------------------------------

# Latest human message, captured by the pre_llm_call hook. Used only to honor a
# per-turn "read it raw" opt-out from inside the pre_tool_call hook (which does
# not otherwise see the user's natural-language request). Single-user reactive
# host → a module global is an acceptable bridge.
_LAST_USER_MESSAGE: str = ""


def note_user_message(user_message: str) -> None:
    """Record the latest human message so the opt-out check can see it."""
    global _LAST_USER_MESSAGE
    _LAST_USER_MESSAGE = user_message or ""


def _auto_intercept_enabled() -> bool:
    """True unless the operator disabled it via COBALT_MARKITDOWN_AUTO.

    Default ON. Any of 0/false/no/off (case-insensitive) disables it, reverting
    to soft-instruction-only behavior. Also requires markitdown to be wired.
    """
    raw = os.environ.get("COBALT_MARKITDOWN_AUTO", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return _markitdown_configured()


def _user_opted_out() -> bool:
    """True if the latest human message explicitly asked to skip conversion."""
    msg = _LAST_USER_MESSAGE.lower()
    return any(phrase in msg for phrase in _OPTOUT_PHRASES)


def _to_file_uri(path: str) -> str:
    """Best-effort absolute, percent-encoded file:// URI for a matched path.

    Paths with spaces are common (invoices, bank statements), so encode them so
    convert_to_markdown's URI parser does not choke.
    """
    try:
        expanded = os.path.abspath(os.path.expanduser(path))
    except Exception:
        expanded = path
    return "file://" + quote(expanded, safe="/")


def _redirect_block(path: str) -> dict:
    """The block directive returned to the agent for a convertible file read."""
    ext = os.path.splitext(path)[1].lower()
    uri = _to_file_uri(path)
    return {
        "action": "block",
        "message": (
            f"[markitdown] Raw read of a convertible file ({ext}) is blocked to "
            f"avoid burning tokens on binary content.\n"
            f"Convert it first, then read the returned Markdown:\n"
            f'    convert_to_markdown(uri="{uri}")\n'
            f"Do NOT read the original file directly. (If the user explicitly "
            f'asked to read it raw — e.g. "léelo sin convertir" — they must say '
            f"so; then this block is lifted.)"
        ),
    }


def intercept_file_read(tool_name: str, args: dict) -> Optional[dict]:
    """Return a block directive if `tool_name` is reading a convertible file.

    Deterministic enforcement of automatic markitdown conversion. Fail-open:
    any unexpected shape returns None (the read proceeds normally).

    Handled tools:
      - read_file            → args["path"]
      - terminal / process   → args["command"] (only when a `cat`-style read
                               verb targets a convertible path)
    """
    if not isinstance(args, dict):
        return None
    if not _auto_intercept_enabled() or _user_opted_out():
        return None

    if tool_name == "read_file":
        path = args.get("path")
        if isinstance(path, str) and path.lower().endswith(AUTO_CONVERT_EXTENSIONS):
            return _redirect_block(path)
        return None

    if tool_name in ("terminal", "process"):
        command = args.get("command")
        if not isinstance(command, str) or not command:
            # tolerate the nested tool_input shape the firewall also handles
            tool_input = args.get("tool_input") or args.get("input") or {}
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command:
            return None
        if not _READ_VERB_RE.search(command):
            return None
        match = _PATH_TOKEN_RE.search(command)
        if match:
            path = match.group("path")
            # An unexpanded shell glob (`less *.pdf`) cannot be resolved to a
            # real file statically — let it through rather than emit a bogus URI.
            if "*" in path or "?" in path:
                return None
            return _redirect_block(path)
        return None

    return None


def build_convert_first_directive(
    user_message: str = "", task_id: str = ""
) -> Optional[str]:
    """Proactive complement to the pre_tool_call safety net.

    If the incoming human message references a convertible file by path, inject a
    turn-0 directive naming it so the agent converts BEFORE attempting any read,
    rather than getting bounced by the interception. No-op for sub-agents, when
    auto-interception is disabled, on opt-out, or when no path is present.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _auto_intercept_enabled() or _user_opted_out():
        return None
    if not user_message:
        return None
    # Only fire on genuine path-like tokens (a separator or ~ / tmp prefix),
    # not a bare "report.pdf" mentioned in passing.
    paths = []
    for m in _PATH_TOKEN_RE.finditer(user_message):
        tok = m.group("path")
        if "/" in tok or tok.startswith("~"):
            paths.append(tok)
    if not paths:
        return None
    seen = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    listed = "\n".join(f'    convert_to_markdown(uri="{_to_file_uri(p)}")' for p in seen)
    return (
        "[MANDATORY — convert uploaded file(s) first]\n"
        "The user referenced convertible file(s) this turn. Before reading them "
        "with any tool, your FIRST action MUST be:\n"
        f"{listed}\n"
        "Then work from the returned Markdown. Reading them raw is blocked."
    )


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
