"""Cobalt Routing - Model resolution from presets with multi-provider support."""

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_presets: Dict[str, Any] = {}
_active_preset: str = "economy"
_provider_cache: Dict[str, Dict[str, Any]] = {}

_TIMEOUT_PER_TYPE: Dict[str, int] = {
    "scout": 300,
    "explore": 300,
    "summarize": 300,
    "apply": 600,
    "archive": 600,
    "design": 900,
    "spec": 900,
    "tasks": 900,
    "verify": 900,
    "propose": 900,
}


def apply_dynamic_timeout(task_type: str) -> None:
    """Set per-task timeout via env var before delegation."""
    timeout = _TIMEOUT_PER_TYPE.get(task_type, 600)
    os.environ["DELEGATION_CHILD_TIMEOUT_SECONDS"] = str(timeout)
    logger.info("cobalt-routing: timeout set to %ds for task_type=%s", timeout, task_type)


def load_presets() -> None:
    global _presets, _active_preset
    try:
        import yaml
    except ImportError:
        logger.warning("cobalt-routing: PyYAML not available, presets disabled")
        return

    presets_path = Path(__file__).parent / "presets.yaml"
    if not presets_path.exists():
        logger.warning("cobalt-routing: presets.yaml not found")
        return

    try:
        data = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
        _presets = data.get("presets", {})
        _active_preset = data.get("active", "economy")
        if _active_preset not in _presets:
            available = list(_presets.keys())
            logger.warning(
                "cobalt-routing: active preset '%s' not found in loaded presets. Available: %s",
                _active_preset, available,
            )
            if "economy" in _presets:
                _active_preset = "economy"
            elif available:
                _active_preset = available[0]
            else:
                _active_preset = None
        logger.info("cobalt-routing: %d presets loaded, active=%s", len(_presets), _active_preset)
    except Exception as e:
        logger.error("cobalt-routing: failed to load presets: %s", e)


def _resolve_provider_creds(provider_name: str) -> Optional[Dict[str, Any]]:
    if provider_name in _provider_cache:
        return _provider_cache[provider_name]
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        creds = resolve_runtime_provider(requested=provider_name)
        _provider_cache[provider_name] = creds
        return creds
    except Exception as e:
        logger.debug("cobalt-routing: cannot resolve provider %s: %s", provider_name, e)
        return None


def _get_provider_for_model(model: str, preset: Dict[str, Any]) -> Optional[str]:
    model_providers = preset.get("model_providers", {})
    if model in model_providers:
        return model_providers[model]
    try:
        import json
        from hermes_constants import get_hermes_home
        cache_path = get_hermes_home() / "models_dev_cache.json"
        if cache_path.exists():
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            for prov_name, prov_data in cache.items():
                models = prov_data.get("models", {})
                if isinstance(models, dict) and model in models:
                    return prov_name
                elif isinstance(models, list) and model in models:
                    return prov_name
    except Exception:
        pass
    return None


def resolve_routing(task_type: Optional[str]) -> Optional[Dict[str, Any]]:
    if not task_type or not _presets:
        return None
    preset = _presets.get(_active_preset)
    if not preset:
        logger.warning(
            "cobalt-routing: routing returning None for task_type=%s because active preset '%s' is missing",
            task_type, _active_preset,
        )
        return None
    routing = preset.get("routing", {})
    model = routing.get(task_type) or routing.get("default")
    if not model:
        return None
    result = {"model": model}
    preset_provider = preset.get("provider", "opencode-go")
    if preset_provider == "mixed":
        target_provider = _get_provider_for_model(model, preset)
        if target_provider:
            creds = _resolve_provider_creds(target_provider)
            if creds:
                result["provider"] = creds.get("provider", target_provider)
                result["base_url"] = creds.get("base_url")
                result["api_key"] = creds.get("api_key")
                result["api_mode"] = creds.get("api_mode")
    return result


def get_active_preset() -> str:
    return _active_preset


def set_active_preset(name: str) -> bool:
    global _active_preset
    if name in _presets:
        _active_preset = name
        logger.info("cobalt-routing: preset -> %s", name)
        return True
    return False


def list_presets() -> Dict[str, str]:
    return {n: p.get("description", "") for n, p in _presets.items()}


_TASK_TYPE_KEYWORDS = {
    "apply": ["implementa", "escribe", "crea el", "crea un", "crear", "modifica", "write", "implement", "create", "modify", "refactor", "fix", "genera", "generate", "develop", "build", "construye", "code", "programa"],
    "verify": ["test", "tests", "prueba", "valida", "validate", "ejecuta el", "run the", "failing", "broken", "coverage", "pytest", "funciona correctamente", "verifica que", "verify", "ensure it works", "check that"],
    "design": ["arquitectura", "architecture", "design the", "architect", "structure", "system design"],
    "spec": ["requisitos", "requirements", "spec", "criteria", "acceptance", "given/when/then"],
    "propose": ["propone", "evalua", "decide", "propose", "evaluate", "compare", "tradeoff", "alternative", "opcion"],
    "explore": ["investiga", "analiza", "explica", "explain", "investigate", "analyze", "understand", "trace", "examine", "read the", "lee el", "estudia"],
    "scout": ["busca en", "encuentra", "find", "search for", "locate", "scan", "discover", "busca informacion", "busca documentacion", "web"],
    "summarize": ["resume", "resumen", "summarize", "summary", "condense", "overview", "synopsis"],
}

_TASK_TYPE_PRIORITY = ["apply", "verify", "design", "spec", "propose", "explore", "scout", "summarize"]

_ROLE_TO_TASK_TYPE: Dict[str, str] = {
    "worker": "apply",
    "researcher": "explore",
    "reviewer": "verify",
    "planner": "design",
    "scout": "scout",
}

def resolve_task_type_from_role(role: Optional[str], goal: str) -> str:
    """Resolve task_type considering role as fallback signal from K2.6."""
    if role and role in _ROLE_TO_TASK_TYPE:
        mapped = _ROLE_TO_TASK_TYPE[role]
        if mapped:
            logger.info("cobalt-routing: resolved task_type=%s from role=%s", mapped, role)
            return mapped
    return _infer_task_type(goal)


def _infer_task_type(goal: str) -> str:
    """Infer task_type from goal - multi-verb analysis + keyword scoring."""
    goal_lower = goal.lower()

    # Limit to first 60 chars for verb-only signals to avoid matching keywords
    # inside file paths or filenames (e.g. "lib-validate-json.py" → verify false positive).
    first_segment = goal_lower[:60]
    full_segment = goal_lower[:120]

    creation_verbs = ["crea", "escribe", "implementa", "genera", "construye", "modifica", "modificar", "modify", "refactoriza", "agrega", "agregar", "añade", "añadir", "actualiza", "actualizar", "extiende", "extender", "write", "implement", "create", "build", "develop", "make", "code", "programa", "add", "append", "update", "extend"]
    verify_verbs = ["verifica", "testea", "prueba", "ejecuta", "run", "test", "check if", "confirma", "verify", "ensure", "confirm"]
    verify_intent_signals = ["verify that", "check that", "ensure that", "confirm that", "verifica que", "comprobar que", "report the"]
    scout_verbs = ["busca", "encuentra", "search", "find", "locate", "descubre"]
    explore_verbs = ["investiga", "analiza", "lee ", "read", "analyze", "explore", "examine", "revisa", "entiende", "understand"]
    design_verbs = ["disena", "architect", "design", "planifica"]

    leading_segment = goal_lower[:30]

    # Apply leading verb wins unconditionally — checked before verify to prevent
    # filenames in the path (e.g. "lib-validate-json.py") from hijacking routing.
    if any(v in leading_segment for v in creation_verbs):
        logger.info("cobalt-routing: inferred task_type=apply from goal (leading verb)")
        return "apply"

    if any(v in first_segment for v in verify_verbs):
        logger.info("cobalt-routing: inferred task_type=verify from goal (verb match)")
        return "verify"
    if any(signal in full_segment for signal in verify_intent_signals):
        logger.info("cobalt-routing: inferred task_type=verify from goal (intent signal)")
        return "verify"

    if any(v in first_segment for v in creation_verbs):
        if not any(v in leading_segment for v in explore_verbs + scout_verbs):
            logger.info("cobalt-routing: inferred task_type=apply from goal (verb match)")
            return "apply"
    if any(v in first_segment for v in scout_verbs):
        logger.info("cobalt-routing: inferred task_type=scout from goal (verb match)")
        return "scout"
    # explore checked before design: "explorar ... estructura" should route to
    # explore, not design, because the leading intent is exploration.
    if any(v in first_segment for v in explore_verbs):
        logger.info("cobalt-routing: inferred task_type=explore from goal (verb match)")
        return "explore"
    if any(v in full_segment for v in design_verbs):
        logger.info("cobalt-routing: inferred task_type=design from goal (verb match)")
        return "design"

    scores = {}
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in goal_lower)
        if score > 0:
            scores[task_type] = score

    if scores:
        max_score = max(scores.values())
        for tt in _TASK_TYPE_PRIORITY:
            if scores.get(tt, 0) == max_score:
                logger.info("cobalt-routing: inferred task_type=%s from goal (keyword)", tt)
                return tt

    return "explore"


def wrap_delegate_handler(original_handler: Callable) -> Callable:
    def routed_handler(args: Dict[str, Any], **kw) -> Any:
        task_type = args.pop("task_type", None)
        tasks = args.get("tasks")
        if tasks and isinstance(tasks, list):
            for t in tasks:
                tt = t.pop("task_type", None) or task_type or _infer_task_type(t.get("goal", ""))
                if tt:
                    routing = resolve_routing(tt)
                    if routing:
                        t["_routed_model"] = routing["model"]
                        if "provider" in routing:
                            t["_routed_provider"] = routing["provider"]
                        if "base_url" in routing:
                            t["_routed_base_url"] = routing["base_url"]
                        if "api_key" in routing:
                            t["_routed_api_key"] = routing["api_key"]
                        if "api_mode" in routing:
                            t["_routed_api_mode"] = routing["api_mode"]
        elif args.get("goal"):
            effective_tt = task_type or _infer_task_type(args.get("goal", ""))
            routing = resolve_routing(effective_tt)
            if routing:
                task_entry = {
                    "goal": args.pop("goal"),
                    "context": args.pop("context", None),
                    "toolsets": args.pop("toolsets", None),
                    "role": args.pop("role", None),
                    "_routed_model": routing["model"],
                }
                if "provider" in routing:
                    task_entry["_routed_provider"] = routing["provider"]
                if "base_url" in routing:
                    task_entry["_routed_base_url"] = routing["base_url"]
                if "api_key" in routing:
                    task_entry["_routed_api_key"] = routing["api_key"]
                if "api_mode" in routing:
                    task_entry["_routed_api_mode"] = routing["api_mode"]
                args["tasks"] = [task_entry]
        return original_handler(args, **kw)
    return routed_handler
