"""Cobalt Routing - Logging and utility helpers."""

import logging
from typing import Any, MutableMapping


class _PrefixAdapter(logging.LoggerAdapter):
    """LoggerAdapter that prefixes every message with 'cobalt-routing: '."""

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        return f"cobalt-routing: {msg}", kwargs


def get_logger(name: str) -> logging.LoggerAdapter:
    """Return a logger adapter that automatically prefixes messages.

    Usage:
        from utils import get_logger
        logger = get_logger(__name__)
        logger.info("presets loaded")  # → "cobalt-routing: presets loaded"
    """
    raw = logging.getLogger(name)
    return _PrefixAdapter(raw, {})
