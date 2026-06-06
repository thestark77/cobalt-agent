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

Scaling (no fixed limits, to avoid bias):
  - Relevance-scoped context: the facts shown to the extractor are not a fixed
    top-K. Each fact is scored by cosine similarity (embeddings) to the current
    message and included only at/above a threshold — a dynamic count.
  - Deterministic consolidation: periodically (time-gated), near-duplicate facts
    are clustered by cosine similarity (>= merge threshold) and merged into one;
    the clustering decides what merges, not the model. Absorbed keys are
    soft-deleted (recoverable).
"""

import json
import logging
import math
import os
import threading
import time
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

# Safety fuse, NOT a semantic cap: the criterion decides how many facts a message
# yields; this only stops a pathological model reply from writing dozens of rows.
_MAX_FACTS_PER_MESSAGE = 10

# --- Relevance-scoped known facts (option 3) ----------------------------------
# We do NOT send a fixed top-K of facts to the extractor (a fixed width biases:
# it can drop genuinely-relevant facts when a topic has many, or pad with
# irrelevant ones). Instead we score every fact by cosine similarity to the
# current message (semantic, via embeddings) and include those AT OR ABOVE a
# threshold — a dynamic count driven purely by relevance.
_EMBED_MODEL_DEFAULT = "openai/text-embedding-3-small"  # matches iris EMBEDDINGS_MODEL
_EMBED_DIM = 1536
# Cosine thresholds (text-embedding-3-small). Tunable via .env without redeploy:
#   CAPTURE_RELEVANCE_THRESHOLD — include a fact in the extractor context.
#   CAPTURE_MERGE_THRESHOLD     — cluster near-duplicate facts for consolidation.
# MERGE must be > RELEVANCE: we retrieve loosely-related facts but only merge
# near-identical ones.
_RELEVANCE_THRESHOLD = 0.30
_MERGE_THRESHOLD = 0.80
_RELEVANCE_MIN_FACTS = 8     # at/below this many facts, send all (no bias at tiny scale)
_RELEVANCE_CEILING = 150     # runaway guard ONLY (not a semantic cap)

# --- Periodic deterministic consolidation (option 2) --------------------------
_CONSOLIDATION_INTERVAL_S = 7 * 24 * 3600   # at most once a week
_CONSOLIDATION_MIN_FACTS = 12               # don't bother below this
_CONSOLIDATION_MARKER_KEY = "profile-meta/last-consolidation"
_CONSOL_CHECK_THROTTLE_S = 3600             # re-check the gate at most hourly per process
_consol_checked_at = 0.0

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

# Consolidation merge prompt. The DECISION of what to merge is made
# deterministically (cosine clustering) BEFORE this is called — the model only
# unifies an already-chosen cluster of near-duplicate facts into one, with no
# discretion to add, drop, or regroup.
_MERGE_SYSTEM = (
    "You are given several near-duplicate statements about ONE user that have "
    "already been determined to describe the SAME fact. Unify them into a SINGLE "
    "statement that preserves EVERY detail present across them. Do NOT add anything "
    "not stated, do NOT drop any detail, do NOT generalize away specifics. "
    "Reply with ONLY a JSON object: "
    '{"title": "<=70 chars", "content": "one or two concise sentences, third person about the user"}.'
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
        # Defense in depth: never capture on an incognito turn, independent of
        # the gate in __init__ (the primary guard). Fail-open if unavailable.
        try:
            from incognito import is_incognito_effective
            if is_incognito_effective():
                return None
        except Exception:
            pass
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
    # Opportunistic, time-gated, fully fail-open: never affects the capture above.
    try:
        _maybe_consolidate()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture maybe_consolidate failed: %s", exc)


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

def _api_key() -> str:
    env = _load_env()
    return env.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")


def _api_base() -> str:
    env = _load_env()
    return (
        env.get("OPENROUTER_BASE_URL")
        or env.get("OPENAI_BASE_URL")  # legacy var name; accepted for back-compat
        or _DEFAULT_BASE_URL
    ).rstrip("/")


def _chat_completion(system: str, user: str, max_tokens: int) -> Optional[str]:
    """One temperature-0 chat call. Returns the message content, or None."""
    key = _api_key()
    if not key:
        return None
    env = _load_env()
    model = env.get("CAPTURE_MODEL") or _DEFAULT_CAPTURE_MODEL
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _api_base() + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def _extract_facts(message: str) -> list:
    """Extract 0..N durable profile facts from one user message. Returns [] when
    there is no key, no network, or nothing durable. Caller swallows exceptions."""
    if not _api_key():
        return []
    known = _known_facts_block(message)
    user_content = (
        f"Already known facts:\n{known}\n\nNew message from the user:\n{message}"
        if known
        else f"Already known facts: (none yet)\n\nNew message from the user:\n{message}"
    )
    content = _chat_completion(_EXTRACT_SYSTEM, user_content, 900)
    if content is None:
        return []
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


def _env_float(name: str, default: float) -> float:
    try:
        raw = _load_env().get(name) or os.environ.get(name, "")
        return float(raw) if raw else default
    except Exception:
        return default


def _export_profile_facts() -> list:
    """All LIVE profile/* observations (full objects). Never raises -> []."""
    try:
        data = _get(_engram_base() + "/export?project=" + _CAPTURE_PROJECT)
        obs = data if isinstance(data, list) else (data.get("observations") or []) if data else []
        return [
            o for o in obs
            if (o.get("topic_key") or "").startswith("profile/")
            and (o.get("deleted_at") or None) is None
            and (o.get("content") or "").strip()
        ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture export profile facts failed: %s", exc)
        return []


def _fact_line(o: dict) -> str:
    tk = o.get("topic_key") or ""
    content = (o.get("content") or "").strip().replace("\n", " ")
    return f"{tk}: {content[:200]}"


def _known_facts_block(message: str = "") -> str:
    """Profile facts (topic_key: content) for the extractor, RELEVANCE-SCOPED.

    NOT a fixed top-K (a fixed width biases — it drops genuinely-relevant facts
    when a topic has many, or pads with irrelevant ones). Every fact whose content
    is cosine-similar to the message AT OR ABOVE a threshold is included — a
    dynamic count driven by relevance. Falls back to "all facts" at tiny scale,
    with no message, or when embeddings are unavailable. Never raises."""
    facts = _export_profile_facts()
    if not facts:
        return ""
    selected = facts
    if message and len(facts) > _RELEVANCE_MIN_FACTS:
        scored = _relevant_facts(message, facts)
        if scored is not None:
            selected = scored
    return "\n".join(_fact_line(o) for o in selected[:_RELEVANCE_CEILING])


def _relevant_facts(message: str, facts: list) -> Optional[list]:
    """Facts cosine-similar to the message at/above the relevance threshold,
    most-similar first. None when embeddings are unavailable (caller falls back
    to all facts). The COUNT is dynamic — pure relevance, no fixed width."""
    vecs = _embed([message] + [(o.get("content") or "") for o in facts])
    if not vecs or len(vecs) != len(facts) + 1:
        return None
    threshold = _env_float("CAPTURE_RELEVANCE_THRESHOLD", _RELEVANCE_THRESHOLD)
    msg_vec = vecs[0]
    scored = [(_cosine(msg_vec, v), o) for o, v in zip(facts, vecs[1:])]
    hits = [(s, o) for s, o in scored if s >= threshold]
    hits.sort(key=lambda t: t[0], reverse=True)
    return [o for _, o in hits]


# ---------------------------------------------------------------------------
# Embeddings + similarity (semantic relevance; deterministic)
# ---------------------------------------------------------------------------

def _embed(texts: list) -> Optional[list]:
    """Batch-embed via OpenRouter /embeddings (same model iris uses). Returns a
    list of vectors parallel to `texts`, or None on any failure. Deterministic
    for identical inputs."""
    key = _api_key()
    if not key or not texts:
        return None
    env = _load_env()
    model = env.get("EMBEDDINGS_MODEL") or _EMBED_MODEL_DEFAULT
    body = json.dumps({"model": model, "input": list(texts), "dimensions": _EMBED_DIM}).encode("utf-8")
    req = urllib.request.Request(
        _api_base() + "/embeddings",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
        vecs = [it.get("embedding") for it in items]
        if len(vecs) != len(texts) or any(not v for v in vecs):
            return None
        return vecs
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture embed failed: %s", exc)
        return None


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _cluster_by_similarity(vecs: list, threshold: float) -> list:
    """Connected-components clustering: i and j join the same cluster iff
    cosine(vecs[i], vecs[j]) >= threshold. Fully DETERMINISTIC given the vectors —
    the merge decision is never the model's. Returns a list of index-lists."""
    n = len(vecs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        for j in range(i + 1, n):
            if _cosine(vecs[i], vecs[j]) >= threshold:
                union(i, j)
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for _, g in sorted(groups.items())]


# ---------------------------------------------------------------------------
# Periodic deterministic consolidation (option 2)
# ---------------------------------------------------------------------------

def _maybe_consolidate(now: Optional[float] = None) -> None:
    """Time-gated trigger for consolidate_profile. Per-process hourly throttle so
    we don't export every message; runs the real job at most once per interval.
    Fail-open."""
    global _consol_checked_at
    now = now if now is not None else time.time()
    if now - _consol_checked_at < _CONSOL_CHECK_THROTTLE_S:
        return
    _consol_checked_at = now
    data = _get(_engram_base() + "/export?project=" + _CAPTURE_PROJECT)
    obs = data if isinstance(data, list) else (data.get("observations") or []) if data else []
    live = [o for o in obs if (o.get("deleted_at") or None) is None]
    facts = [
        o for o in live
        if (o.get("topic_key") or "").startswith("profile/")
        and (o.get("content") or "").strip()
    ]
    if len(facts) < _CONSOLIDATION_MIN_FACTS:
        return
    last = 0.0
    for o in live:
        if o.get("topic_key") == _CONSOLIDATION_MARKER_KEY:
            try:
                last = float((o.get("content") or "0").split()[0])
            except Exception:
                last = 0.0
            break
    if last and (now - last) < _CONSOLIDATION_INTERVAL_S:
        return
    _write_marker(now)  # claim the slot BEFORE the heavy work (concurrency guard)
    consolidate_profile(facts=facts)


def _write_marker(now: float) -> None:
    base = _engram_base()
    _post(base + "/sessions", {"id": _CAPTURE_SESSION, "project": _CAPTURE_PROJECT})
    _post(base + "/observations", {
        "session_id": _CAPTURE_SESSION,
        "project": _CAPTURE_PROJECT,
        "scope": "personal",
        "title": "profile consolidation marker",
        "type": "profile",  # proven-accepted type; topic_key prefix keeps it out of fact queries
        "topic_key": _CONSOLIDATION_MARKER_KEY,
        "content": f"{now:.0f} epoch-seconds of last profile consolidation",
    })


def consolidate_profile(facts: Optional[list] = None) -> dict:
    """DETERMINISTIC consolidation. Clusters near-duplicate facts by cosine
    similarity (>= merge threshold) and merges each cluster into one, soft-deleting
    the absorbed keys. The model NEVER decides what merges — clustering does; the
    model only phrases an already-chosen cluster. Conservative + fail-open.
    Returns a summary dict (also used by the manual script)."""
    try:
        if facts is None:
            facts = _export_profile_facts()
        if len(facts) < 2:
            return {"skipped": "too few facts", "count": len(facts)}
        vecs = _embed([(o.get("content") or "") for o in facts])
        if not vecs or len(vecs) != len(facts):
            return {"skipped": "embeddings unavailable"}
        threshold = _env_float("CAPTURE_MERGE_THRESHOLD", _MERGE_THRESHOLD)
        clusters = _cluster_by_similarity(vecs, threshold)
        merged, deleted = [], []
        for idx in clusters:
            if len(idx) < 2:
                continue  # singleton — leave untouched
            group = [facts[i] for i in idx]
            # canonical fact = most recently updated (deterministic tiebreak by key)
            canon = sorted(
                group, key=lambda o: (o.get("updated_at") or "", o.get("topic_key") or "")
            )[-1]
            canon_key = canon.get("topic_key")
            content = _merge_cluster_content(group) or (canon.get("content") or "").strip()
            if not content or not canon_key:
                continue
            _write_to_engram({
                "title": (canon.get("title") or canon_key)[:120],
                "content": content,
                "type": "profile",
                "topic_key": canon_key,
            })
            merged.append(canon_key)
            for o in group:
                if o.get("topic_key") != canon_key and o.get("id") is not None:
                    if _delete_obs(o["id"]):
                        deleted.append(o.get("topic_key"))
        return {
            "facts": len(facts),
            "clusters_merged": len(merged),
            "merged_into": merged,
            "deleted": deleted,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture consolidate failed: %s", exc)
        return {"error": str(exc)}


def _merge_cluster_content(group: list) -> Optional[str]:
    """Unify a deterministically-chosen cluster into one statement. The model only
    phrases (cannot regroup, add, or drop). Returns merged content or None."""
    listing = "\n".join(f"- {(o.get('content') or '').strip()}" for o in group)
    parsed = _parse_json_payload(_chat_completion(_MERGE_SYSTEM, listing, 400) or "")
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if isinstance(parsed, dict):
        return (parsed.get("content") or "").strip() or None
    return None


def _delete_obs(obs_id) -> bool:
    """Soft-delete (tombstone) an observation by id. Engram retains the row with
    deleted_at, so this is recoverable. Never raises."""
    try:
        req = urllib.request.Request(
            f"{_engram_base()}/observations/{obs_id}",
            headers=_engram_headers(),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            resp.read()
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("iris_capture delete obs %s failed: %s", obs_id, exc)
        return False


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
