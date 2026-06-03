"""Cobalt Firewall — Port of the praxis-ai irreversibility firewall (TypeScript → Python).

Engine module: pure logic, no Hermes imports.

Tokeniser: quote-aware, substitution-aware splitter that splits on ;, &&, ||, |, &
but does NOT split inside single/double quotes or $(...)/backtick substitutions.

17 rules ported faithfully from praxis-ai/src/lib/ast/rules.ts, each with an id
and a reversibility class.

inspect_command(command) -> InspectResult
evaluate(command, mode)  -> dict  (fail-open: exceptions → allow)

Modes:
  strict  — block on ANY hit
  warn    — block ONLY on hits in IRREVERSIBLE_CLASSES (data-loss, history-rewrite)
  off     — never block
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reversibility classes
# ---------------------------------------------------------------------------

IRREVERSIBLE_CLASSES = frozenset({"data-loss", "history-rewrite"})

VALID_MODES = frozenset({"strict", "warn", "off"})

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Hit:
    rule_id: str
    reversibility: str
    excerpt: str
    reason: str


@dataclass
class InspectResult:
    decision: str  # "allow" | "block"
    hits: List[Hit] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tokeniser — ported from tokeniser.ts
# ---------------------------------------------------------------------------


def tokenise_bash(input_str: str) -> list[str]:
    """Split a shell command string into discrete commands.

    Splits on ;, &&, ||, |, & at top level only (not inside quotes or
    $(...)/backtick substitutions). Returns at least one token.
    """
    tokens: list[str] = []
    buf: list[str] = []
    i = 0
    # context_stack: 'single' | 'double' | 'subst-paren' | 'subst-back'
    context_stack: list[str] = []

    def flush() -> None:
        text = "".join(buf).strip()
        if text or not tokens:
            tokens.append(text)
        buf.clear()

    length = len(input_str)
    while i < length:
        ch = input_str[i]
        nxt = input_str[i + 1] if i + 1 < length else ""
        ctx = context_stack[-1] if context_stack else None

        # --- single-quote context ---
        if ctx == "single":
            if ch == "'":
                context_stack.pop()
            buf.append(ch)
            i += 1
            continue

        # --- double-quote context ---
        if ctx == "double":
            if ch == "\\" and nxt:
                buf.append(ch)
                buf.append(nxt)
                i += 2
                continue
            if ch == '"':
                context_stack.pop()
                buf.append(ch)
                i += 1
                continue
            if ch == "$" and nxt == "(":
                context_stack.append("subst-paren")
                buf.append("$(")
                i += 2
                continue
            if ch == "`":
                context_stack.append("subst-back")
                buf.append(ch)
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue

        # --- $(...) substitution context ---
        if ctx == "subst-paren":
            if ch == ")":
                context_stack.pop()
            elif ch == "(":
                context_stack.append("subst-paren")
            elif ch == "'":
                context_stack.append("single")
            elif ch == '"':
                context_stack.append("double")
            buf.append(ch)
            i += 1
            continue

        # --- backtick substitution context ---
        if ctx == "subst-back":
            if ch == "`":
                context_stack.pop()
            buf.append(ch)
            i += 1
            continue

        # --- top level ---
        if ch == "\\" and nxt:
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue
        if ch == "'":
            context_stack.append("single")
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            context_stack.append("double")
            buf.append(ch)
            i += 1
            continue
        if ch == "$" and nxt == "(":
            context_stack.append("subst-paren")
            buf.append("$(")
            i += 2
            continue
        if ch == "`":
            context_stack.append("subst-back")
            buf.append(ch)
            i += 1
            continue

        # splitting operators (order matters: && before &, || before |)
        if ch == "&" and nxt == "&":
            flush()
            i += 2
            continue
        if ch == "|" and nxt == "|":
            flush()
            i += 2
            continue
        if ch == ";":
            flush()
            i += 1
            continue
        if ch == "|":
            flush()
            i += 1
            continue
        if ch == "&":
            flush()
            i += 1
            continue

        buf.append(ch)
        i += 1

    flush()
    return tokens


def extract_substitutions(input_str: str) -> list[str]:
    """Return the bodies of all $(...) and backtick substitutions.

    Ported from tokeniser.ts:extractSubstitutions.
    """
    out: list[str] = []
    i = 0
    length = len(input_str)
    while i < length:
        if input_str[i] == "$" and i + 1 < length and input_str[i + 1] == "(":
            depth = 1
            j = i + 2
            start = j
            while j < length and depth > 0:
                if input_str[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                if input_str[j] == "(":
                    depth += 1
                elif input_str[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(input_str[start:j])
            i = j + 1
            continue
        if input_str[i] == "`":
            j = i + 1
            start = j
            while j < length and input_str[j] != "`":
                if input_str[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                j += 1
            out.append(input_str[start:j])
            i = j + 1
            continue
        i += 1
    return out


# ---------------------------------------------------------------------------
# Helper: strip quoted regions before token-splitting (ported from rules.ts)
# ---------------------------------------------------------------------------


def _strip_quoted(command: str) -> str:
    """Strip single- and double-quoted regions so downstream token split
    only sees operative shell words.

    Prevents false positives on commit messages like git commit -m "rm -rf".
    Ported from rules.ts:stripQuoted.
    """
    out: list[str] = []
    i = 0
    length = len(command)
    while i < length:
        ch = command[i]
        if ch == "'":
            end = command.find("'", i + 1)
            if end == -1:
                out.append(ch)
                i += 1
                continue
            i = end + 1
            continue
        if ch == '"':
            j = i + 1
            while j < length:
                if command[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                if command[j] == '"':
                    break
                j += 1
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Command wrappers that prefix a real command (the inner command is what the
# token-based rules must inspect). `sudo`/`doas` are also detected on their own
# by _rule_sudo_escalation (which reads RAW tokens), so stripping them here does
# not weaken sudo detection — it lets the inner rm/git/dd rules fire too.
_WRAPPERS = frozenset({"env", "sudo", "doas"})
# sudo/doas options that consume a following argument.
_SUDO_ARG_OPTS = frozenset(
    {"-u", "-g", "-p", "-C", "-U", "-r", "-t", "-T", "-R", "-h",
     "--user", "--group", "--prompt", "--chdir", "--other-user"}
)


def _normalize_tokens(toks: list[str]) -> list[str]:
    """Normalise a token list so command-word rules are not bypassed by:

    - leading env-var assignments: ``FOO=bar rm -rf /``           (L2)
    - wrapper commands: ``env FOO=bar rm -rf /`` / ``sudo rm -rf /``
      (incl. sudo options: ``sudo -u root rm -rf /``)
    - a full path to the binary: ``/bin/rm -rf /``                (L1)

    Only the COMMAND word (first remaining token) is basename-normalised; the
    rest of the argv is left untouched so index-based rules keep working.
    """
    out = list(toks)
    changed = True
    while changed and out:
        changed = False
        # Drop leading VAR=val assignments.
        while out and _ENV_ASSIGN_RE.match(out[0]):
            out = out[1:]
            changed = True
        # Drop a leading wrapper command (and, for sudo/doas, its options).
        if out and out[0] in _WRAPPERS:
            wrapper = out[0]
            out = out[1:]
            changed = True
            if wrapper in ("sudo", "doas"):
                while out and out[0].startswith("-"):
                    if out[0] == "--":
                        out = out[1:]
                        break
                    if out[0] in _SUDO_ARG_OPTS and len(out) > 1:
                        out = out[2:]
                    else:
                        out = out[1:]
    # Basename the command word (path-prefix bypass).
    if out and "/" in out[0]:
        out[0] = out[0].rsplit("/", 1)[-1]
    return out


def _raw_tokens(command: str) -> list[str]:
    """Shell words after quote-stripping, WITHOUT wrapper normalisation.
    Used by the sudo rule so `sudo` is still detected as the first word."""
    return [t for t in _strip_quoted(command).split() if t]


def _tokens(command: str) -> list[str]:
    """Split command into shell words after stripping quoted regions, then
    normalise away env-prefix / wrapper / path-prefix command bypasses."""
    return _normalize_tokens(_raw_tokens(command))


# ---------------------------------------------------------------------------
# 17 rules — ported faithfully from rules.ts
# ---------------------------------------------------------------------------


def _rule_rm_recursive_force(command: str) -> Optional[Hit]:
    """Recursive rm in any form (-r, -R, -rf, -fr, --recursive, ...), WITH OR
    WITHOUT -f. Recursive deletion is irreversible regardless of the force flag —
    `rm -r dir` alone still wipes a whole tree. (A live red-team test showed an
    agent bypassing an -rf-only rule by re-running as `rm -r`.)"""
    toks = _tokens(command)
    if not toks or toks[0] != "rm":
        return None
    recursive = False
    for t in toks[1:]:
        if t in ("--recursive", "-r", "-R"):
            recursive = True
        elif t.startswith("-") and not t.startswith("--"):
            if "r" in t or "R" in t:
                recursive = True
    if recursive:
        return Hit(
            rule_id="rm-recursive-force",
            reversibility="data-loss",
            excerpt=command[:80],
            reason="`rm` with a recursive flag (-r/-R/--recursive) deletes a directory tree. Irreversible, with or without -f.",
        )
    return None


# Full-string safety net for catastrophic patterns that the token-based rules
# miss when they appear INSIDE code (execute_code) or wrapped/quoted strings —
# e.g. os.system("rm -rf /"), shutil.rmtree(...), subprocess shell strings.
_CODE_DESTRUCTIVE_RE = re.compile(
    r"shutil\.rmtree|os\.removedirs|os\.rmdir\b|pathlib[^\n]*\.rmdir|"
    r"\brm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b|"
    r"\bmkfs(?:\.\w+)?\b|\bwipefs\b|\bshred\b|"
    r"of=/dev/(?:sd|nvme|hd|disk|loop|xvd|vd|mapper/)"
)


def _rule_code_destructive(command: str) -> Optional[Hit]:
    """Regex safety net over the full string for catastrophic/irreversible
    patterns embedded in code or quoted payloads (where _tokens() can't see the
    real command word): recursive rm, shutil.rmtree/os.removedirs, mkfs/wipefs/
    shred, dd to a block device. Used especially for the execute_code/process
    tools whose payload is arbitrary code, not a bare shell command."""
    if _CODE_DESTRUCTIVE_RE.search(command):
        return Hit(
            rule_id="code-destructive",
            reversibility="data-loss",
            excerpt=command[:80],
            reason="Irreversible pattern (recursive delete / shutil.rmtree / mkfs / dd-to-device) detected in command or code payload.",
        )
    return None


def _rule_find_delete(command: str) -> Optional[Hit]:
    """find ... -delete or find ... -exec rm"""
    toks = _tokens(command)
    if not toks or toks[0] != "find":
        return None
    if "-delete" in toks:
        return Hit(
            rule_id="find-delete",
            reversibility="data-loss",
            excerpt=command[:80],
            reason="`find -delete` removes files matching a pattern. Irreversible.",
        )
    try:
        exec_idx = toks.index("-exec")
        if exec_idx >= 0 and len(toks) > exec_idx + 1 and toks[exec_idx + 1] == "rm":
            return Hit(
                rule_id="find-delete",
                reversibility="data-loss",
                excerpt=command[:80],
                reason="`find -exec rm` removes files matching a pattern. Irreversible.",
            )
    except ValueError:
        pass
    return None


def _rule_git_force_push(command: str) -> Optional[Hit]:
    """git force-push variants (--force, -f, --force-with-lease)"""
    toks = _tokens(command)
    if len(toks) < 2 or toks[0] != "git" or toks[1] != "push":
        return None
    for t in toks[2:]:
        # --force, --force-with-lease, -f, and fused short-flag clusters
        # containing f (e.g. -fv, -fu). (L4)
        fused_force = (
            t.startswith("-")
            and not t.startswith("--")
            and "f" in t[1:]
        )
        if t in ("--force", "-f") or t.startswith("--force-with-lease") or fused_force:
            return Hit(
                rule_id="git-force-push",
                reversibility="history-rewrite",
                excerpt=command[:80],
                reason=(
                    "Force-push overwrites remote history. "
                    "Even `--force-with-lease` rewrites published commits."
                ),
            )
    return None


def _rule_git_reset_hard(command: str) -> Optional[Hit]:
    """git reset --hard"""
    toks = _tokens(command)
    if len(toks) < 2 or toks[0] != "git" or toks[1] != "reset":
        return None
    if "--hard" in toks:
        return Hit(
            rule_id="git-reset-hard",
            reversibility="data-loss",
            excerpt=command[:80],
            reason="`git reset --hard` discards uncommitted changes. Irreversible.",
        )
    return None


def _rule_git_update_ref(command: str) -> Optional[Hit]:
    """git update-ref against refs/heads/* or refs/tags/*"""
    toks = _tokens(command)
    if len(toks) < 2 or toks[0] != "git" or toks[1] != "update-ref":
        return None
    for t in toks[2:]:
        if t.startswith("refs/heads/") or t.startswith("refs/tags/"):
            return Hit(
                rule_id="git-update-ref",
                reversibility="history-rewrite",
                excerpt=command[:80],
                reason=(
                    "`git update-ref` against `refs/heads/*` or `refs/tags/*` bypasses "
                    "the porcelain layer and rewrites a ref to an arbitrary commit. Hard to walk back."
                ),
            )
    return None


def _rule_git_filter_branch(command: str) -> Optional[Hit]:
    """git filter-branch — bulk history rewrite"""
    toks = _tokens(command)
    if len(toks) < 2 or toks[0] != "git" or toks[1] != "filter-branch":
        return None
    return Hit(
        rule_id="git-filter-branch",
        reversibility="history-rewrite",
        excerpt=command[:80],
        reason=(
            "`git filter-branch` rewrites every commit on every ref it touches. "
            "Recovery requires the reflog to still hold the old tips."
        ),
    )


def _rule_no_verify(command: str) -> Optional[Hit]:
    """Any --no-verify or --no-gpg-sign (skips hooks/signing)"""
    toks = _tokens(command)
    if any(t in ("--no-verify", "--no-gpg-sign") for t in toks):
        return Hit(
            rule_id="no-verify",
            reversibility="exec-bypass",
            excerpt=command[:80],
            reason=(
                "`--no-verify` or `--no-gpg-sign` bypasses pre-commit hooks "
                "or signature requirements."
            ),
        )
    return None


def _rule_sudo_escalation(command: str) -> Optional[Hit]:
    """sudo + anything. Reads RAW tokens because _tokens() strips the sudo
    wrapper (so inner rm/git/dd rules can fire); sudo itself is detected here."""
    toks = _raw_tokens(command)
    if toks and toks[0] in ("sudo", "doas"):
        return Hit(
            rule_id="sudo-escalation",
            reversibility="sudo-escalation",
            excerpt=command[:80],
            reason="Privilege escalation via sudo/doas is blocked in cobalt sessions.",
        )
    return None


def _rule_encoded_execution(command: str) -> Optional[Hit]:
    """Encoded execution patterns — decoder piped into a shell."""
    # Decoder piped into a shell: base64/base32/xxd/openssl … | (sh|bash|...)
    if re.search(
        r"\b(base64|base32|xxd|openssl)\b\s+[^|]*\|\s*(sh|bash|zsh|fish|exec|eval)\b",
        command,
    ):
        return Hit(
            rule_id="encoded-execution",
            reversibility="exec-bypass",
            excerpt=command[:80],
            reason=(
                "Decoding a payload (base64/xxd/openssl) into a shell or eval "
                "is a deny-list bypass pattern."
            ),
        )
    # eval/exec of a command substitution body that decodes a payload
    if re.search(
        r"\b(eval|exec)\s+[\"']?\$\([^)]*\b(base64|base32|xxd|openssl)\b[^)]*\)",
        command,
    ):
        return Hit(
            rule_id="encoded-execution",
            reversibility="exec-bypass",
            excerpt=command[:80],
            reason=(
                "eval/exec of a command-substitution body that decodes a payload "
                "is a deny-list bypass pattern."
            ),
        )
    # Hex-encoded printf piped to a shell
    if re.search(
        r"\bprintf\s+[\"']?\\x[0-9a-fA-F]{2}[^|]*\|\s*(sh|bash|exec|eval)\b",
        command,
    ):
        return Hit(
            rule_id="encoded-execution",
            reversibility="exec-bypass",
            excerpt=command[:80],
            reason="Hex-encoded printf piped to a shell is a deny-list bypass pattern.",
        )
    return None


def _rule_dd_block_device(command: str) -> Optional[Hit]:
    """dd to a block device"""
    if not re.match(r"^\s*dd\b", command):
        return None
    # Covers bare-metal (sd*, nvme*, hd*), macOS (disk*), loopback (loop*),
    # and virtualised disks common on a VPS: Xen (xvd*), VirtIO (vd*),
    # and device-mapper (mapper/*). (L5)
    if re.search(r"of=/dev/(sd|nvme|hd|disk|loop|xvd|vd|mapper/)", command):
        return Hit(
            rule_id="dd-block-device",
            reversibility="data-loss",
            excerpt=command[:80],
            reason="`dd of=/dev/sdX` overwrites a block device. Irreversible.",
        )
    return None


def _rule_mkfs(command: str) -> Optional[Hit]:
    """Disk-format tools: mkfs, wipefs, shred"""
    if re.match(r"^\s*(mkfs|mkfs\.\w+|wipefs|shred)\b", command):
        return Hit(
            rule_id="mkfs",
            reversibility="data-loss",
            excerpt=command[:80],
            reason="Filesystem creation, wipe, or shred is irreversible.",
        )
    return None


def _rule_chmod_recursive_permissive(command: str) -> Optional[Hit]:
    """chmod -R 777 / 666 — recursive world-writable permissions"""
    toks = _tokens(command)
    if not toks or toks[0] != "chmod":
        return None
    recursive = False
    permissive = False
    for t in toks[1:]:
        if t in ("-R", "--recursive"):
            recursive = True
        elif t.startswith("-") and not t.startswith("--"):
            if "R" in t:
                recursive = True
        # octal modes
        if re.match(r"^[0-7]{3,4}$", t):
            world_digit = t[-1]
            if world_digit in ("6", "7"):
                permissive = True
        if t in ("777", "666") or t.endswith(",o+w") or t == "a+w":
            permissive = True
    if recursive and permissive:
        return Hit(
            rule_id="chmod-recursive-permissive",
            reversibility="data-loss",
            excerpt=command[:80],
            reason=(
                "`chmod -R` to a world-writable mode (e.g. 777, 666) exposes the tree "
                "to any user. Hard to walk back without an audit."
            ),
        )
    return None


def _rule_chown_recursive(command: str) -> Optional[Hit]:
    """chown -R against an unbounded path — catastrophic ownership flip"""
    toks = _tokens(command)
    if not toks or toks[0] != "chown":
        return None
    recursive = False
    rootlike_target = False
    for t in toks[1:]:
        if t in ("-R", "--recursive"):
            recursive = True
        elif t.startswith("-") and not t.startswith("--"):
            if "R" in t:
                recursive = True
        if t in ("/", "/*", "/usr", "/etc", "/var"):
            rootlike_target = True
    if recursive and rootlike_target:
        return Hit(
            rule_id="chown-recursive",
            reversibility="data-loss",
            excerpt=command[:80],
            reason=(
                "`chown -R` against `/`, `/usr`, `/etc`, or `/var` rewrites system ownership. "
                "Hard to recover without a known-good backup."
            ),
        )
    return None


def _rule_tar_absolute_names(command: str) -> Optional[Hit]:
    """tar with --absolute-names / -P while extracting"""
    toks = _tokens(command)
    if not toks or toks[0] != "tar":
        return None
    absolute = False
    extracting = False
    for t in toks[1:]:
        if t in ("--absolute-names", "-P"):
            absolute = True
        if t in ("-x", "--extract", "--get"):
            extracting = True
        elif t.startswith("-") and not t.startswith("--"):
            if "x" in t:
                extracting = True
            if "P" in t:
                absolute = True
    if absolute and extracting:
        return Hit(
            rule_id="tar-absolute-names",
            reversibility="data-loss",
            excerpt=command[:80],
            reason=(
                "`tar -x --absolute-names` writes outside the current working directory "
                "at archive-controlled paths. Path-traversal risk."
            ),
        )
    return None


def _rule_curl_pipe_shell(command: str) -> Optional[Hit]:
    """curl | sh and wget | sh — remote-code-execution patterns."""
    if re.search(
        r"\b(curl|wget|fetch)\s+[^|]+\|\s*(sh|bash|zsh|fish|exec|eval)\b",
        command,
    ):
        return Hit(
            rule_id="curl-pipe-shell",
            reversibility="exec-bypass",
            excerpt=command[:80],
            reason=(
                "Piping a download (curl/wget/fetch) directly into a shell "
                "executes remote code without inspection."
            ),
        )
    return None


def _rule_pip_install_target_root(command: str) -> Optional[Hit]:
    """pip install --target /"""
    toks = _tokens(command)
    if not toks or toks[0] not in ("pip", "pip3"):
        return None
    if len(toks) < 2 or toks[1] != "install":
        return None
    for i, t in enumerate(toks[2:], start=2):
        if t in ("--target", "-t") and i + 1 < len(toks):
            dest = toks[i + 1]
            if dest == "/" or dest.startswith("/usr") or dest.startswith("/etc"):
                return Hit(
                    rule_id="pip-install-target-root",
                    reversibility="data-loss",
                    excerpt=command[:80],
                    reason="`pip install --target` to `/`, `/usr`, or `/etc` overwrites system files.",
                )
    return None


def _rule_npm_install_force(command: str) -> Optional[Hit]:
    """npm install --force / -f"""
    toks = _tokens(command)
    if not toks or toks[0] not in ("npm", "pnpm", "yarn"):
        return None
    if len(toks) < 2 or toks[1] not in ("install", "i", "add"):
        return None
    for t in toks[2:]:
        if t in ("--force", "-f"):
            return Hit(
                rule_id="npm-install-force",
                reversibility="exec-bypass",
                excerpt=command[:80],
                reason=(
                    "`npm install --force` (or pnpm/yarn equivalent) skips peer-dependency "
                    "conflict resolution. Often masks a real dependency-graph problem and "
                    "writes a lockfile that lies."
                ),
            )
    return None


# All 17 rules as a list of callables
_ALL_RULES = [
    _rule_rm_recursive_force,
    _rule_find_delete,
    _rule_git_force_push,
    _rule_git_reset_hard,
    _rule_git_update_ref,
    _rule_git_filter_branch,
    _rule_no_verify,
    _rule_sudo_escalation,
    _rule_encoded_execution,
    _rule_dd_block_device,
    _rule_mkfs,
    _rule_chmod_recursive_permissive,
    _rule_chown_recursive,
    _rule_tar_absolute_names,
    _rule_curl_pipe_shell,
    _rule_pip_install_target_root,
    _rule_npm_install_force,
]


# ---------------------------------------------------------------------------
# Inspector — ported from inspect.ts
# ---------------------------------------------------------------------------


# Bodies of `<shell> -c "<body>"` so a payload hidden behind -c is re-inspected
# instead of being stripped away by _strip_quoted. (L3)
_SHELL_C_RE = re.compile(
    r"\b(?:bash|sh|dash|zsh|ksh)\s+(?:-[a-z]*\s+)*-c\s+(['\"])(.+?)\1",
    re.DOTALL,
)


def _extract_shell_c_bodies(command: str) -> list[str]:
    """Return the quoted bodies passed to `<shell> -c`."""
    return [m.group(2) for m in _SHELL_C_RE.finditer(command)]


def inspect_command(command: str, is_code: bool = False) -> InspectResult:
    """Run all rules against the full string, each tokenised sub-command,
    and each substitution body. Returns decision=allow|block + hits list.

    is_code=True ALSO runs the _rule_code_destructive regex safety net over the
    full string. That net is code-only because it matches dangerous substrings
    (e.g. `rm -rf`, shutil.rmtree) regardless of quoting — correct for an
    execute_code/process payload, but a false-positive source for a plain shell
    command like `git commit -m "rm -rf in the message"`, where the token rules
    (which strip quotes) are authoritative instead.

    Mirrors inspectBashCommand from inspect.ts, plus two hardening passes over
    the source: `<shell> -c` body extraction (L3) and one extra level of nested
    command-substitution recursion (M1).
    """
    hits: list[Hit] = []
    seen: set[str] = set()

    def add_hit(hit: Hit) -> None:
        key = f"{hit.rule_id}:{hit.reason}"
        if key not in seen:
            seen.add(key)
            hits.append(hit)

    # Build the inspection queue: full string + substitution bodies (one extra
    # nesting level, M1) + shell -c bodies (L3).
    queue: list[str] = [command]
    for subst in extract_substitutions(command):
        queue.append(subst)
        # M1: recurse one level deeper into nested $(...) / backticks.
        for inner in extract_substitutions(subst):
            queue.append(inner)
    for body in _extract_shell_c_bodies(command):
        queue.append(body)
        for subst in extract_substitutions(body):
            queue.append(subst)

    for raw in queue:
        # Rules on the full (possibly chained) string first — catches cross-command patterns
        for rule_fn in _ALL_RULES:
            hit = rule_fn(raw)
            if hit:
                add_hit(hit)
        # Rules on each individual tokenised sub-command
        for sub in tokenise_bash(raw):
            if not sub:
                continue
            for rule_fn in _ALL_RULES:
                hit = rule_fn(sub)
                if hit:
                    add_hit(hit)

    # Code-only regex safety net (execute_code / process payloads).
    if is_code:
        code_hit = _rule_code_destructive(command)
        if code_hit:
            add_hit(code_hit)

    decision = "block" if hits else "allow"
    return InspectResult(decision=decision, hits=hits)


# ---------------------------------------------------------------------------
# Public evaluate() — mode-aware, fail-open
# ---------------------------------------------------------------------------


def evaluate(command: str, mode: str = "strict", is_code: bool = False) -> dict:
    """Evaluate a shell command (or code payload) against the firewall rules.

    Args:
        command: The shell command or code string to evaluate.
        mode:    'strict' — block on any hit.
                 'warn'   — block only on hits in IRREVERSIBLE_CLASSES.
                 'off'    — never block.
        is_code: True for execute_code/process payloads — enables the code-only
                 destructive-pattern safety net (see inspect_command).

    Returns a dict:
        {
          "blocked": bool,
          "mode": str,
          "hits": [{"rule_id", "reversibility", "excerpt", "reason"}, ...],
          "message": str,
        }

    FAIL-OPEN: any exception during inspection → blocked=False (allow).
    """
    if mode not in VALID_MODES:
        mode = "strict"

    # off mode — never block, skip inspection entirely
    if mode == "off":
        return {"blocked": False, "mode": "off", "hits": [], "message": ""}

    try:
        result = inspect_command(command, is_code=is_code)
    except Exception as exc:
        logger.warning("cobalt-firewall: inspection error (fail-open): %s", exc)
        return {
            "blocked": False,
            "mode": mode,
            "hits": [],
            "message": "",
        }

    hits_data = [
        {
            "rule_id": h.rule_id,
            "reversibility": h.reversibility,
            "excerpt": h.excerpt,
            "reason": h.reason,
        }
        for h in result.hits
    ]

    if not result.hits:
        return {"blocked": False, "mode": mode, "hits": [], "message": ""}

    if mode == "strict":
        blocking_hits = result.hits
    else:  # warn
        blocking_hits = [h for h in result.hits if h.reversibility in IRREVERSIBLE_CLASSES]

    if not blocking_hits:
        # Non-irreversible hits in warn mode — allow but return hits for logging
        return {"blocked": False, "mode": mode, "hits": hits_data, "message": ""}

    lines = ["cobalt-firewall blocked this command. Triggered rules:"]
    for h in blocking_hits:
        lines.append(f"  [{h.rule_id}] ({h.reversibility}) {h.reason}")
    lines.append("")
    lines.append(
        "To allow this command, switch the firewall mode: "
        "use the cobalt_firewall tool with action='set' and mode='warn' or mode='off'. "
        "Or run the command yourself outside of the agent."
    )
    message = "\n".join(lines)

    return {
        "blocked": True,
        "mode": mode,
        "hits": hits_data,
        "message": message,
    }
