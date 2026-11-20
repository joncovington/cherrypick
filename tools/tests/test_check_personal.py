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


# --- the owner's machine: home paths and the local user name -----------------------------------
# `zqowner` stands in for a real user name; the real one is read from the machine and never written.
NAME = ("zqowner",)


@pytest.mark.parametrize(
    "line",
    [
        r"C:\Users\zqowner\.cherrypick\config.json",
        r'"C:\\Users\\zqowner\\AppData\\Local\\Temp"',
        "cd /c/Users/zqowner/Claude/cherrypick-next",
        "/home/zqowner/.cherrypick",
        "/Users/zqowner/Library",
        # the 2026-11-17 shape: a Windows --basetemp read by Git Bash, separators gone
        "UserszqownerAppDataLocalTempclaude1234scratchpadbt-b-8514",
        "C:UserszqownerAppDataLocalTemp",
    ],
)
def test_a_home_directory_fires(line):
    assert "home-path" in rules(line)
    # the finding never repeats the path: CI's log is public
    assert all("zqowner" not in f for f in cp.check_line("docs/x.md", 1, line, ()))


@pytest.mark.parametrize(
    "line",
    [
        r"C:\Users\u\.local\bin",
        r"C:\\Users\\x\\.cherrypick\\research",
        "/home/me/backup.sh",
        "/home/u/.venv/bin/python",
        r"Never hardcode absolute paths (`C:\Users\...`)",
        r'logdir=r"C:\home\logs\service"',
        "https://example.com/api/users/zqowner",
        "~/.cherrypick/data/ladder",
        r"C:\Users\<you>\.cherrypick",
    ],
)
def test_a_placeholder_home_stays_quiet(line):
    assert rules(line) == []


def test_the_user_name_fires_on_a_line_and_is_not_echoed_or_silenced():
    found = cp.check_line("docs/x.md", 3, "saved by zqowner  <!-- personal-ok: a reason -->", NAME)
    assert found == ["docs/x.md:3: owner-name: the line holds this machine's user name"]
    assert cp.check_line("docs/x.md", 1, "Zqowner wrote this", NAME) != []


def test_the_user_name_inside_a_longer_word_stays_quiet():
    # a GitHub handle that starts with the login is the repo's own address, not the machine
    assert cp.check_line("README.md", 1, "https://github.com/zqownerville/cherrypick", NAME) == []


def test_a_path_is_checked_whatever_the_file_type():
    stray = "UserszqownerAppDataLocalTempclaude1234scratchpadbt-b-8514/test_x0/p.db"
    assert [f.split(": ")[1] for f in cp.check_path(stray, ())] == ["home-path"]
    assert [f.split(": ")[1] for f in cp.check_path("docs/zqowner-notes.png", NAME)] == ["owner-name"]
    assert cp.check_path("packages/console/web/src/App.tsx", NAME) == []


def test_a_stray_path_fails_the_scan_even_as_a_binary():
    stray = "UserszqownerAppDataLocalTempscratchpadbt-1/p.db"
    found = cp.scan([stray], read=lambda rel: b"SQLite format 3\x00", names=())
    assert any(": home-path:" in f for f in found)


def test_a_binary_is_read_for_a_home_path_and_the_name():
    png = b"\x89PNG\r\n\x1a\n...tEXtSource\x00C:\\Users\\zqowner\\shot.png..."
    found = cp.scan(["docs/guide/shot.png"], read=lambda rel: png, names=NAME)
    assert {f.split(": ")[1] for f in found} == {"home-path", "owner-name"}
    assert cp.scan(["docs/guide/shot.png"], read=lambda rel: b"\x89PNG plain", names=NAME) == []


def test_the_names_come_from_the_machine_and_the_environment(monkeypatch):
    monkeypatch.setenv("CHERRYPICK_PERSONAL_NAMES", "ZqOther, ab ,runner")
    cp.owner_names.cache_clear()
    try:
        names = cp.owner_names()
        assert "zqother" in names  # lower-cased
        assert "ab" not in names and "runner" not in names  # too short; CI's generic login
    finally:
        cp.owner_names.cache_clear()


def test_staged_mode_reads_the_index_not_the_tree(monkeypatch, capsys):
    stray = "UserszqownerAppDataLocalTempscratchpadbt-1/p.db"
    monkeypatch.setattr(cp, "_staged", lambda: [stray])
    monkeypatch.setattr(cp, "_read_index", lambda rel: b"")
    assert cp.main(["--staged"]) == 1
    assert f"{stray}:0: home-path" in capsys.readouterr().out
    monkeypatch.setattr(cp, "_staged", lambda: [])
    assert cp.main(["--staged"]) == 0


def test_the_pre_commit_hook_runs_the_staged_check():
    hook = (cp.ROOT / "tools" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "tools/check_personal.py --staged" in hook
    mode = subprocess.run(
        ["git", "ls-files", "-s", "tools/hooks/pre-commit"], cwd=cp.ROOT, capture_output=True, text=True
    ).stdout
    assert mode.startswith("100755"), "the hook must be executable for Linux and macOS clones"


@pytest.mark.parametrize("script", ["scripts/dev-install.ps1", "scripts/dev-install.sh"])
def test_dev_install_points_git_at_the_hooks(script):
    assert "core.hooksPath tools/hooks" in (cp.ROOT / script).read_text(encoding="utf-8")
