"""Tests for tools/check_personal.py -- the gate that keeps the owner's account facts out of a public repo.

Every rule is shown firing on the shape that was actually scrubbed from the tree (so a regression
that stops it matching is caught) and shown staying quiet on the generic wording that replaced it
(so the gate does not cry wolf on descriptions of what a tool does). This file is exempt from the
phrase rules by path, because it holds the phrases on purpose.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_personal as cp  # noqa: E402


def rules(line: str, rel: str = "docs/x.md") -> list[str]:
    return [f.split(": ")[1] for f in cp.check_line(rel, 1, line)]


@pytest.mark.parametrize(
    "line,rule",
    [
        ("Put credit spreads on the funds the user trades: metals", "owner-trades"),
        ("*Source: the user's own trade (2026-10-19).", "owner-trades"),
        ("The user's own six fills each sit inside the bar", "owner-trades"),
        ("the user holds it in the real account", "owner-trades"),
        ("checks the user's account against the broker", "owner-account"),
        ("On 10-20 the account holds 16 names and none a covered call", "account-holds-count"),
        ("The real account is an IRA, so only cash-secured puts arise", "real-account-type"),
        ("(net -6.96, again matching the account)", "matching-the-account"),
        ("the entry order 479793516 filled at 0.43", "order-id"),
        ('"entry_order_id": "489436397",', "order-id"),
        ("Put credit spreads, order 479793516.", "order-id"),
        ("see plan.md#16-put-credit-spreads-on-the-funds-the-user-trades", "owner-trades"),
        ('"account_number": "5WT99991"', "account-number"),
        ("account number 123456789012 was shown", "account-number"),
    ],
)
def test_the_scrubbed_shapes_fire(line, rule):
    assert rule in rules(line)


@pytest.mark.parametrize(
    "line",
    [
        "manage reads the real account and recommends an action",
        "the dry run preflights every order against the real account",
        "the account holds no uncovered 100-share lot, so no call fits",
        "On an IRA or cash account only defined risk is allowed",
        "a handful of hand-placed fills on a leveraged fund",
        '"entry_order_id": "900000001",',
        "order 999999999 is a pid sentinel",
        "account ****0000 is masked",
        "The user's own switch in the suite config.",
        "the user decides whether to arm it",
        "a 9-digit figure 123456789 with nothing near it",
    ],
)
def test_generic_wording_stays_quiet(line):
    assert rules(line) == []


def test_the_marker_allows_a_line_but_needs_a_reason():
    assert rules("the funds the user trades  <!-- personal-ok: quoting the rule this tool enforces -->") == []
    assert rules("the funds the user trades personal-ok:") == ["marker"]


def test_a_path_allowance_silences_only_its_rule():
    assert rules('"account_number": "5WT99991"', "packages/core/cherrypick/core/redact.py") == []
    assert "order-id" in rules("order 489436397", "packages/core/cherrypick/core/redact.py")
    assert rules('"account_number": "5WT99991"', "packages/x/tests/test_a.py") == []


def test_the_tracked_tree_is_clean():
    """The gate as CI runs it. Shown to fail by test_the_scrubbed_shapes_fire; this is the pass side."""
    assert cp.scan() == []


def test_the_command_exits_1_on_a_finding_and_0_when_clean(tmp_path):
    exe = [sys.executable, str(Path(cp.__file__))]
    clean = subprocess.run(exe, capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout
    probe = cp.ROOT / "docs" / "zz_personal_probe.md"
    probe.write_text("the funds the user trades\n", encoding="utf-8")
    try:
        subprocess.run(["git", "add", "-N", str(probe)], cwd=cp.ROOT, check=True)
        bad = subprocess.run(exe, capture_output=True, text=True)
        assert bad.returncode == 1 and "docs/zz_personal_probe.md:1: owner-trades" in bad.stdout
    finally:
        subprocess.run(["git", "rm", "-q", "--cached", "-f", str(probe)], cwd=cp.ROOT)
        probe.unlink(missing_ok=True)
