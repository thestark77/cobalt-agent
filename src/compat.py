"""Cobalt Routing — Version compatibility and patch verification."""

import logging
import re
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

TESTED_VERSION = "0.12.0"
MAX_COMPATIBLE = (0, 12, 99)
WARN_FROM = (0, 13, 0)
ERROR_FROM = (1, 0, 0)

PATCH_MARKER = "_routed_model"


def parse_version(version_str: str) -> Tuple[int, ...]:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version_str)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


def get_hermes_version() -> str:
    try:
        from hermes_constants import get_hermes_home
        pyproject = get_hermes_home() / "hermes-agent" / "pyproject.toml"
        if pyproject.exists():
            for line in pyproject.read_text().splitlines():
                if line.strip().startswith("version"):
                    match = re.search(r'"([^"]+)"', line)
                    if match:
                        return match.group(1)
    except Exception:
        pass
    return "0.0.0"


def check_version() -> str:
    """Check Hermes version compatibility.

    Returns:
        'ok' — compatible
        'warn' — untested version, may work
        'error' — incompatible, must not load
    """
    version = get_hermes_version()
    parsed = parse_version(version)

    if parsed >= ERROR_FROM:
        return "error"
    if parsed >= WARN_FROM:
        return "warn"
    return "ok"


def verify_patch_applied() -> bool:
    """Check if the source patch is applied to delegate_tool.py."""
    try:
        from hermes_constants import get_hermes_home
        delegate_path = get_hermes_home() / "hermes-agent" / "tools" / "delegate_tool.py"
        if not delegate_path.exists():
            return False
        content = delegate_path.read_text(encoding="utf-8")
        return PATCH_MARKER in content
    except Exception:
        return False


def get_delegate_tool_path() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "hermes-agent" / "tools" / "delegate_tool.py"
