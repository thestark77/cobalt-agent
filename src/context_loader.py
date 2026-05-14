"""Cobalt Routing — Inline CONTEXT.md loader.

Replaces the previous "Step 0.5" pattern where SOUL.md asked the orchestrator
to delegate a scout sub-agent every new session just to read CONTEXT.md from
the cwd. That cost ~60s per session plus the sub-agent's tokens and added a
round trip for what is fundamentally a single `cat`.

This module reads CONTEXT.md directly from the orchestrator's working
directory the first time we see a given `session_id`, caches the resulting
block in-process, and exposes it as a pre_llm_call injection so the content
lands in the system prompt automatically. Subsequent turns of the same
session reuse the cache (no re-read).

Lookup order (first hit wins):
  1. $PWD/CONTEXT.md
  2. Git repo root from $PWD (if inside a git work tree) + CONTEXT.md
  3. $HOME/CONTEXT.md (last-resort default for non-project shells)

A missing CONTEXT.md is a normal case — we cache the empty result and stay
silent. Read errors (permission denied, malformed encoding) are also cached
as empty + logged once, so we don't spam the log every turn.

Sub-agents never trigger a re-read; they inherit their parent's context
via the goal text instead.
"""

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, str] = {}
_CACHE_LOCK = threading.Lock()
_MAX_BYTES = 32 * 1024  # 32KB safety cap — anything larger isn't context, it's noise.


def _git_root_from(cwd: Path) -> Optional[Path]:
    """Best-effort git toplevel resolution. Returns None if cwd isn't in a git tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return Path(out) if out else None


def _resolve_context_file() -> Optional[Path]:
    cwd = Path(os.getcwd())
    candidates = [cwd / "CONTEXT.md"]

    git_root = _git_root_from(cwd)
    if git_root is not None:
        candidates.append(git_root / "CONTEXT.md")

    home = Path.home()
    candidates.append(home / "CONTEXT.md")

    seen: set = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _read_context_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("cobalt-routing: cannot read %s (%s)", path, exc)
        return ""
    if len(data) > _MAX_BYTES:
        logger.warning(
            "cobalt-routing: %s exceeds %d bytes — truncating; trim the file or split it.",
            path, _MAX_BYTES,
        )
        data = data[:_MAX_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("cobalt-routing: %s is not valid UTF-8; skipping", path)
        return ""
    text = text.strip()
    if not text:
        return ""

    return (
        "[PROJECT CONTEXT — loaded from "
        f"{path}]\n"
        "The following file lives in the current project's working directory. "
        "Treat its contents as project-specific rules that apply to ALL "
        "subsequent work in this session. Do NOT delegate a scout to read it "
        "again — it is already loaded below.\n\n"
        f"{text}\n"
    )


def build_context_block(task_id: str = "", session_id: str = "") -> Optional[str]:
    """Return the CONTEXT.md block to inject, or None.

    - Sub-agents never get it injected (they inherit via goal text from parent).
    - Same session_id reuses the cached result. Different session → re-resolve.
    - Empty CONTEXT.md, missing file, or non-orchestrator caller → None.
    """
    if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
        return None

    cache_key = session_id or "_default_"
    with _CACHE_LOCK:
        if cache_key in _CACHE:
            cached = _CACHE[cache_key]
            return cached if cached else None

        path = _resolve_context_file()
        if path is None:
            _CACHE[cache_key] = ""
            logger.info("cobalt-routing: no CONTEXT.md found for session=%s", cache_key)
            return None

        block = _read_context_file(path)
        _CACHE[cache_key] = block
        if block:
            logger.info(
                "cobalt-routing: CONTEXT.md loaded from %s for session=%s (%d chars)",
                path, cache_key, len(block),
            )
        return block if block else None


def reset_cache() -> None:
    """Drop the in-process cache. Useful for tests and for hot-reload scenarios."""
    with _CACHE_LOCK:
        _CACHE.clear()
