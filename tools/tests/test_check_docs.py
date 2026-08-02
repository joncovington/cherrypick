r"""Tests for tools/check_docs.py -- the linter that enforces the suite's documentation guardrails.

The tool exists because this suite prefers executable invariants to prose. It had none of its own,
and that cost real money twice while it was being written:

  * `_exists_exact` did not exist, so a stale tree entry passed on Windows (case-insensitive) and
    failed in CI on Linux. The local run reported OK over a genuine error.
  * The first cut of that fix used `Path.resolve()`, which reports every relative link in the repo as
    broken, because links arrive holding `..` segments.

Neither was caught by the tool passing. Both were caught by cases like the ones below, so they live
in the repo now instead of in a scratch file.

Every case here is either a bug that actually shipped in this repo, or a legitimate shape the checks
must leave alone. That second half matters more than it looks: a linter that cries wolf on 56 valid
`python run.py` lines gets switched off, and then catches nothing at all.

This file sits under `tools/tests/` deliberately -- `check_docs._is_test()` exempts anything beneath a
`tests/` directory from the absolute-path and account-number rules, and the fixtures below are full of
synthetic violations that would otherwise trip the very tool they test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_docs as cd  # noqa: E402

ROOT = cd.ROOT
MEIC = "packages/meic/CLAUDE.md"
ORCH = "packages/orchestrator/CLAUDE.md"


def fenced(*body: str) -> list[str]:
    """Wrap lines in a code fence, which is the only place the tree/command rules look."""
    return ["```bash", *body, "```"]


# --------------------------------------------------------------------------- rule 5: layout trees
@pytest.mark.parametrize(
    "name,body,expect_finding",
    [
        (
            "a file that does not exist is reported",
            ["cherrypick/", "├── packages/meic/", "│   ├── src/", "│   │   └── ghost.py"],
            True,
        ),
        (
            "a tree matching reality is silent",
            ["cherrypick/", "├── packages/meic/", "│   ├── src/cherrypick/meic/", "│   │   └── tt.py"],
            False,
        ),
        (
            # reporting-and-dashboard.md draws a dataflow with the same box characters; its root is an
            # expression, not a directory, so it must not be resolved as one.
            "a dataflow diagram is not a file tree",
            ["report.run(session=day)  -- unified P&L", "        ├── eod_digest --> nowhere.md"],
            False,
        ),
        (
            "an entry annotated as runtime-created is believed",
            ["cherrypick/", "├── packages/meic/", "│   └── logs/   # Created at first run (gitignored)"],
            False,
        ),
        (
            "a <placeholder> names a shape, not a file",
            ["cherrypick/", "├── packages/meic/", "│   └── docs/", "│       └── eod-<date>.md"],
            False,
        ),
        (
            "a root outside the repo is runtime state",
            ["~/.cherrypick/data/meic/", "├── meic_trades.db"],
            False,
        ),
    ],
)
def test_tree_rule(name, body, expect_finding):
    findings = cd._check_trees("probe.md", fenced(*body))
    assert bool(findings) is expect_finding, f"{name}: {findings}"


def test_tree_rule_ignores_rows_outside_a_fence():
    assert cd._check_trees("probe.md", ["├── not-in-a-fence.py"]) == []


# --------------------------------------------------------------------------- rule 6: broken links
def _unclosed(line: str) -> bool:
    return cd._MD_LINK_OPEN.search(line) is not None


@pytest.mark.parametrize(
    "line,expect_finding",
    [
        ("see [paths](src/paths.py:", True),  # the real one, survived several doc passes
        ("see [paths](src/paths.py) here", False),
        ("a sentence (with parens) in it", False),
    ],
)
def test_unclosed_link_rule(line, expect_finding):
    assert _unclosed(line) is expect_finding


# --------------------------------------------------------------------------- rule 7: commands
@pytest.mark.parametrize(
    "name,rel,body,expect_finding",
    [
        # --- shapes that actually shipped broken ---
        (
            "PowerShell ArgumentList with a backslash path (3 real occurrences)",
            MEIC,
            [r"Start-Process python -ArgumentList 'src\streamer.py' -WindowStyle Hidden"],
            True,
        ),
        ("a flat forward-slash path", MEIC, ["python src/streamer.py --status"], True),
        ("a deleted module via -m", MEIC, ["python -m cherrypick.meic.ghost --once"], True),
        (
            "the quoted PowerShell -m form",
            MEIC,
            [r"Start-Process python -ArgumentList '-m','cherrypick.meic.ghost'"],
            True,
        ),
        # --- shapes that must be left alone ---
        ("the current -m form", MEIC, ["python -m cherrypick.meic.streamer --status"], False),
        (
            "the current PowerShell -m form",
            MEIC,
            [r"Start-Process python -ArgumentList '-m','cherrypick.meic.streamer'"],
            False,
        ),
        # bare `run.py` means "from packages/orchestrator" only next to the prose saying so; there are
        # 56 legitimate uses, and flagging them would make the rule unusable.
        ("a bare run.py", ORCH, ["python run.py doctor"], False),
        ("a repo-root-relative path", "docs/PROJECT.md", ["python packages/orchestrator/run.py"], False),
        ("a package-root-relative path", MEIC, ["python ../streamer/run.py --status"], False),
        ("a tests/ path under the package root", ORCH, ["python -m pytest tests/test_report.py"], False),
        ("a <mod> placeholder in a comment", MEIC, ["# run as -m cherrypick.meic.<mod>"], False),
        ("a non-command line inside a fence", MEIC, ["src/streamer.py   # sample output"], False),
        ("a tree row, which is not a command", MEIC, ["    |-- src/streamer.py"], False),
        ("a tools path from the repo root", "docs/README.md", ["python tools/check_docs.py"], False),
    ],
)
def test_command_rule(name, rel, body, expect_finding):
    findings = cd._check_commands(rel, fenced(*body), cd._real_modules())
    assert bool(findings) is expect_finding, f"{name}: {findings}"


def test_real_modules_resolves_both_package_layouts():
    """packages/core is flat; the other six are src-layout; cherrypick.cli sits above orchestrator.*"""
    mods = cd._real_modules()
    for present in (
        "cherrypick.meic.tt",
        "cherrypick.core.fees",
        "cherrypick.orchestrator.doctor",
        "cherrypick.cli",
    ):
        assert present in mods, present
    assert "cherrypick.meic.ghost" not in mods


# --------------------------------------------------------- rules 1/3: the quoted-example exemption
@pytest.mark.parametrize(
    "name,line,expect_exempt",
    [
        # The two real counter-examples in the repo. Both quote the bad path, so both stay exempt.
        ("guardrail text quoting the bad pattern", r"Never hardcode absolute paths (`C:\Users\...`)", True),
        ("ROADMAP guardrail, same shape", r"> **NEVER** hardcode (e.g. `C:\Users\...`, `/Users/...`)", True),
        # These passed under the old line-level heuristic purely because they contain "no" / "not ".
        ("a real leak containing 'no'", "no reason this ran from C:/Users/me/secret", False),
        ("a real leak containing 'not '", "this did not work: /home/me/key.pem", False),
    ],
)
def test_quoted_example_exemption(name, line, expect_exempt):
    matches = list(cd._ABS_PATH.finditer(line))
    assert matches, f"{name}: fixture no longer matches the rule it is testing"
    exempt = all(cd._is_quoted_example(line, m.start()) for m in matches)
    assert exempt is expect_exempt, name


# ------------------------------------------------------------------- case-sensitive path existence
def test_exists_exact_is_case_sensitive_on_every_platform():
    """The bug: a tree said `meic-start.md` while the file was `MEIC-start.md`. Windows said it
    existed, Linux CI said it did not, so the local run reported OK over a real error."""
    real = ROOT / "packages/meic/.claude/commands/meic-start.md"
    assert real.exists(), "fixture moved -- pick another lowercase-named command file"
    assert cd._exists_exact(real)
    assert not cd._exists_exact(real.with_name("MEIC-start.md"))


def test_exists_exact_resolves_relative_segments():
    """Links arrive unresolved. Collapsing them with Path.resolve() would re-canonicalize case on
    Windows and hide the mismatch above, so normpath does it textually instead."""
    assert cd._exists_exact(ROOT / "packages/meic/docs/../README.md")


# ------------------------------------------------------------------------------------ end to end
def test_the_repo_itself_is_clean():
    """The check every push runs. Kept last so a failure above points at the rule, not the repo."""
    assert cd.check(cd.tracked_files()) == []
