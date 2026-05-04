#!/usr/bin/env python3
"""
Cobalt Routing — Source Patch Applicator for delegate_tool.py

This script applies (or verifies/reverts) the minimal patch that enables
per-task model routing in Hermes Agent's delegation system.

The patch modifies ONE call site in delegate_tool.py to read _routed_* fields
from task dicts and pass them to _build_child_agent. The _build_child_agent
function already accepts override_provider/base_url/api_key/api_mode natively —
this patch simply wires them up from the task dict.

Usage:
    python apply_routing_patch.py [apply|verify|revert]

    apply   — Apply the patch (idempotent, safe to run multiple times)
    verify  — Check if patch is applied (exit 0 = applied, exit 1 = not applied)
    revert  — Remove the patch, restore original lines

The patch is designed to be resilient:
- Searches for the exact pattern rather than relying on line numbers
- Fails safely if delegate_tool.py structure has changed
- Leaves clear markers (# cobalt-routing patch) for detection
"""

import re
import sys
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
DELEGATE_TOOL = HERMES_HOME / "hermes-agent" / "tools" / "delegate_tool.py"

PATCH_MARKER = "# cobalt-routing patch"

# Original pattern: model=...creds["model"]
# We need to find the _build_child_agent call inside the task loop
ORIGINAL_LINES = [
    ('model=', 'creds["model"]'),
    ('override_provider=', 'creds["provider"]'),
    ('override_base_url=', 'creds["base_url"]'),
    ('override_api_key=', 'creds["api_key"]'),
    ('override_api_mode=', 'creds["api_mode"]'),
]

PATCHED_REPLACEMENTS = {
    'model=': 'model=t.get("_routed_model") or creds["model"],  {marker}',
    'override_provider=': 'override_provider=t.get("_routed_provider") or creds["provider"],  {marker}',
    'override_base_url=': 'override_base_url=t.get("_routed_base_url") or creds["base_url"],  {marker}',
    'override_api_key=': 'override_api_key=t.get("_routed_api_key") or creds["api_key"],  {marker}',
    'override_api_mode=': 'override_api_mode=t.get("_routed_api_mode") or creds["api_mode"],  {marker}',
}


def find_patch_location(content: str):
    """Find the _build_child_agent call inside 'for i, t in enumerate(task_list)'."""
    # Look for the task loop followed by _build_child_agent
    pattern = r'for i, t in enumerate\(task_list\):.*?_build_child_agent\('
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return None
    return match.start()


def is_patched(content: str) -> bool:
    return PATCH_MARKER in content


def apply_patch(content: str) -> str:
    """Apply the routing patch to delegate_tool.py content."""
    if is_patched(content):
        print("Patch already applied. Nothing to do.")
        return content

    lines = content.split('\n')
    patched_lines = []
    in_target_block = False
    found_task_loop = False
    found_build_child = False
    changes = 0

    for i, line in enumerate(lines):
        # Detect we're in the task_list loop
        if 'for i, t in enumerate(task_list):' in line:
            found_task_loop = True

        # Detect _build_child_agent call within the loop
        if found_task_loop and '_build_child_agent(' in line:
            found_build_child = True
            in_target_block = True

        if in_target_block:
            stripped = line.lstrip()

            # model= line (only the one using creds["model"])
            if stripped.startswith('model=') and 'creds["model"]' in line:
                indent = line[:len(line) - len(stripped)]
                patched_lines.append(
                    f'{indent}model=t.get("_routed_model") or creds["model"],  {PATCH_MARKER}'
                )
                changes += 1
                continue

            # override_provider= line
            if stripped.startswith('override_provider=') and 'creds["provider"]' in line:
                indent = line[:len(line) - len(stripped)]
                patched_lines.append(
                    f'{indent}override_provider=t.get("_routed_provider") or creds["provider"],  {PATCH_MARKER}'
                )
                changes += 1
                continue

            # override_base_url= line
            if stripped.startswith('override_base_url=') and 'creds["base_url"]' in line:
                indent = line[:len(line) - len(stripped)]
                patched_lines.append(
                    f'{indent}override_base_url=t.get("_routed_base_url") or creds["base_url"],  {PATCH_MARKER}'
                )
                changes += 1
                continue

            # override_api_key= line
            if stripped.startswith('override_api_key=') and 'creds["api_key"]' in line:
                indent = line[:len(line) - len(stripped)]
                patched_lines.append(
                    f'{indent}override_api_key=t.get("_routed_api_key") or creds["api_key"],  {PATCH_MARKER}'
                )
                changes += 1
                continue

            # override_api_mode= line
            if stripped.startswith('override_api_mode=') and 'creds["api_mode"]' in line:
                indent = line[:len(line) - len(stripped)]
                patched_lines.append(
                    f'{indent}override_api_mode=t.get("_routed_api_mode") or creds["api_mode"],  {PATCH_MARKER}'
                )
                changes += 1
                continue

            # End of _build_child_agent call (closing paren at same indent or less)
            if changes > 0 and (stripped.startswith(')') or stripped == ''):
                in_target_block = False

        patched_lines.append(line)

    if changes < 5:
        print(f"WARNING: Only {changes}/5 patch points found. delegate_tool.py may have changed.")
        if changes == 0:
            print("ERROR: Could not find patch location. Aborting.")
            return content

    return '\n'.join(patched_lines)


def revert_patch(content: str) -> str:
    """Remove the cobalt-routing patch, restoring original lines."""
    if not is_patched(content):
        print("Patch not detected. Nothing to revert.")
        return content

    lines = content.split('\n')
    reverted = []
    changes = 0

    for line in lines:
        if PATCH_MARKER in line:
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]

            if stripped.startswith('model='):
                reverted.append(f'{indent}model=creds["model"],')
            elif stripped.startswith('override_provider='):
                reverted.append(f'{indent}override_provider=creds["provider"],')
            elif stripped.startswith('override_base_url='):
                reverted.append(f'{indent}override_base_url=creds["base_url"],')
            elif stripped.startswith('override_api_key='):
                reverted.append(f'{indent}override_api_key=creds["api_key"],')
            elif stripped.startswith('override_api_mode='):
                reverted.append(f'{indent}override_api_mode=creds["api_mode"],')
            else:
                reverted.append(line)
            changes += 1
        else:
            reverted.append(line)

    print(f"Reverted {changes} patched lines.")
    return '\n'.join(reverted)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "verify"

    if not DELEGATE_TOOL.exists():
        print(f"ERROR: {DELEGATE_TOOL} not found.")
        print("Is Hermes Agent installed at ~/.hermes/hermes-agent/?")
        sys.exit(2)

    content = DELEGATE_TOOL.read_text(encoding="utf-8")

    if action == "verify":
        if is_patched(content):
            print(f"OK: Patch is applied in {DELEGATE_TOOL}")
            sys.exit(0)
        else:
            print(f"NOT APPLIED: Patch not found in {DELEGATE_TOOL}")
            sys.exit(1)

    elif action == "apply":
        result = apply_patch(content)
        if result != content:
            DELEGATE_TOOL.write_text(result, encoding="utf-8")
            print(f"Patch applied to {DELEGATE_TOOL}")
        sys.exit(0)

    elif action == "revert":
        result = revert_patch(content)
        if result != content:
            DELEGATE_TOOL.write_text(result, encoding="utf-8")
            print(f"Patch reverted in {DELEGATE_TOOL}")
        sys.exit(0)

    else:
        print(f"Unknown action: {action}")
        print("Usage: apply_routing_patch.py [apply|verify|revert]")
        sys.exit(1)


if __name__ == "__main__":
    main()
