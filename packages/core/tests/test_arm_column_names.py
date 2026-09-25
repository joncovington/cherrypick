"""No code may name meic's pre-rename arm column.

The 2026-09-23 `book` rename was sound and still broke two things afterwards, both for the same
reason: a reader OUTSIDE the migrated packages still asked for the old column, and nobody had
listed it. The notifier re-sent 7,726 notifications for 553 events; four console readers turned
"no such column" into an empty page. Grepping for `book` could never have found them all -- it is a
common word. `risk_profile` is not, so for meic the inventory can be the repository itself.

Outside meic, a reader resolves the column per file (`core.db.arm_column`, the console's
`armColumnOf`); inside it, the column is `arm` (renamed 2026-09-24). So the old name has no business
in any code. It may appear in exactly two ways:

  - inside backticks, in prose that names the column (a docstring, a comment);
  - in the declared alias lists below, which are where the spellings are defined.

Anything else -- a SQL token, a quoted string, a dict key -- fails here, naming the line.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Where the code lives: every package's source, the console's workspaces, and the scripts.
ROOTS = [
    *(REPO / "packages").glob("*/src"),
    REPO / "packages" / "core" / "cherrypick",
    *(REPO / "packages" / "console").glob("*/src"),
    REPO / "scripts",
]
SUFFIXES = {".py", ".ts", ".tsx"}

# path (repo-relative, forward slashes) -> why the old name may stand bare there.
ALLOWED = {
    "packages/core/cherrypick/core/db/__init__.py": "ARM_COLUMNS, the Python alias list",
    "packages/console/server/src/readers/db.ts": "ARM_COLUMNS, the TypeScript alias list",
    "scripts/arm_column_preflight.py": "OLD_NAMES: the preflight looks for exactly these",
    "scripts/retag_advised_books.py": "one-off, ran 2026-09-17 against the pre-rename ledgers",
    "packages/meic/src/cherrypick/meic/db.py": "_refuse_pre_rename names the old column to refuse it",
}

WORD = re.compile(r"\brisk_profile\b")
BACKTICKED = re.compile(r"`[^`\n]*`")


def _offending_lines():
    for root in ROOTS:
        for path in sorted(root.rglob("*")):
            if path.suffix not in SUFFIXES or "node_modules" in path.parts or "dist" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            if rel in ALLOWED:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if WORD.search(BACKTICKED.sub("", line)):
                    yield f"{rel}:{n}: {line.strip()}"


def test_no_code_names_meics_pre_rename_arm_column():
    hits = list(_offending_lines())
    assert not hits, (
        "code names `risk_profile` directly -- resolve the column per file instead "
        "(core.db.arm_column / armColumnOf):\n" + "\n".join(hits)
    )


def test_the_scan_covers_the_readers_that_broke_last_time():
    # A scan whose roots silently stopped matching would pass forever. These are the readers the
    # `book` rename missed, plus meic's own; each must be inside what is scanned.
    scanned = {p.relative_to(REPO).as_posix() for r in ROOTS for p in r.rglob("*") if p.suffix in SUFFIXES}
    for must in (
        "packages/orchestrator/src/cherrypick/orchestrator/trade_notifier.py",
        "packages/console/server/src/readers/decisions.ts",
        "packages/console/server/src/readers/meic.ts",
        "packages/core/cherrypick/core/ledgers.py",
        "packages/advisor/src/cherrypick/advisor/factpack.py",
        "packages/meic/src/cherrypick/meic/paper.py",
    ):
        assert must in scanned, f"{must} is outside the scan"
    for allowed in ALLOWED:
        assert (REPO / allowed).exists(), f"allow-listed {allowed} no longer exists -- drop it"


# --------------------------------------------------------------------------- earnings' `profile`
# `profile` is a common word -- a config preset registry, the gamma-by-strike curve, prose -- so it
# cannot be banned the way `risk_profile` is. What CAN be pinned is where it would do damage: a
# file that queries an earnings table naming the column there, as SQL or as a row key. The files
# are discovered from the SQL itself, so a new reader of an earnings ledger is covered the day it
# appears; within them, only SQL-shaped and row-key uses fail, and prose/comments are ignored.
EARNINGS_SQL = re.compile(r"(FROM|JOIN|INTO)\s+(trades|scan_log|entry_reviews|management_events)\b")
PROFILE_AS_COLUMN = re.compile(
    r"(\bprofile\s*(,|\bFROM\b|\bAS\b|=|\bIN\b|\bLIKE\b|\bIS\b|\))|"
    r"(\bSELECT|\bBY|,|\.)\s*profile\b|"
    r"\[['\"]profile['\"]\]|get\(['\"]profile['\"]\))"
)
COMMENT = re.compile(r"^\s*(#|//|\*|/\*)")
# Files that query earnings tables and may name `profile` bare, with why.
PROFILE_ALLOWED = {
    "scripts/retag_advised_books.py": "one-off, ran 2026-09-17 against the pre-rename ledgers",
}


def _earnings_readers() -> list[Path]:
    out = []
    for root in ROOTS:
        for path in sorted(root.rglob("*")):
            if path.suffix not in SUFFIXES or "node_modules" in path.parts or "dist" in path.parts:
                continue
            if "earnings" in path.relative_to(REPO).parts[:2]:
                # earnings itself says `profile` for presets and filter arguments throughout; its
                # column is pinned by its own schema fixture and refusal tests instead.
                continue
            if EARNINGS_SQL.search(path.read_text(encoding="utf-8", errors="replace")):
                out.append(path)
    return out


def test_no_reader_of_an_earnings_ledger_names_its_pre_rename_arm_column():
    hits = []
    for path in _earnings_readers():
        rel = path.relative_to(REPO).as_posix()
        if rel in PROFILE_ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if COMMENT.match(line):
                continue
            if PROFILE_AS_COLUMN.search(BACKTICKED.sub("", line)):
                hits.append(f"{rel}:{n}: {line.strip()}")
    assert not hits, (
        "a reader of an earnings ledger names `profile` as a column -- resolve it per file instead "
        "(core.db.arm_column / armColumnOf):\n" + "\n".join(hits)
    )


def test_the_earnings_scan_finds_the_readers_it_is_meant_to_cover():
    found = {p.relative_to(REPO).as_posix() for p in _earnings_readers()}
    for must in (
        "packages/core/cherrypick/core/ledgers.py",
        "packages/orchestrator/src/cherrypick/orchestrator/reconcile.py",
        "packages/orchestrator/src/cherrypick/orchestrator/trade_notifier.py",
        "packages/advisor/src/cherrypick/advisor/factpack.py",
        "packages/console/server/src/readers/earnings.ts",
    ):
        assert must in found, f"{must} no longer discovered as an earnings reader -- the scan went blind"
