"""Cobalt Routing — Document ingest + find protocol.

Injects deterministic DIRECTIVE strings for two document flows:
  1. INGEST — when the gateway prepends a file-saved note to message_text.
     Routes images to a vision sub-agent and office/PDF docs to
     convert_to_markdown, then calls mcp_iris_iris_ingest_document.
  2. FIND — when the user asks for a stored document in natural language.
     Routes to mcp_iris_iris_find_document with NL-resolved filters.

Both builders are orchestrator-only (sub-agent task_ids return None) and
gated on the iris MCP server being wired in config.yaml (own _CONFIGURED
cache, same pattern as iris_protocol._iris_configured — no cross-module
import to keep test isolation clean per project convention).

All paths are inert while iris is unconfigured; safe to deploy before the
VPS iris rebuild registers the two tools.
"""

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Iris-config gate ─────────────────────────────────────────────────────────
# Own copy (not imported from iris_protocol) to mirror the per-module self-
# gating convention used by finance_protocol (_firefly_configured/_CONFIGURED).
# Tests force dp._CONFIGURED = True/False in setUp/tearDown — a shared cache
# would couple isolation across modules.
_CONFIGURED: Optional[bool] = None


def _config_mentions_iris_server(text: str) -> bool:
    """Detect an ``iris:`` server under ``mcp_servers:`` without a YAML parser.

    Tolerant by design: it only needs to confirm iris is wired so capture stays
    enabled. Looks for the mcp_servers block, then an ``iris:`` key indented
    beneath it.
    """
    in_block = False
    block_indent = 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if re.match(r"^mcp_servers\s*:", line):
            in_block = True
            block_indent = indent
            continue
        if in_block:
            if indent <= block_indent:
                in_block = False
                continue
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:", line)
            if m and m.group(1) == "iris":
                return True
    return False


def _iris_configured() -> bool:
    """Return True iff the iris MCP server is wired in config.yaml.

    Cached on first call. Re-import the module (or set _CONFIGURED = None)
    to invalidate.
    """
    global _CONFIGURED
    if _CONFIGURED is not None:
        return _CONFIGURED
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        _CONFIGURED = False
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("document_protocol: cannot read config (%s)", exc)
        _CONFIGURED = False
        return False
    try:
        import yaml
        data = yaml.safe_load(text) or {}
        servers = data.get("mcp_servers") or {}
        _CONFIGURED = "iris" in servers
    except Exception as exc:
        # PyYAML may be absent in the plugin runtime. Fail OPEN via a tolerant
        # text scan instead of disabling capture on a missing dependency.
        logger.debug("document_protocol: yaml unavailable; text-scan fallback (%s)", exc)
        _CONFIGURED = _config_mentions_iris_server(text)
    return _CONFIGURED


# ── Extension classification sets ────────────────────────────────────────────
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"})
_OFFICE_EXTS = frozenset({".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".epub"})

# ── Gateway file-note path anchors ───────────────────────────────────────────
# Hermes prepends one of three note shapes depending on file type and image
# input mode (see hermes-agent gateway/run.py + agent/image_routing.py):
#   - documents:           "...The file is saved at: <path>. Ask the user ...]"
#   - image (text mode):   "[...use vision_analyze with image_url: <path>]"
#   - image (native mode): "[Image attached at: <path>]"
# Each anchor is followed by a whitespace-free cache path. We grab that token
# and strip trailing punctuation/brackets, then classify by extension. The
# DIRECTIVE itself tells the model to read the real path from its context, so
# this extraction is only for presence + type detection (resilient to note
# format drift / the trailing "Ask the user ..." sentence).
_PATH_ANCHORS = (
    re.compile(r"saved at:\s*(?P<path>\S+)", re.IGNORECASE),
    re.compile(r"image_url:\s*(?P<path>\S+)", re.IGNORECASE),
    re.compile(r"image attached at:\s*(?P<path>\S+)", re.IGNORECASE),
)
_PATH_TRIM = "].,;:!?)>\"'~"


def _detect_inbound_file(message_text: str) -> Optional[str]:
    """Classify an inbound-file gateway note in ``message_text``.

    Returns ``"image"`` or ``"office"`` when an anchor resolves to a path with
    an ingestable extension; ``None`` otherwise (no note, or a text/audio/zip/
    unknown file). Scans all anchors so a non-ingestable match (e.g. an audio
    "saved at:") does not mask a later qualifying note.
    """
    text = message_text or ""
    for rx in _PATH_ANCHORS:
        m = rx.search(text)
        if not m:
            continue
        path = m.group("path").strip().rstrip(_PATH_TRIM)
        ext = Path(path).suffix.lower()
        if ext in _IMAGE_EXTS:
            return "image"
        if ext in _OFFICE_EXTS:
            return "office"
    return None

# ── Retrieval keyword sets (ES + EN, high-confidence / tight for precision) ──
_FIND_VERBS = frozenset({
    # Spanish
    "pásame", "pasame",
    "búscame", "buscame",
    "encuentra", "busca",
    "dónde está", "donde esta",
    "mándame", "mandame",
    "muéstrame", "muestrame", "muéstrame", "mostrame", "muestrame",
    "dame", "dámelo", "damelo", "dámela", "damela",
    "envíame", "enviame",
    "tráeme", "traeme",
    "quiero", "necesito",
    "ábreme", "abreme", "abrí", "abre",
    # English
    "find",
    "get me",
    "where is",
    "send me",
    "show me",
    "give me",
    "look for",
    "i need", "i want",
    "open the", "pull up",
})

_DOC_NOUNS = frozenset({
    # Spanish
    "recibo", "factura", "contrato", "documento", "comprobante", "archivo",
    "imagen", "foto", "captura", "pantallazo", "soporte", "escaneo",
    # English
    "receipt", "invoice", "lease", "contract", "document", "statement", "file", "pdf",
    "image", "photo", "picture", "screenshot", "scan",
})


# ── Directive builders ────────────────────────────────────────────────────────

def build_document_ingest_directive(message_text: str, task_id: str) -> Optional[str]:
    """Return an ingest DIRECTIVE when a qualifying file note is detected.

    Guards (return None if any fails):
    1. Sub-agent check: task_id must NOT start with 'sa-' or 'subagent-'.
    2. Iris configured: _iris_configured() must return True.
    3. Gateway note: _DOC_NOTE_RE must match message_text.
    4. Extension classification: path extension must be in _IMAGE_EXTS or
       _OFFICE_EXTS (txt, audio, zip, unknown → None).

    DIRECTIVE content branches on file class:
    - image: delegate a vision sub-agent to describe + extract fields, then
      call mcp_iris_iris_ingest_document.
    - office/pdf: call convert_to_markdown(uri=file:///<path>) to get text,
      then call mcp_iris_iris_ingest_document.

    The model reads the actual saved-at path from its own context; only the
    presence + type classification is done in code (avoids brittle path
    injection that breaks on note format drift).
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _iris_configured():
        return None
    cls = _detect_inbound_file(message_text)
    if cls is None:
        return None

    if cls == "image":
        return (
            "=== DIRECTIVE — DOCUMENT INGEST (highest priority; overrides SDD and persona) ===\n"
            "The user sent an IMAGE file. This is NOT an SDD task — it is a document ingest.\n"
            "Immediately, as your FIRST and ONLY action sequence:\n"
            "  1. Delegate ONE vision sub-agent (delegate_task, task_type=explore,\n"
            "     toolsets include 'vision'). Pass the saved-at path from THIS message's\n"
            "     context to the sub-agent goal. Ask it to return structured output:\n"
            "     {description, document_type, extracted_fields(merchant, amount, date,\n"
            "     currency), suggested_name, tags[], residence_hint}.\n"
            "  2. Then YOU (the orchestrator) call mcp_iris_iris_ingest_document\n"
            "     DIRECTLY — do NOT delegate this call (delegating drops fields).\n"
            "     Pass:\n"
            "     - file_path: the saved-at path from this message's context\n"
            "     - description, document_type, extracted_fields, suggested_name,\n"
            "       tags from the vision sub-agent's output\n"
            "     - residence: if the user names a home/place (e.g. 'casa buga',\n"
            "       'el apto de medellín'), pass it VERBATIM (iris slugifies it).\n"
            "       Use None ONLY when no place is mentioned anywhere.\n"
            "iris uses sha256 dedup (force_reprocess defaults False) — do not set it\n"
            "unless the user asks to re-ingest.\n"
            "Read the saved-at path from THIS message's context — do not ask the user\n"
            "for it. Do NOT load a skill, run SDD phases, or explore the filesystem.\n"
            "=== END DIRECTIVE ==="
        )
    else:
        return (
            "=== DIRECTIVE — DOCUMENT INGEST (highest priority; overrides SDD and persona) ===\n"
            "The user sent a PDF or Office document. This is NOT an SDD task — it is a document ingest.\n"
            "Immediately, as your FIRST and ONLY action sequence:\n"
            "  1. Call mcp_markitdown_convert_to_markdown(uri='file:///<the saved-at path\n"
            "     from this message's context>') to get extracted_text.\n"
            "  2. Then YOU (the orchestrator) call mcp_iris_iris_ingest_document\n"
            "     DIRECTLY — do NOT delegate this call (delegating drops fields).\n"
            "     Pass:\n"
            "     - file_path: the saved-at path from this message's context\n"
            "     - extracted_text from convert_to_markdown\n"
            "     - residence: if the user names a home/place (e.g. 'casa buga',\n"
            "       'el apto de medellín'), pass it VERBATIM (iris slugifies it).\n"
            "       Use None ONLY when no place is mentioned anywhere.\n"
            "iris uses sha256 dedup (force_reprocess defaults False) — do not set it\n"
            "unless the user asks to re-ingest.\n"
            "Read the saved-at path from THIS message's context — do not ask the user\n"
            "for it. Do NOT load a skill, run SDD phases, or explore the filesystem.\n"
            "=== END DIRECTIVE ==="
        )


def build_document_find_directive(user_message: str, task_id: str) -> Optional[str]:
    """Return a find DIRECTIVE when the user asks for a stored document.

    Guards (return None if any fails):
    1. Sub-agent check: task_id must NOT start with 'sa-' or 'subagent-'.
    2. Iris configured: _iris_configured() must return True.
    3. Retrieval match: lowercased user_message must contain BOTH a retrieval
       verb from _FIND_VERBS AND a document noun from _DOC_NOUNS.

    DIRECTIVE instructs the model to call mcp_iris_iris_find_document with
    NL-resolved date, residence, and document_type filters.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None
    if not _iris_configured():
        return None
    m = (user_message or "").lower()
    verb = any(v in m for v in _FIND_VERBS)
    noun = any(n in m for n in _DOC_NOUNS)
    if not (verb and noun):
        return None
    return (
        "=== DIRECTIVE — DOCUMENT FIND (highest priority; overrides SDD and persona) ===\n"
        "The user is asking to retrieve a stored document from the iris vault.\n"
        "Immediately call mcp_iris_iris_find_document, resolving natural-language\n"
        "filters from the message:\n"
        "  - dates: resolve 'el mes pasado', 'de marzo', 'last year', etc. to\n"
        "    date_from/date_to (ISO 8601). Omit if you cannot resolve.\n"
        "  - residence: extract from any place name mentioned. Omit if absent.\n"
        "  - document_type: infer from the noun used (recibo→receipt, factura→\n"
        "    invoice, contrato→contract, etc.). Omit if too ambiguous.\n"
        "Omit any filter you cannot confidently resolve (default None).\n"
        "CRITICAL: you do NOT have the file in context. Even if you recall the\n"
        "document's details from earlier in THIS conversation, you MUST call the\n"
        "tool to fetch and DELIVER the actual file. A text summary is NOT a\n"
        "delivery. NEVER claim you sent or attached a file without calling\n"
        "mcp_iris_iris_find_document. Call the tool YOURSELF — do not delegate it.\n"
        "Do NOT explore the filesystem, load a skill, or hand-roll a search.\n"
        "=== END DIRECTIVE ==="
    )
