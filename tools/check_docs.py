#!/usr/bin/env python3
r"""Enforce the suite's documentation guardrails, instead of auditing for them by hand.

This suite already prefers executable invariants to prose -- orchestrator's `test_headless.py` is a
source scan, and `test_schema_registry.py` enforces schema coverage "not prose". This is the same
idea for the guardrails that live in every CLAUDE.md:

  1. No machine-specific absolute paths in tracked files (the "portable paths only" guardrail).
  2. No relative links that don't resolve (a doc pointing at a file that isn't there).
  3. No unmasked account numbers (the "mask to ****1234" guardrail).
  4. No out-of-repo pointers presented as authoritative (e.g. `~/.claude/plans/...`), which is how
     78KB of design ended up living on exactly one machine, untracked.
  5. No stale paths in ASCII file-layout trees. These are hand-maintained and drift silently: the
     namespace migration moved every module to src/cherrypick/<pkg>/ and both package READMEs still
     drew a flat src/, while two deleted slash commands stayed listed for a whole cleanup pass. A
     tree is a claim about the filesystem, so it can be checked like one.
  6. No unclosed markdown links -- `](path` with no `)`. Rule 2's regex needs the closing paren to
     match at all, so a truncated link is invisible to it: the link renders as literal text and the
     broken-link check never fires. Exactly one existed, and it had survived several doc passes.
  7. No documented command that points at nothing -- a `-m cherrypick.…` naming a module that does
     not exist, or a script path that resolves from nowhere a reader could plausibly be standing.
     This is the class that outlived every hand sweep, because the path was hidden inside something
     that did not look like a path: three commands ran `Start-Process python -ArgumentList
     'src\streamer.py'` for weeks after the migration moved that file, while the sweeps were
     matching `src/<mod>.py`. Prose can be stale and merely misleading; a command is either right
     or it is broken, so it is worth checking mechanically.

Run: python tools/check_docs.py
Exits non-zero on any finding, printing `path:line: message` so it reads like a linter.

Scope is `git ls-files` -- tracked files only. Untracked scratch and gitignored local tooling are
deliberately out of scope.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A doc may legitimately *quote* a bad path as an example of what not to do. Those lines say so.
_ALLOW_MARKERS = ("never", "not ", "no ", "don't", "do not", "avoid", "e.g.", "instead of", "wrong")

_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/)[A-Za-z0-9._-]+", re.I)
_HOME_POINTER = re.compile(r"~[\\/]\.claude[\\/]")
# 5+ consecutive digits adjacent to account wording, but not a masked ****1234 and not a year/port.
_ACCOUNT = re.compile(r"account[^\n]{0,24}?\b(?<!\*)(\d{5,})\b", re.I)
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
#: `](` that never closes on its line. Anchored on the same line deliberately -- a genuine multi-line
#: link is not idiomatic here, and allowing them would hide the truncation this is meant to catch.
_MD_LINK_OPEN = re.compile(r"\]\(([^)\n]*)$")
_FENCE = re.compile(r"^\s*```")
#: A tree row: leading indent (spaces plus the │ continuation bars), a branch marker, then the name.
_TREE_ROW = re.compile(r"^(?P<indent>[\s│|]*)(?:├──|└──|\|--|`--)\s*(?P<name>\S+)")
#: Placeholders (`eod-<date>.md`, `*.log`, `…`) name a shape, not a file. Nothing to resolve.
_PLACEHOLDER = ("<", ">", "*", "?", "...", "…")
#: A tree row may say outright that its entry is runtime-created and therefore absent from a fresh
#: checkout ("logs/  # Created at first run (gitignored; all rotated)"). Believe the annotation --
#: it is the author declaring the absence is intended, which is exactly what this check should not
#: flag. git check-ignore cannot settle it: a directory-only pattern like `logs/` will not match a
#: path that does not exist yet, because git cannot tell it is a directory.
_RUNTIME_MARKERS = ("gitignored", "created at", "created on", "at first run", "runtime")
#: A line inside a fence that actually invokes something, as opposed to sample output or a tree row.
_CMD_LINE = re.compile(r"\b(?:python3?|pythonw|Start-Process)\b")
#: A script path used as a command argument. The separator is required: a *bare* `run.py` or `tt.py`
#: is context-dependent ("run this from packages/orchestrator") and is also what tree rows are made
#: of, so demanding one keeps this rule off both. A separator is what the real bug class had --
#: `src/streamer.py` and the backslashed `'src\streamer.py'` inside a PowerShell argument list.
_CMD_PATH = re.compile(r"[\w.@-]*[/\\][\w./\\@-]*\.py\b")
#: `-m some.dotted.module`, including the quoted/comma form PowerShell needs ('-m','cherrypick.x').
_DASH_M = re.compile(r"-m[\s,'\"]+['\"]?(cherrypick\.[\w.]+)")

TEXT_SUFFIXES = {".md", ".toml", ".ini", ".cfg", ".yml", ".yaml", ".json", ".ps1", ".sh", ".py"}
DOC_SUFFIXES = {".md"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [ROOT / p for p in out if p.strip()]


def _is_example(line: str) -> bool:
    low = line.lower()
    return any(m in low for m in _ALLOW_MARKERS)


def _is_test(rel: str) -> bool:
    """Tests legitimately hold synthetic fixtures that look like violations.

    Two real cases this exists for, both false positives when the checks ran repo-wide:
    `test_tasks_cron.py` uses `/home/me/backup.sh` as a stand-in for a *foreign* crontab line it must
    not clobber, and `test_util.py` passes a bare `12345678` straight into `mask_account()` -- that is
    the test OF the masker, the opposite of a leak.

    So the path/account checks skip tests. The guardrails they enforce are about code that actually
    runs and about what reaches a reader; a fixture string is neither. Broken-link checking still
    applies everywhere, since that has no equivalent ambiguity.
    """
    return "/tests/" in rel or rel.startswith("tests/")


#: This file is necessarily full of examples of every pattern it detects -- the regexes themselves,
#: the message strings, and the docstring explaining the two real false positives. Linters routinely
#: exempt their own rule definitions for exactly this reason. Contorting the strings to dodge a
#: self-match would make the code worse to read for no gain.
SELF = "tools/check_docs.py"


def _exists_exact(p: Path) -> bool:
    """`Path.exists()`, but case-sensitive on every platform.

    Plain `.exists()` makes this whole tool answer differently depending on who runs it. Windows and
    macOS are case-insensitive by default, so a doc whose casing does not match the file resolves
    locally and 404s for a reader on a case-sensitive filesystem -- and on GitHub, which serves blob
    paths case-sensitively regardless of the author's OS.

    That is not hypothetical: it is exactly how this function came to exist. A stale tree entry passed
    clean on Windows and failed in CI on Linux, so the local run was reporting OK over a real error.
    Comparing against the actual directory listing removes the platform from the answer.
    """
    try:
        if not p.exists():
            return False
        # Collapse `..` textually with normpath, NOT Path.resolve(). resolve() asks the filesystem for
        # the canonical path, and on Windows that hands back the on-disk casing -- which would quietly
        # re-mask the exact mismatch this function exists to find. Links arrive unresolved
        # (`packages/meic/../../docs/README.md`), so without this the walk below compares a literal
        # ".." against a directory listing and reports every relative link as broken.
        cur = Path(os.path.normpath(p))
        while cur != ROOT and cur.parent != cur:
            if cur.name not in {c.name for c in cur.parent.iterdir()}:
                return False
            cur = cur.parent
    except OSError:
        return False
    return True


def _real_modules() -> set[str]:
    """Every importable `cherrypick.*` dotted name, derived from the filesystem.

    Deliberately not `importlib.util.find_spec`: the `docs` CI job is a bare checkout plus Python and
    installs no packages, so an import-based check would report every module missing there. Walking the
    tree also sidesteps per-package layout differences for free -- `packages/core` is flat while the
    other six are src-layout, and `cherrypick.cli` sits a level above `cherrypick.orchestrator.*`.
    """
    names: set[str] = set()
    roots = [ROOT / "packages/core/cherrypick"]
    pkgs = ROOT / "packages"
    if pkgs.is_dir():
        roots += [p / "src/cherrypick" for p in pkgs.iterdir() if (p / "src/cherrypick").is_dir()]
    for base in roots:
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            parts = list(f.relative_to(base.parent).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                names.add(".".join(parts))
    return names


def _check_commands(rel: str, lines: list[str], modules: set[str]) -> list[str]:
    """Verify that documented commands still point at something real.

    This closes the class of bug that survived every earlier sweep: a module path embedded in
    something that does not look like a path. Three commands launched
    `Start-Process python -ArgumentList 'src\\streamer.py'` for weeks after the namespace migration
    moved that file, because sweeps matched `src/<mod>.py` and not a backslashed bare filename inside
    a PowerShell argument list. Two deleted slash commands had the same shape.

    Two checks, both scoped to lines inside a fence that actually invoke something:

    * `-m cherrypick.…` must name a real module. This is the form the whole suite now uses, so it is
      the highest-value thing to keep honest.
    * A script path *containing a separator* must resolve against the doc's own directory, its package
      root, or the repo root -- whichever the reader would plausibly be sitting in. Bare filenames are
      excluded on purpose; `python run.py` is meaningful only next to the prose telling you where to
      run it, and would fire on every one of the 56 legitimate uses.
    """
    findings: list[str] = []
    parts = rel.split("/")
    pkg_root = ROOT / parts[0] / parts[1] if len(parts) > 2 and parts[0] == "packages" else ROOT
    bases = [(ROOT / rel).parent, pkg_root, ROOT]

    in_fence = False
    for n, line in enumerate(lines, 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence or not _CMD_LINE.search(line):
            continue

        for dotted in _DASH_M.findall(line):
            if dotted.endswith(".") or any(t in dotted for t in _PLACEHOLDER):
                continue  # `-m cherrypick.meic.<mod>` names a shape, not a module
            if dotted not in modules:
                findings.append(f"{rel}:{n}: `-m {dotted}` is not a real module")

        for token in _CMD_PATH.findall(line):
            if any(t in token for t in _PLACEHOLDER) or token.startswith(("http", "~")):
                continue
            if not any(_exists_exact(b / token) for b in bases):
                findings.append(
                    f"{rel}:{n}: command references {token!r}, which resolves from neither this "
                    "file's directory, its package root, nor the repo root"
                )
    return findings


def _check_trees(rel: str, lines: list[str]) -> list[str]:
    """Resolve every path drawn in a fenced ASCII file-layout tree, and report the ones that vanished.

    Scoped tightly on purpose, because a tree diagram is a loose format and over-reach here would be
    worse than no check at all:

    * Only fenced blocks whose root line ends in `/` are treated as file layouts. `reporting-and-
      dashboard.md` draws a *dataflow* with the same box characters (`report.run(session=day) ──►`);
      its root is an expression, not a directory, so it is skipped rather than nonsensically resolved.
    * Roots outside the repo (`~/.cherrypick/data/meic/`) are skipped -- runtime state, not the tree.
    * A row is only judged when its parent directory actually exists. `logs/` is gitignored and absent
      from a fresh checkout, so its children are unknowable, not wrong.
    * Existence is checked on disk, not against git. `.claude/settings.json` is gitignored but real,
      and a tree may legitimately draw it.
    """
    findings: list[str] = []
    in_fence = False
    stack: list[tuple[int, str]] = []  # (indent column, directory name)
    base: Path | None = None
    prefix = ""

    for n, line in enumerate(lines, 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            stack, base, prefix = [], None, ""
            continue
        if not in_fence:
            continue

        row = _TREE_ROW.match(line)
        if not row:
            # The root line: the last non-empty line before the first branch, ending in a slash.
            text = line.strip()
            if text.endswith("/") and not text.startswith(("~", "/")) and "://" not in text:
                root = text.rstrip("/")
                # The root line usually names the repo itself ("cherrypick/"), which is not a
                # directory *inside* the repo -- and need not match the checkout's folder name (this
                # one is still cloned as "cherrypick-next"). Treat it as a prefix only when it really
                # resolves to a directory; otherwise it denotes the repo root.
                base, prefix = ROOT, root if (ROOT / root).is_dir() else ""
                stack = []
            continue

        if base is None:  # a tree we chose not to interpret (e.g. the dataflow diagram)
            continue

        col, name = len(row.group("indent")), row.group("name")
        while stack and stack[-1][0] >= col:
            stack.pop()

        if any(tok in name for tok in _PLACEHOLDER):
            if name.endswith("/"):
                stack.append((col, name.rstrip("/")))
            continue

        parts = [prefix] if prefix else []
        parts += [d for _, d in stack] + [name.rstrip("/")]
        target = base.joinpath(*parts)

        if name.endswith("/"):
            stack.append((col, name.rstrip("/")))
        comment = line.split("#", 1)[1].lower() if "#" in line else ""
        if any(mark in comment for mark in _RUNTIME_MARKERS):
            continue
        if target.parent.exists() and not _exists_exact(target):
            findings.append(f"{rel}:{n}: file-layout tree lists {'/'.join(parts)!r}, which does not exist")
    return findings


def check(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    modules = _real_modules()
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel == SELF:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        if not _is_test(rel):
            for n, line in enumerate(lines, 1):
                if _is_example(line):
                    continue
                if _ABS_PATH.search(line):
                    findings.append(
                        f"{rel}:{n}: absolute machine path -- derive it from "
                        "Path(__file__), an env var, or config"
                    )
                if _HOME_POINTER.search(line):
                    findings.append(
                        f"{rel}:{n}: points outside the repo (~/.claude/) -- move it "
                        "in-repo, or mark it non-authoritative"
                    )
                if m := _ACCOUNT.search(line):
                    findings.append(
                        f"{rel}:{n}: possible unmasked account number {m.group(1)!r} -- mask to ****1234"
                    )

        if path.suffix.lower() in DOC_SUFFIXES:
            for n, line in enumerate(lines, 1):
                for target in _MD_LINK.findall(line):
                    t = target.split("#", 1)[0].strip()
                    if not t or t.startswith(("http://", "https://", "mailto:", "<")):
                        continue
                    if not _exists_exact(path.parent / t):
                        findings.append(f"{rel}:{n}: broken relative link -> {t}")
                if m := _MD_LINK_OPEN.search(line):
                    findings.append(
                        f"{rel}:{n}: unclosed markdown link -> '({m.group(1)}' is missing its ')' "
                        "(it renders as literal text, and the broken-link check cannot see it)"
                    )
            findings += _check_trees(rel, lines)
            findings += _check_commands(rel, lines, modules)
    return findings


def main() -> int:
    findings = check(tracked_files())
    if not findings:
        print("check_docs: OK")
        return 0
    for f in sorted(findings):
        print(f)
    print(f"\ncheck_docs: {len(findings)} finding(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
