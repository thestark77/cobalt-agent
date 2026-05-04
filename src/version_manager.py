"""Cobalt Routing — Automatic App Versioning.

Creates and manages version artifacts for each SDD execution cycle.
Stores the original prompt, execution plan, and changelog per version.

Structure:
    context/appVersions/vX.Y.Z/
    ├── original_prompt.md   ← Raw user prompt that triggered this version
    ├── plan.md              ← SDD phases + task breakdown
    └── changelog.md         ← Generated at version close

Integration:
- Version init: triggered on first delegate_task of an SDD cycle
- Version close: triggered when orchestrator calls honcho_conclude after completing work
- Mid-session additions: appended to plan.md with timestamp
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VERSIONS_DIR_NAME = "context/appVersions"


def _get_project_root() -> Optional[Path]:
    """Find the project root (first parent with .git or context/ dir)."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists() or (parent / "context").exists():
            return parent
    return cwd


def _get_versions_dir() -> Path:
    root = _get_project_root()
    return root / VERSIONS_DIR_NAME


def get_latest_version() -> Optional[str]:
    """Find the latest version from existing appVersions folders."""
    versions_dir = _get_versions_dir()
    if not versions_dir.exists():
        return None

    version_pattern = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
    versions = []

    for entry in versions_dir.iterdir():
        if entry.is_dir():
            match = version_pattern.match(entry.name)
            if match:
                versions.append(tuple(int(x) for x in match.groups()))

    if not versions:
        return None

    latest = max(versions)
    return f"{latest[0]}.{latest[1]}.{latest[2]}"


def next_version(bump: str = "patch") -> str:
    """Calculate next version number."""
    latest = get_latest_version()
    if not latest:
        return "0.1.0"

    parts = [int(x) for x in latest.split(".")]

    if bump == "major":
        parts[0] += 1
        parts[1] = 0
        parts[2] = 0
    elif bump == "minor":
        parts[1] += 1
        parts[2] = 0
    else:
        parts[2] += 1

    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def init_version(user_prompt: str, version: Optional[str] = None) -> str:
    """Create a new version directory with the original prompt.

    Returns the version string (e.g., "0.1.0").
    """
    if version is None:
        version = next_version()

    versions_dir = _get_versions_dir()
    version_dir = versions_dir / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)

    # Save original prompt
    prompt_path = version_dir / "original_prompt.md"
    if not prompt_path.exists():
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prompt_path.write_text(
            f"---\nversion: {version}\ndate: {now}\n---\n\n{user_prompt}\n",
            encoding="utf-8",
        )
        logger.info("cobalt-routing: version v%s initialized at %s", version, version_dir)

    return version


def save_plan(version: str, phases: str, tasks: str = "") -> None:
    """Save the execution plan for a version."""
    versions_dir = _get_versions_dir()
    version_dir = versions_dir / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)

    plan_path = version_dir / "plan.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    content = f"# Plan v{version}\n\nCreated: {now}\n\n## Phases\n{phases}\n"
    if tasks:
        content += f"\n## Tasks\n{tasks}\n"

    plan_path.write_text(content, encoding="utf-8")
    logger.info("cobalt-routing: plan saved for v%s", version)


def append_to_plan(version: str, addition: str) -> None:
    """Append a mid-session modification to the plan."""
    versions_dir = _get_versions_dir()
    plan_path = versions_dir / f"v{version}" / "plan.md"

    if not plan_path.exists():
        logger.warning("cobalt-routing: no plan.md for v%s, creating one", version)
        save_plan(version, "Unknown", "")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(plan_path, "a", encoding="utf-8") as f:
        f.write(f"\n\n## Modification ({now})\n{addition}\n")

    logger.info("cobalt-routing: appended modification to v%s plan", version)


def close_version(version: str, summary: str, changes: list = None) -> None:
    """Close a version by generating its changelog."""
    versions_dir = _get_versions_dir()
    version_dir = versions_dir / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)

    changelog_path = version_dir / "changelog.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    content = f"# v{version}\n\n**Date**: {now}\n\n## Summary\n{summary}\n"
    if changes:
        content += "\n## Changes\n"
        for change in changes:
            content += f"- {change}\n"

    changelog_path.write_text(content, encoding="utf-8")
    logger.info("cobalt-routing: version v%s closed", version)


# State tracking for the current session
_current_version: Optional[str] = None


def get_current_version() -> Optional[str]:
    """Get the version being worked on in this session."""
    return _current_version


def set_current_version(version: str) -> None:
    """Set the active version for this session."""
    global _current_version
    _current_version = version
