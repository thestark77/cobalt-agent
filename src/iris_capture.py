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

Flow per turn: cheap LLM extraction ("which durable profile facts are in this
message?", 0..N) -> write each to Engram. The extraction credential is OPENROUTER_API_KEY
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
# Mirror iris-ai's own resolution (src/brain/config): read OPENROUTER_BASE_URL
# (with OPENAI_BASE_URL accepted as a legacy fallback) and default to OpenRouter,
# so capture targets exactly the provider iris already authenticates against with
# OPENROUTER_API_KEY. Getting this wrong = the extraction call 401/404s and the
# whole capture silently no-ops (nothing saved).
_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Extraction model on OpenRouter. Override via CAPTURE_MODEL in ~/iris-ai/.env.
_DEFAULT_CAPTURE_MODEL = "deepseek/deepseek-v4-flash"
_CAPTURE_SESSION = "iris-capture"
_CAPTURE_PROJECT = "sebas"  # personal/profile facts live here
_HTTP_TIMEOUT = 20
_MIN_MESSAGE_LEN = 12

_env_cache: Optional[dict] = None

_MAX_KNOWN_FACTS = 80  # bound the prompt; profile facts don't grow unbounded fast.
# Safety fuse, NOT a semantic cap: the criterion decides how many facts a message
# yields; this only stops a pathological model reply from writing dozens of rows.
_MAX_FACTS_PER_MESSAGE = 10

_EXTRACT_SYSTEM = (
    "You maintain a personal assistant's long-term memory of DURABLE PROFILE facts "
    "about the user. A DURABLE PROFILE fact is something that will STILL be true and "
    "relevant about him in several weeks AND that sharpens the model of who he is and "
    "what he wants. "
    "INCLUDE: identity; family, relationships, close people; where he lives and with "
    "whom; employer, role, ongoing projects or ventures (as lasting facts, not task "
    "status); health; values and worldview; aspirations and goals (career, personal "
    "growth, relocation, lifestyle); major decisions AND their motivation/reasoning "
    "(the WHY, not only the WHAT); strong, lasting preferences. "
    "EXCLUDE: task or project status, code, work-in-progress, to-dos; questions or "
    "requests to the assistant; ephemeral states (today's mood, what he is doing right "
    "now) unless they reveal a stable pattern; anything that would be stale in a few "
    "weeks; and facts already known and unchanged. "
    "You are given the facts ALREADY KNOWN (as 'topic_key: fact') and the user's NEW "
    "message, which may be long and contain SEVERAL durable facts, one, or none. "
    "RULES: "
    "(1) Extract EVERY durable profile fact present. There is NO fixed number: a long "
    "message may yield several; a message with nothing durable yields []. "
    "(2) One fact = one specific dimension. Use a SPECIFIC topic_key 'profile/<slug>' "
    "(e.g. profile/employer, profile/role, profile/location-home, "
    "profile/aspiration-growth, profile/decision-move-city). Do NOT lump distinct facts "
    "under one key, and do NOT split one fact into near-duplicates. "
    "(3) If a fact UPDATES or corrects something already known, reuse that fact's EXACT "
    "topic_key (its content will be overwritten). If it is already known and unchanged, "
    "OMIT it. "
    "(4) Be conservative: when unsure whether something is durable, OMIT it. Quality "
    "over quantity. Never return more than 10 facts; if the message has more, keep only "
    "the most important. "
    "Reply with ONLY a JSON array and nothing else, each element: "
    '{"topic_key": "profile/<slug>", "title": "<=70 chars", '
    '"content": "the fact in ONE concise sentence, third person about the user"}. '
    "Return [] if there is nothing durable to save."
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
        for fact in _extract_facts(message):
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

def _extract_facts(message: str) -> list:
    """Extract 0..N durable profile facts from one user message. Never raises here
    is NOT guaranteed (caller swallows); returns [] when there is no key/nothing."""
    env = _load_env()
    key = env.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return []
    base = (
        env.get("OPENROUTER_BASE_URL")
        or env.get("OPENAI_BASE_URL")  # legacy var name; accepted for back-compat
        or _DEFAULT_BASE_URL
    ).rstrip("/")
    model = env.get("CAPTURE_MODEL") or _DEFAULT_CAPTURE_MODEL
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
            "max_tokens": 900,
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
    return _facts_from_content(content)


def _facts_from_content(content: str) -> list:
    """Parse the model reply into a list of validated fact dicts. Never raises.

    Accepts a JSON array (the contract), a single object, or the legacy
    {"durable": false}. Bounded by _MAX_FACTS_PER_MESSAGE (a safety fuse, not a
    semantic cap — the prompt's criterion decides how many facts are real)."""
    parsed = _parse_json_payload(content)
    if parsed is None:
        return []
    if isinstance(parsed, dict):
        if parsed.get("durable") is False:  # legacy single-object "nothing" reply
            return []
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    facts = []
    for item in parsed:
        fact = _normalize_fact(item)
        if fact is not None:
            facts.append(fact)
        if len(facts) >= _MAX_FACTS_PER_MESSAGE:
            break
    return facts


def _normalize_fact(item) -> Optional[dict]:
    """Validate and shape one fact element. Returns None when unusable."""
    if not isinstance(item, dict):
        return None
    title = (item.get("title") or "").strip()
    fact_content = (item.get("content") or "").strip()
    if not title or not fact_content:
        return None
    return {
        "title": title[:120],
        "content": fact_content,
        "type": "profile",
        "topic_key": _normalize_topic_key(item.get("topic_key")),
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


def _parse_json_payload(text: str):
    """Extract the first JSON array or object from a model reply. Never raises.

    Prefers an array (the contract) when present; falls back to a bare object.
    Returns a list, a dict, or None.
    """
    text = (text or "").strip()
    candidates = []
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            candidates.append((start, text[start:end + 1]))
    candidates.sort(key=lambda c: c[0])  # earliest opener first (array wins)
    for _, snippet in candidates:
        try:
            value = json.loads(snippet)
            if isinstance(value, (list, dict)):
                return value
        except Exception:
            continue
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
