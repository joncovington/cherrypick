"""The read-side CLI — meic was the last module in the suite without one.

What is tested here is the contract that makes the CLI worth having, not argparse: every verb emits
one JSON object on stdout with `ok`, it opens the ledger READ-ONLY, and it never grows a verb that
runs or writes. The last of those is the one that matters most — `paper_loop` shells out to
`cherrypick.meic.db` and `...meic.tt` on every tick, so a "tidy-up" that folds those in repoints the
live loop.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import analytics, cli, db  # noqa: E402


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = str(tmp_path / "paper_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    conn = sqlite3.connect(path)
    row = {
        "trade_date": "2026-08-25", "symbol": "SPX", "status": "expired",
        "risk_profile": "control", "era": analytics.CURRENT_ERA,
        "put_credit": 0.9, "call_credit": 0.9, "net_credit": 1.8, "wing_width": 10,
        "put_strike": 7450.0, "call_strike": 7550.0, "pnl": 100.0, "fees": 6.89,
        "quantity": 1, "ic_order_id": "IC-1", "exit_reason": "expired_settlement",
        "settle_underlying": 7500.0, "created_at": "x", "updated_at": "x",
    }
    conn.execute(
        f"INSERT INTO ic_trades ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    )
    conn.commit()
    conn.close()
    return path


def _run(capsys, *argv) -> dict:
    assert cli.main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- the verbs


@pytest.mark.parametrize(
    "argv",
    [
        ("headline",),
        ("arms",),
        ("coverage",),
        ("exits",),
        ("stops",),
        ("stops", "--sessions"),
        ("regime", "gex"),
        ("gate-blocks", "--date", "2026-08-25"),
        ("settlement-audit",),
        ("gex-gate",),
    ],
    ids=lambda a: "-".join(a),
)
def test_every_verb_emits_one_ok_json_object(ledger, capsys, argv):
    payload = _run(capsys, "--db", ledger, *argv)
    assert payload["ok"] is True


def test_headline_matches_the_analytics_function_it_wraps(ledger, capsys):
    """The CLI must not become a second place where the headline is shaped: the console's mirror
    test compares a page against this output, and a CLI that aggregated separately would make that
    check compare two of its own opinions."""
    payload = _run(capsys, "--db", ledger, "headline")

    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    direct = analytics.headline(conn)
    conn.close()

    assert payload["headline"] == json.loads(json.dumps(direct, default=str))


def test_headline_reports_the_era_it_was_taken_over(ledger, capsys):
    """A number without its window is not a reading. `--era ALL` is an explicit cross-era read."""
    assert _run(capsys, "--db", ledger, "headline")["headline"]["era"] == analytics.CURRENT_ERA
    assert _run(capsys, "--db", ledger, "headline", "--era", "ALL")["headline"]["era"] == "ALL"


def test_the_audits_default_to_every_era(ledger, capsys):
    """Both audit verbs ask whether the LEDGER is sound, not how the current era performed. Scoping
    them to CURRENT_ERA would hide exactly the historical rows an audit exists to find — the 90
    unpriced settlements are all pre-2026-08-04."""
    assert _run(capsys, "--db", ledger, "settlement-audit")["audit"]["era"] == "ALL"
    assert _run(capsys, "--db", ledger, "gex-gate")["counterfactual"]["era"] == "ALL"


# --------------------------------------------------------------------------- the boundary


def test_the_cli_cannot_write_the_ledger(ledger, capsys):
    """A read surface over a trading ledger must be unable to write it, not merely choose not to."""
    with cli._connect(ledger) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM ic_trades")


def test_the_connection_is_closed_not_just_committed(ledger):
    """`with sqlite3.connect(...)` commits and leaves the handle OPEN, which is not what the `with`
    reads as and would hold the WAL for the life of the process."""
    with cli._connect(ledger) as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_the_cli_exposes_no_verb_that_runs_or_writes():
    """The boundary, pinned. `paper_loop` shells out to `cherrypick.meic.db` and `...meic.tt` on
    every tick, and the orchestrator's jobspec, onboarding and the suite's skills all name those
    module paths; folding them behind this CLI would repoint the live loop to buy a reader nothing.
    Any verb added here must be a read."""
    parser = cli.build_parser()
    actions = [a for a in parser._subparsers._group_actions[0].choices]  # noqa: SLF001
    forbidden = {"once", "start", "stop", "run", "settle", "save", "install-task", "trade", "enact"}
    assert forbidden.isdisjoint(actions), f"the read-side CLI grew a verb that acts: {actions}"


def test_every_verb_is_listed_in_the_module_docstring():
    """The docstring is the only place a reader finds out what this CLI does — there is no `--help`
    in the docs. Driven off the parser so a verb added without a line here fails.

    Matched as the first token of a listed line, not as a substring: a plain `in` check passes for
    any verb that happens to be a prefix of a documented one, and `settle` inside `settlement-audit`
    is exactly that case — it slipped through the first version of this test."""
    listed = {
        line.split()[0]
        for line in cli.__doc__.splitlines()
        if line.startswith("    ") and line.strip() and not line.strip().startswith(("*", "-"))
    }
    parser = cli.build_parser()
    for verb in parser._subparsers._group_actions[0].choices:  # noqa: SLF001
        assert verb in listed, f"{verb!r} is not listed in cli.py's docstring (found: {sorted(listed)})"
