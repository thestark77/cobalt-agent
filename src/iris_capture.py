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
# Mirror iris-ai's own resolution (src/brain/config): same env var
# (OPENAI_BASE_URL) and the SAME default (OpenAI direct, not OpenRouter), so
# capture targets exactly the provider iris already authenticates against with
# OPENROUTER_API_KEY. Getting this wrong = the extraction call 404s/401s and the
# whole capture silently no-ops (nothing saved).
_DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
# Chat model id differs by provider: OpenAI direct uses the bare id; OpenRouter
# namespaces it under the vendor.
_CAPTURE_MODEL_OPENAI = "gpt-4o-mini"
_CAPTURE_MODEL_OPENROUTER = "openai/gpt-4o-mini"
_CAPTURE_SESSION = "iris-capture"
_CAPTURE_PROJECT = "sebas"  # personal/profile facts live here
_HTTP_TIMEOUT = 12
_MIN_MESSAGE_LEN = 12

_env_cache: Optional[dict] = None

_MAX_KNOWN_FACTS = 60  # bound the prompt; profile facts don't grow unbounded fast.

_EXTRACT_SYSTEM = (
    "You maintain a personal assistant's long-term memory of DURABLE PROFILE facts "
    "about the user: employer/company, job/role, family, relationships, health, "
    "location, strong lasting preferences, and major life decisions. "
    "You are given the facts ALREADY KNOWN (as 'topic_key: fact') and the user's "
    "NEW message. Decide whether the new message contains a durable profile fact, "
    "following these rules: "
    "(1) If it is ALREADY known and unchanged -> reply {\"durable\": false}. "
    "(2) If it UPDATES or corrects a known fact -> reuse that fact's EXACT topic_key "
    "(its content will be overwritten). "
    "(3) If it is a NEW durable fact -> create a SPECIFIC topic_key "
    "'profile/<slug>' where the slug is the precise dimension, e.g. "
    "profile/employer, profile/role, profile/family-sister, profile/location-home, "
    "profile/decision-move-city. One fact = one specific key (do NOT lump distinct "
    "facts under one broad key). "
    "Do NOT capture project status/progress, code or work-task details, day-to-day "
    "activity, questions, requests, or anything transient. Be conservative: when "
    "unsure, reply {\"durable\": false}. At most one fact per message. "
    "Reply with ONE JSON object and nothing else: "
    '{"durable": true, "topic_key": "profile/<slug>", "title": "<=70 chars", '
    '"content": "the fact in ONE concise sentence, third person about the user"} '
    'OR {"durable": false}.'
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

def _default_capture_model(base: str) -> str:
    """Pick a chat-model id that matches the resolved provider.

    OpenRouter namespaces OpenAI models as ``openai/gpt-4o-mini``; OpenAI direct
    wants the bare ``gpt-4o-mini``. Using the wrong one 404s the request and the
    capture silently drops the fact, so derive it from the base URL.
    """
    return _CAPTURE_MODEL_OPENROUTER if "openrouter" in base.lower() else _CAPTURE_MODEL_OPENAI


def _extract_fact(message: str) -> Optional[dict]:
    env = _load_env()
    key = env.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    base = (env.get("OPENAI_BASE_URL") or _DEFAULT_OPENAI_BASE).rstrip("/")
    model = env.get("CAPTURE_MODEL") or _default_capture_model(base)
    known = _known_facts_block()
    user_content = (
        f"Already known facts:\n{known}\n\nNew message from the user:\n{message}"
        if known
        else f"Already known facts: (none yet)\n\nNew message from the user:\n{message}"
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "max_tokens": 220,
            "messages": [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": user_content},
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
    return {
        "title": title[:120],
        "content": fact_content,
        "type": "profile",
        "topic_key": _normalize_topic_key(parsed.get("topic_key")),
    }


def _normalize_topic_key(raw: Optional[str]) -> str:
    """Force a stable 'profile/<slug>' key so Engram upserts cleanly.

    The model returns a specific key (e.g. profile/employer); we sanitize the
    slug (lowercase, [a-z0-9-]) and default to profile/misc on anything off.
    """
    tk = (raw or "").strip().lower()
    if tk.startswith("profile/"):
        slug = tk[len("profile/"):]
    else:
        slug = tk
    cleaned = "".join(c if (c.isalnum() or c in "-/") else "-" for c in slug).strip("-/")
    return f"profile/{cleaned}" if cleaned else "profile/misc"


def _known_facts_block() -> str:
    """Fetch current durable profile facts (topic_key: content) for the extractor.

    Lets the model reuse an exact topic_key when updating a fact (Engram upserts
    by (project, topic_key)) and avoid re-saving anything already known. Bounded
    and never raises.
    """
    try:
        base = _engram_base()
        data = _get(base + "/export?project=" + _CAPTURE_PROJECT)
        if not data:
            return ""
        obs = data if isinstance(data, list) else (data.get("observations") or [])
        lines = []
        for o in obs:
            tk = (o.get("topic_key") or "")
            if not tk.startswith("profile/"):
                continue
            if (o.get("deleted_at") or None) is not None:
                continue
            content = (o.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{tk}: {content[:160]}")
        return "\n".join(lines[:_MAX_KNOWN_FACTS])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture known-facts fetch failed: %s", exc)
        return ""


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


def _get(url: str):
    req = urllib.request.Request(url, headers=_engram_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture engram GET %s failed: %s", url, exc)
        return None


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
