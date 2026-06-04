"""Cobalt Routing — Deterministic Iris memory capture.

Why this exists: the orchestrator LLM is probabilistic about calling the
remember tool, so durable facts the user states in passing (employer, family,
health, decisions) were being lost. This module makes capture DETERMINISTIC:
it runs on every orchestrator turn regardless of what the model decides.

Decoupling guarantees (cobalt-agent must work WITHOUT iris):
  - Gated on the iris MCP server being configured in ~/.hermes/config.yaml
    (same gate as iris_protocol). With no iris, ``maybe_capture`` returns
    immediately — Cobalt behaves exactly as before.
  - Writes to Engram (``POST /observations`` on :7437), the memory store Cobalt
    depends on natively. It NEVER calls any iris.* tool. Iris's sync worker
    ingests the Engram write afterwards, on its own.
  - Fail-open: every error is swallowed; the turn and Cobalt are never affected.
  - Runs in a daemon thread — never blocks the user-facing response.

Flow per turn: cheap LLM extraction ("is there a durable user fact here?") ->
if yes, write it to Engram. The extraction credential is OPENROUTER_API_KEY
read from ~/iris-ai/.env, which only exists when iris is installed (consistent
with the gate).
"""

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Reuse the iris gate so capture is active iff iris is configured.
try:
    from iris_protocol import _iris_configured
except Exception:  # pragma: no cover - defensive (partial install)
    def _iris_configured() -> bool:  # type: ignore
        return False

_IRIS_ENV = Path.home() / "iris-ai" / ".env"
_DEFAULT_ENGRAM = "http://127.0.0.1:7437"
_DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_DEFAULT_CAPTURE_MODEL = "openai/gpt-4o-mini"
_CAPTURE_SESSION = "iris-capture"
_CAPTURE_PROJECT = "sebas"  # personal/profile facts live here
_HTTP_TIMEOUT = 12
_MIN_MESSAGE_LEN = 12

_env_cache: Optional[dict] = None

# Controlled topic_key vocabulary: keeps memory clustered + dedupable at scale.
_ALLOWED_CATEGORIES = {
    "work",
    "family",
    "relationships",
    "health",
    "location",
    "preferences",
    "decisions",
}

_EXTRACT_SYSTEM = (
    "You capture DURABLE PROFILE facts about the user for a personal assistant's "
    "long-term memory. Extract ONLY stable facts about WHO THE USER IS: "
    "employer/company, job/role, family, relationships, health, location/where "
    "they live, strong lasting preferences, and major life decisions or "
    "commitments. "
    "Do NOT capture: project status or progress, code/technical/work-task "
    "details, day-to-day activity, questions, requests, opinions about external "
    "things, or anything transient that changes week to week. "
    "Reply with ONE JSON object and nothing else. "
    'If there IS a durable profile fact: {"durable": true, '
    '"category": "work|family|relationships|health|location|preferences|decisions", '
    '"title": "<=70 char summary", '
    '"content": "the fact in ONE concise sentence, third person about the user"}. '
    'If there is NOT: {"durable": false}. '
    "Be conservative: when unsure, return durable:false. At most one fact per "
    "message — pick the single most important durable fact."
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def maybe_capture(
    user_message: str = "",
    task_id: str = "",
    session_id: str = "",
) -> Optional[threading.Thread]:
    """Fire-and-forget deterministic capture. No-op unless iris is configured.

    Never raises and never blocks. Returns the spawned thread (for tests) or
    None when it short-circuits. Sub-agent turns are skipped.
    """
    try:
        if task_id and (task_id.startswith("sa-") or task_id.startswith("subagent-")):
            return None
        msg = (user_message or "").strip()
        if len(msg) < _MIN_MESSAGE_LEN or msg.startswith("/"):
            return None
        if not _iris_configured():
            return None
        thread = threading.Thread(
            target=_capture_worker,
            args=(msg,),
            name="iris-capture",
            daemon=True,
        )
        thread.start()
        return thread
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture.maybe_capture failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Worker (runs in a daemon thread)
# ---------------------------------------------------------------------------

def _capture_worker(message: str) -> None:
    try:
        fact = _extract_fact(message)
        if fact is not None:
            _write_to_engram(fact)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture worker failed: %s", exc)


# ---------------------------------------------------------------------------
# Config / env
# ---------------------------------------------------------------------------

def _load_env() -> dict:
    """Parse ~/iris-ai/.env (KEY=value). Cached. Never raises."""
    global _env_cache
    if _env_cache is not None:
        return _env_cache
    env: dict = {}
    try:
        for raw in _IRIS_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    except Exception as exc:
        logger.debug("iris_capture: cannot read %s (%s)", _IRIS_ENV, exc)
    _env_cache = env
    return env


# ---------------------------------------------------------------------------
# Extraction (cheap LLM call)
# ---------------------------------------------------------------------------

def _extract_fact(message: str) -> Optional[dict]:
    env = _load_env()
    key = env.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    base = (env.get("OPENAI_BASE_URL") or _DEFAULT_OPENROUTER_BASE).rstrip("/")
    model = env.get("CAPTURE_MODEL") or _DEFAULT_CAPTURE_MODEL
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "max_tokens": 220,
            "messages": [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": message},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (
        (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    )
    return _fact_from_content(content)


def _fact_from_content(content: str) -> Optional[dict]:
    parsed = _parse_json_object(content)
    if not parsed or not parsed.get("durable"):
        return None
    title = (parsed.get("title") or "").strip()
    fact_content = (parsed.get("content") or "").strip()
    if not title or not fact_content:
        return None
    # Map the model's category onto a fixed topic_key vocabulary so facts about
    # the same dimension cluster on one key and Engram can dedupe them.
    category = (parsed.get("category") or "").strip().lower()
    if category not in _ALLOWED_CATEGORIES:
        category = "other"
    return {
        "title": title[:120],
        "content": fact_content,
        "type": "profile",
        "topic_key": f"profile/{category}",
    }


def _parse_json_object(text: str) -> Optional[dict]:
    """Extract the first {...} JSON object from a model reply. Never raises."""
    text = (text or "").strip()
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = json.loads(text[start:end])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Engram write
# ---------------------------------------------------------------------------

def _engram_base() -> str:
    env = _load_env()
    return (env.get("ENGRAM_BASE_URL") or _DEFAULT_ENGRAM).rstrip("/")


def _engram_headers() -> dict:
    env = _load_env()
    headers = {"Content-Type": "application/json"}
    token = env.get("ENGRAM_HTTP_TOKEN") or os.environ.get("ENGRAM_HTTP_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _post(url: str, payload: dict) -> Optional[dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_engram_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.debug("iris_capture engram POST %s -> HTTP %s", url, exc.code)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture engram POST %s failed: %s", url, exc)
        return None


def _write_to_engram(fact: dict) -> None:
    base = _engram_base()
    # Ensure the capture session exists (idempotent; ignore the result).
    _post(base + "/sessions", {"id": _CAPTURE_SESSION, "project": _CAPTURE_PROJECT})
    payload = {
        "session_id": _CAPTURE_SESSION,
        "project": _CAPTURE_PROJECT,
        "scope": "personal",
        "title": fact["title"],
        "content": fact["content"],
        "type": fact["type"],
    }
    if fact.get("topic_key"):
        payload["topic_key"] = fact["topic_key"]
    _post(base + "/observations", payload)
