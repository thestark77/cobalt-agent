#!/usr/bin/env python3
"""Standalone tests for iris_capture — no pytest, plain asserts + sys.exit.

Covers: the iris-config gate (decoupling), sub-agent / short / slash skips,
JSON parsing robustness, fact extraction shaping, no-key fail-open, and the
Engram write payloads. No network: LLM and HTTP are stubbed.

Run from repo root:
    python3 scripts/test_iris_capture.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import iris_capture as ic

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


# ---------------------------------------------------------------------------
# Gate / decoupling: no-op unless iris is configured
# ---------------------------------------------------------------------------
print("=== gate (decoupling) ===")
_orig_configured = ic._iris_configured
_orig_worker = ic._capture_worker

# iris NOT configured -> no-op, no thread
ic._iris_configured = lambda: False
check("no iris -> returns None", ic.maybe_capture("Trabajo en Bemovil como dev") is None)

# iris configured but extraction/worker stubbed so we never touch the network
ran = {"n": 0, "msg": None}


def _fake_worker(message):
    ran["n"] += 1
    ran["msg"] = message


ic._iris_configured = lambda: True
ic._capture_worker = _fake_worker

print("=== skips ===")
check("subagent task_id -> None", ic.maybe_capture("Trabajo en Bemovil", task_id="sa-123") is None)
check("subagent-* task_id -> None", ic.maybe_capture("Trabajo en Bemovil", task_id="subagent-x") is None)
check("short message -> None", ic.maybe_capture("hola") is None)
check("slash command -> None", ic.maybe_capture("/reset ahora mismo por favor") is None)
check("no worker ran on skips", ran["n"] == 0)

print("=== happy path spawns worker ===")
t = ic.maybe_capture("Trabajo en Bemovil, es la empresa donde estoy empleado")
check("valid message -> thread returned", t is not None)
if t is not None:
    t.join(timeout=2)
check("worker ran once", ran["n"] == 1)
check("worker got the message", ran["msg"] and "Bemovil" in ran["msg"])

# restore
ic._iris_configured = _orig_configured
ic._capture_worker = _orig_worker

# ---------------------------------------------------------------------------
# JSON parsing robustness
# ---------------------------------------------------------------------------
print("=== _parse_json_object ===")
check("plain object", ic._parse_json_object('{"durable": false}') == {"durable": False})
check("fenced json", ic._parse_json_object('```json\n{"durable": true}\n```') == {"durable": True})
check(
    "surrounding prose",
    ic._parse_json_object('Sure! {"durable": true, "x": 1} done') == {"durable": True, "x": 1},
)
check("garbage -> None", ic._parse_json_object("no json here") is None)
check("empty -> None", ic._parse_json_object("") is None)
check("array -> None (only objects)", ic._parse_json_object("[1,2,3]") is None)

# ---------------------------------------------------------------------------
# Fact shaping
# ---------------------------------------------------------------------------
print("=== _fact_from_content ===")
check("durable:false -> None", ic._fact_from_content('{"durable": false}') is None)
check("missing -> None", ic._fact_from_content("nonsense") is None)
f = ic._fact_from_content(
    '{"durable": true, "topic_key": "profile/employer", "title": "Empleo en Bemovil", '
    '"content": "El usuario trabaja en Bemovil."}'
)
check("durable fact returns dict", isinstance(f, dict))
check("fact title", f and f["title"] == "Empleo en Bemovil")
check("fact content", f and "Bemovil" in f["content"])
check("fact type is profile", f and f["type"] == "profile")
check("specific topic_key preserved", f and f["topic_key"] == "profile/employer")
check(
    "durable but empty content -> None",
    ic._fact_from_content('{"durable": true, "topic_key": "profile/x", "title": "x", "content": ""}') is None,
)

print("=== _normalize_topic_key ===")
check("already good preserved", ic._normalize_topic_key("profile/role") == "profile/role")
check("absent -> profile/misc", ic._fact_from_content('{"durable": true, "title": "T", "content": "C"}')["topic_key"] == "profile/misc")
check("empty -> profile/misc", ic._normalize_topic_key("") == "profile/misc")
check("uppercase+spaces+punct sanitized", ic._normalize_topic_key("profile/Family Sister!") == "profile/family-sister")
check("non-profile coerced under profile/", ic._normalize_topic_key("employer") == "profile/employer")
check("hierarchical slug kept", ic._normalize_topic_key("profile/decision/move-city") == "profile/decision/move-city")

# ---------------------------------------------------------------------------
# Provider-correct model derivation (the silent-no-save bug)
# ---------------------------------------------------------------------------
print("=== _default_capture_model ===")
check("OpenAI direct -> bare id", ic._default_capture_model("https://api.openai.com/v1") == "gpt-4o-mini")
check("OpenRouter -> namespaced id", ic._default_capture_model("https://openrouter.ai/api/v1") == "openai/gpt-4o-mini")
check("OpenRouter case-insensitive", ic._default_capture_model("https://OpenRouter.ai/API/v1") == "openai/gpt-4o-mini")
check("unknown gateway -> bare id (OpenAI-compatible default)", ic._default_capture_model("https://llm.internal/v1") == "gpt-4o-mini")

# ---------------------------------------------------------------------------
# Extraction fail-open: no key -> None
# ---------------------------------------------------------------------------
print("=== _extract_fact no key ===")
_orig_load = ic._load_env
ic._load_env = lambda: {}
os.environ.pop("OPENROUTER_API_KEY", None)
check("no api key -> None (no network)", ic._extract_fact("Trabajo en Bemovil") is None)
ic._load_env = _orig_load

# ---------------------------------------------------------------------------
# Engram write payloads (stub _post)
# ---------------------------------------------------------------------------
print("=== _write_to_engram ===")
posts = []
_orig_post = ic._post
_orig_base = ic._engram_base
ic._post = lambda url, payload: posts.append((url, payload))
ic._engram_base = lambda: "http://127.0.0.1:7437"

ic._write_to_engram(
    {"title": "Empleo en Bemovil", "content": "Trabaja en Bemovil.", "type": "profile", "topic_key": "profile/work"}
)
check("two posts (session + observation)", len(posts) == 2)
check("first post is /sessions", posts and posts[0][0].endswith("/sessions"))
check("session has id+project", posts and posts[0][1].get("id") == "iris-capture" and posts[0][1].get("project") == "sebas")
check("second post is /observations", len(posts) > 1 and posts[1][0].endswith("/observations"))
obs = posts[1][1] if len(posts) > 1 else {}
check("obs has session_id", obs.get("session_id") == "iris-capture")
check("obs scope personal", obs.get("scope") == "personal")
check("obs carries title/content/type", obs.get("title") and obs.get("content") and obs.get("type") == "profile")
check("obs carries topic_key", obs.get("topic_key") == "profile/work")

ic._post = _orig_post
ic._engram_base = _orig_base

# ---------------------------------------------------------------------------
print()
print(f"PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
