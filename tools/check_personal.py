#!/usr/bin/env python3
"""Fail when tracked text ties a ticker, fill, order or account fact to the owner's real account.

The repo is public. Nothing in it may say what the owner trades or holds, quote a real broker order
id, or show an unmasked account number. Generic tickers in a module's roster, a study or an example
are fine; "the funds the user trades", "the real account is an IRA", "matching the account" and a
nine-digit order number are not. Worked examples use made-up tickers (AAA, BBB), invented figures
and the account `****0000`.

Nor may anything name the owner's machine: no absolute home directory with a real user name in it
(`C:\\Users\\<name>`, `/home/<name>`, `/Users/<name>`, `/c/Users/<name>`), no path mangled from one (a
Windows path through Git Bash loses its backslashes and leaves a folder named `Users<name>AppData...`),
and no local user name. These rules read every tracked PATH, binaries included, as well as text. The
user name is never written here: it is read at run time from this machine (the home folder's name and
the login) and from `CHERRYPICK_PERSONAL_NAMES` (comma-separated), so CI, which knows neither, runs
the path-shape rules only and the pre-commit hook (`tools/hooks/pre-commit`) runs the name rule.
Placeholder users (`u`, `x`, `me`, `you`, `name`, `example`, ...) are fine.

Scope is `git ls-files`, or with `--staged` the files staged for commit, read from the index (the
pre-commit hook). Exit 0 when clean, 1 with one `file:line: rule: text` per finding (line 0 is the
path itself). The same gate runs in CI next to `tools/check_docs.py`.

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
import functools
import getpass
import os
import re
import subprocess
import sys
from collections.abc import Callable
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

# An absolute home directory: `C:\Users\<u>` (any run of slashes or backslashes, so an escaped
# `C:\\Users\\<u>` too), `/c/Users/<u>` (Git Bash), `/Users/<u>` (macOS), `/home/<u>`. The slash
# before `users`/`home` may not follow a word character or a drive's colon, so a URL's
# `/api/users/<id>` is not one, nor `C:\home\logs`. The user must hold a letter (`C:\Users\...` is a
# placeholder).
_HOME = re.compile(
    r"(?:\b[a-z]:[\\/]+users|(?<![\w.:])[\\/](?:[a-z][\\/])?(?:users|home))"
    r"[\\/]+([a-z0-9._-]*[a-z][a-z0-9._-]*)",
    re.I,
)
# The same path with its separators lost: `C:\Users\<u>\AppData\...` read by Git Bash as
# `C:Users<u>AppData...`, which is what put a pytest folder at the root on 2026-11-17.
_MANGLED = re.compile(r"users[a-z0-9._-]{1,40}?appdata", re.I)
#: Users that stand for anyone: a home path with one of these (or one or two characters) is generic.
PLACEHOLDER_USERS = {
    "me", "you", "user", "username", "name", "yourname", "example", "someone", "runner", "public",
    "default", "shared", "admin", "dev", "alice", "bob",
}  # fmt: skip
_GENERIC_LOGINS = PLACEHOLDER_USERS | {"root", "runneradmin", "vscode", "codespace", "ubuntu", "node"}
#: Large binaries are checked by path only.
_MAX_BINARY = 5 * 1024 * 1024


@functools.cache
def owner_names() -> tuple[str, ...]:
    """This machine's user names: the home folder's name, the login and `CHERRYPICK_PERSONAL_NAMES`.

    Read at run time and never written down, so the repo holds no name to protect the name. A generic
    login (CI's `runner`, `root`) or one shorter than three characters is not a name.
    """
    found = [Path.home().name]
    try:
        found.append(getpass.getuser())
    except Exception:  # noqa: BLE001 - no login is not an error
        pass
    found += os.environ.get("CHERRYPICK_PERSONAL_NAMES", "").split(",")
    names = {n.strip().lower() for n in found}
    return tuple(sorted(n for n in names if len(n) >= 3 and n not in _GENERIC_LOGINS))


def _home_hits(text: str) -> bool:
    for m in _HOME.finditer(text):
        user = m.group(1).lower()
        if len(user) > 2 and user not in PLACEHOLDER_USERS:
            return True
    return False


def _name_hit(text: str, names: tuple[str, ...], *, word: bool) -> bool:
    low = text.lower()
    for n in names:
        if word:
            if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", low):
                return True
        elif n in low:
            return True
    return False


def check_path(rel: str, names: tuple[str, ...] | None = None) -> list[str]:
    """Findings for one tracked path itself (any file type). No allowance silences these."""
    names = owner_names() if names is None else names
    found: list[str] = []
    if _home_hits(rel) or _MANGLED.search(rel):
        found.append(f"{rel}:0: home-path: the path names a home directory")
    if _name_hit(rel, names, word=False):
        found.append(f"{rel}:0: owner-name: the path holds this machine's user name")
    return found


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [f for f in out if f]


def _staged() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    return [f for f in out if f]


def _read_worktree(rel: str) -> bytes | None:
    try:
        p = ROOT / rel
        if p.stat().st_size > _MAX_BINARY and p.suffix.lower() not in TEXT_SUFFIXES:
            return None
        return p.read_bytes()
    except OSError:
        return None


def _read_index(rel: str) -> bytes | None:
    r = subprocess.run(["git", "show", f":{rel}"], cwd=ROOT, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _allowed(rel: str, rule: str) -> bool:
    for glob, rules in PATH_ALLOW.items():
        if fnmatch.fnmatch(rel, glob) and ("*" in rules or rule in rules):
            return True
    return False


def check_line(rel: str, n: int, line: str, names: tuple[str, ...] | None = None) -> list[str]:
    """Findings for one line (`file:line: rule: text`). The marker silences a line, with a reason,
    except for this machine's user name, which nothing silences (and whose text is not echoed)."""
    names = owner_names() if names is None else names
    found: list[str] = []
    if _name_hit(line, names, word=True):
        found.append(f"{rel}:{n}: owner-name: the line holds this machine's user name")
    if MARKER in line:
        reason = line.split(MARKER, 1)[1].strip(" \t-*/>`")
        if not reason:
            return [*found, f"{rel}:{n}: marker: `{MARKER}` needs a reason after it"]
        return found

    def add(rule: str) -> None:
        if not _allowed(rel, rule):
            found.append(f"{rel}:{n}: {rule}: {line.strip()[:140]}")

    # Not echoed: CI's log is public, and the line is the home directory it must not show.
    if (_home_hits(line) or _MANGLED.search(line)) and not _allowed(rel, "home-path"):
        found.append(f"{rel}:{n}: home-path: the line names a home directory")

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


def _check_binary(rel: str, data: bytes, names: tuple[str, ...]) -> list[str]:
    """A binary's bytes (an image's metadata, a database) checked for a home path or the user name."""
    if len(data) > _MAX_BINARY:
        return []
    text = data.decode("latin-1")
    found: list[str] = []
    if (_home_hits(text) or _MANGLED.search(text)) and not _allowed(rel, "home-path"):
        found.append(f"{rel}:0: home-path: the file's bytes hold a home directory")
    if _name_hit(text, names, word=True):
        found.append(f"{rel}:0: owner-name: the file's bytes hold this machine's user name")
    return found


def scan(
    files: list[str] | None = None,
    read: Callable[[str], bytes | None] = _read_worktree,
    names: tuple[str, ...] | None = None,
) -> list[str]:
    names = owner_names() if names is None else names
    findings: list[str] = []
    for rel in files if files is not None else _tracked():
        findings.extend(check_path(rel, names))
        p = Path(rel)
        if p.name in SKIP_NAMES or "node_modules/" in rel or rel.startswith("graphify-out/"):
            continue
        data = read(rel)
        if data is None:
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            findings.extend(_check_binary(rel, data, names))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.extend(_check_binary(rel, data, names))
            continue
        for n, line in enumerate(text.split("\n"), 1):
            findings.extend(check_line(rel, n, line, names))
    return findings


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    findings = scan(_staged(), _read_index) if "--staged" in argv else scan()
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
