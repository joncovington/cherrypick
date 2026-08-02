#!/usr/bin/env python3
"""Enforce the suite's documentation guardrails, instead of auditing for them by hand.

This suite already prefers executable invariants to prose -- orchestrator's `test_headless.py` is a
source scan, and `test_schema_registry.py` enforces schema coverage "not prose". This is the same
idea for the guardrails that live in every CLAUDE.md:

  1. No machine-specific absolute paths in tracked files (the "portable paths only" guardrail).
  2. No relative links that don't resolve (a doc pointing at a file that isn't there).
  3. No unmasked account numbers (the "mask to ****1234" guardrail).
  4. No out-of-repo pointers presented as authoritative (e.g. `~/.claude/plans/...`), which is how
     78KB of design ended up living on exactly one machine, untracked.

Run: python tools/check_docs.py
Exits non-zero on any finding, printing `path:line: message` so it reads like a linter.

Scope is `git ls-files` -- tracked files only. Untracked scratch and gitignored local tooling are
deliberately out of scope.
"""

from __future__ import annotations

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


def check(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
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
                    if not (path.parent / t).exists():
                        findings.append(f"{rel}:{n}: broken relative link -> {t}")
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
