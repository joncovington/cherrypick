#!/usr/bin/env python3
"""Fail when tracked text ties a ticker, fill, order or account fact to the owner's real account.

The repo is public. Nothing in it may say what the owner trades or holds, quote a real broker order
id, or show an unmasked account number. Generic tickers in a module's roster, a study or an example
are fine; "the funds the user trades", "the real account is an IRA", "matching the account" and a
nine-digit order number are not. Worked examples use made-up tickers (AAA, BBB), invented figures
and the account `****0000`.

Scope is `git ls-files`, text files only. Exit 0 when clean, 1 with one `file:line: rule: text` per
finding. The same gate runs in CI next to `tools/check_docs.py`.

Allowing a line that is legitimate:

  * Per line: put `personal-ok: <reason>` anywhere on the line (in a comment, or in prose inside an
    HTML comment). A bare marker with no reason is itself reported.
  * Per path: add the path (glob) and the rule names to `PATH_ALLOW` below, with the reason beside it.
    Keep it short; a path entry silences a whole file for that rule.

Descriptions of what a tool does ("manage reads the real account", "the dry run preflights against
the real account") are not findings and are not matched: the rules look for the owner's facts, not
for the words "real account".
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
    ".cmd",
    ".ps1",
    ".sh",
    ".html",
    ".css",
}
SKIP_NAMES = {"pnpm-lock.yaml", "package-lock.json"}
MARKER = "personal-ok:"

#: path glob -> rules it may break, and why. The checker and its own tests hold the very phrases they
#: look for, so they are exempt from the phrase rules (never from the marker rule).
PATH_ALLOW: dict[str, set[str]] = {
    "tools/check_personal.py": {"*"},
    "tools/tests/test_check_personal.py": {"*"},
    # The redaction helper documents the account-number shape it masks, with an invented sample.
    "packages/core/cherrypick/core/redact.py": {"account-number"},
    # Fixtures that stand in for a broker's account-number format; they are invented.
    "*/tests/*": {"account-number"},
    "*/test/*": {"account-number"},
}

# Order ids that are plainly invented are not findings: 9000000NN, or one digit repeated.
_FAKE_ID = re.compile(r"^(?:9000000\d\d|(\d)\1{8})$")

PHRASES: list[tuple[str, re.Pattern[str]]] = [
    (
        "owner-trades",
        re.compile(
            r"\bthe user'?s own (?:\w+ )?(?:trades?|traded|fills?|accounts?|positions?|orders?|holdings?|"
            r"book|names?|credit spreads?|screenshots? of)\b|\bthe user (?:trades|traded|holds|owns)\b",
            re.I,
        ),
    ),
    (
        "owner-account",
        re.compile(r"\buser'?s (?:real |own |live )?(?:account|positions?|fills?|holdings?)\b", re.I),
    ),
    ("account-holds-count", re.compile(r"\bthe account (?:holds|held) \d", re.I)),
    (
        "real-account-type",
        re.compile(r"\b(?:real|live|actual) account is (?:an? )?(?:IRA|margin|cash|individual)\b", re.I),
    ),
    ("matching-the-account", re.compile(r"\bmatch(?:es|ing) the account\b", re.I)),
]

_ORDER_NEAR = re.compile(r"order", re.I)
_NINE = re.compile(r"(?<![\w.$-])(\d{9})(?!\w)(?!\.\d)")
# A tastytrade-style account number (5WT12345): digits and capitals together, not a masked ****1234.
_ACCT = re.compile(r"(?<![\w*])5[A-Z]{2}[0-9]{5,8}(?![\w])")
_ACCT_NUM = re.compile(r"account[ _-]?(?:number|no\.?)\W{0,12}(?<![*\d])(\d{8,})(?!\d)", re.I)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [f for f in out if f]


def _allowed(rel: str, rule: str) -> bool:
    for glob, rules in PATH_ALLOW.items():
        if fnmatch.fnmatch(rel, glob) and ("*" in rules or rule in rules):
            return True
    return False


def check_line(rel: str, n: int, line: str) -> list[str]:
    """Findings for one line (`file:line: rule: text`). The marker silences a line, with a reason."""
    if MARKER in line:
        reason = line.split(MARKER, 1)[1].strip(" \t-*/>`")
        if not reason:
            return [f"{rel}:{n}: marker: `{MARKER}` needs a reason after it"]
        return []
    found: list[str] = []

    def add(rule: str) -> None:
        if not _allowed(rel, rule):
            found.append(f"{rel}:{n}: {rule}: {line.strip()[:140]}")

    # A heading's anchor slug carries the same words joined by hyphens
    # (`#16-put-credit-spreads-on-the-funds-the-user-trades`), so the phrases are also tried with
    # each hyphen read as a space.
    spaced = line.replace("-", " ")
    for rule, pat in PHRASES:
        if pat.search(line) or pat.search(spaced):
            add(rule)
    for m in _NINE.finditer(line):
        if _FAKE_ID.match(m.group(1)):
            continue
        window = line[max(0, m.start() - 20) : m.end() + 20]
        if _ORDER_NEAR.search(window):
            add("order-id")
            break
    if _ACCT.search(line) or _ACCT_NUM.search(line):
        add("account-number")
    return found


def scan(files: list[str] | None = None) -> list[str]:
    findings: list[str] = []
    for rel in files if files is not None else _tracked():
        p = Path(rel)
        if p.name in SKIP_NAMES or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if "node_modules/" in rel or rel.startswith("graphify-out/"):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.split("\n"), 1):
            findings.extend(check_line(rel, n, line))
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print("\n".join(findings))
        print(
            f"\n{len(findings)} personal-data finding(s). Rewrite generically (made-up tickers AAA/BBB, "
            f"account ****0000), or mark a legitimate line with `{MARKER} <reason>`."
        )
        return 1
    print("check_personal: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
