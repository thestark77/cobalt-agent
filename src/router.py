"""Cobalt Routing — Model resolution from presets with multi-provider support."""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_presets: Dict[str, Any] = {}
_active_preset: str = "economy"
_provider_cache: Dict[str, Dict[str, Any]] = {}


def load_presets() -> None:
    """Load presets from presets.yaml."""
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
        logger.info(
            "cobalt-routing: %d presets loaded, active='%s'",
            len(_presets), _active_preset
        )
    except Exception as e:
        logger.error("cobalt-routing: failed to load presets: %s", e)


def _resolve_provider_creds(provider_name: str) -> Optional[Dict[str, Any]]:
    """Resolve credentials for a provider using Hermes runtime."""
    if provider_name in _provider_cache:
        return _provider_cache[provider_name]

    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        creds = resolve_runtime_provider(requested=provider_name)
        _provider_cache[provider_name] = creds
        return creds
    except Exception as e:
        logger.debug("cobalt-routing: cannot resolve provider '%s': %s", provider_name, e)
        return None


def _get_provider_for_model(model: str, preset: Dict[str, Any]) -> Optional[str]:
    """Determine which provider hosts a given model."""
    model_providers = preset.get("model_providers", {})
    if model in model_providers:
        return model_providers[model]

    # Heuristic: check if model exists in known provider catalogs
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
    """Resolve full routing info for a task_type.

    Returns dict with keys: model, provider, base_url, api_key, api_mode
    or None if no routing applies.
    """
    if not task_type or not _presets:
        return None

    preset = _presets.get(_active_preset)
    if not preset:
        return None

    routing = preset.get("routing", {})
    model = routing.get(task_type) or routing.get("default")
    if not model:
        return None

    result = {"model": model}

    # Check if model needs a different provider than delegation default
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
        logger.info("cobalt-routing: preset -> '%s'", name)
        return True
    return False


def list_presets() -> Dict[str, str]:
    return {n: p.get("description", "") for n, p in _presets.items()}




_TASK_TYPE_KEYWORDS = {
    "apply": ["implementa", "escribe", "crea el", "crea un", "crear", "modifica", "write", "implement", "create", "modify", "refactor", "fix", "genera", "generate", "develop", "build", "construye", "code", "script", "programa"],
    "verify": ["test", "tests", "prueba", "valida", "validate", "ejecuta el", "run the", "failing", "broken", "coverage", "pytest", "funciona correctamente", "verifica que"],
    "design": ["arquitectura", "architecture", "design the", "architect", "structure", "system design"],
    "spec": ["requisitos", "requirements", "spec", "criteria", "acceptance", "given/when/then"],
    "propose": ["propone", "evalua", "decide", "propose", "evaluate", "compare", "tradeoff", "alternative", "opcion"],
    "explore": ["investiga", "analiza", "explica", "explain", "investigate", "analyze", "understand", "trace", "examine", "read the", "lee el", "estudia"],
    "scout": ["busca en", "encuentra", "find", "search for", "locate", "scan", "discover", "busca informacion", "busca documentacion", "web"],
    "summarize": ["resume", "resumen", "summarize", "summary", "condense", "overview", "synopsis"],
}

_TASK_TYPE_PRIORITY = ["apply", "verify", "design", "spec", "propose", "explore", "scout", "summarize"]


def _infer_task_type(goal: str) -> str:
    """Infer task_type from goal — first-verb heuristic + keyword scoring with priority."""
    goal_lower = goal.lower()

    # First-verb heuristic: the first 50 chars determine primary intent
    first_words = goal_lower[:50]
    if any(v in first_words for v in ["crea", "escribe", "implementa", "genera", "construye", "write", "implement", "create", "build", "develop", "make"]):
        logger.info("cobalt-routing: inferred task_type=apply from goal (first-verb)")
        return "apply"
    if any(v in first_words for v in ["verifica", "testea", "prueba", "valida", "ejecuta", "run", "test", "check if", "validate"]):
        logger.info("cobalt-routing: inferred task_type=verify from goal (first-verb)")
        return "verify"
    if any(v in first_words for v in ["busca", "encuentra", "search", "find", "locate"]):
        logger.info("cobalt-routing: inferred task_type=scout from goal (first-verb)")
        return "scout"
    if any(v in first_words for v in ["investiga", "analiza", "lee ", "read", "analyze", "explore", "examine"]):
        logger.info("cobalt-routing: inferred task_type=explore from goal (first-verb)")
        return "explore"
    if any(v in first_words for v in ["disena", "diseña", "architect", "design"]):
        logger.info("cobalt-routing: inferred task_type=design from goal (first-verb)")
        return "design"

    # Fallback: keyword scoring with priority on ties
    scores = {}
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in goal_lower)
        if score > 0:
            scores[task_type] = score

    if scores:
        max_score = max(scores.values())
        for tt in _TASK_TYPE_PRIORITY:
            if scores.get(tt, 0) == max_score:
                logger.info("cobalt-routing: inferred task_type=%s from goal", tt)
                return tt

    return "explore"

def wrap_delegate_handler(original_handler: Callable) -> Callable:
    """Wrap delegate_task handler to inject per-task routing."""

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
                # Convert single-mode to batch-mode with routing applied
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
