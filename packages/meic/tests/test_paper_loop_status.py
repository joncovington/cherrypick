"""`paper_loop --status` — and specifically the settlement signal the orchestrator reads from it.

`watchdog._check_settlement` shells out to a module's own `--status` and looks for
`session_settled` / `positions_today`. When it cannot find them it says nothing, deliberately: it
cannot invent a signal. The cost of that is what this file guards. A module opted into the check
whose status lacks the fields is indistinguishable, on the watchdog's output, from one that settled
cleanly — so it alerts on nothing, forever, while reading as coverage.

That is how MEIC came to be the only paper module without a working settlement check (flies,
calendars, pmcc, curve and bwb all had one). It matters more since 2026-08-26, when
`paper.evaluate_open_trade` started REFUSING to settle a position with no underlying price rather
than booking it at zero intrinsic — the right failure, but a silent one, since the position simply
stays open.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import analytics, db, paper_loop  # noqa: E402

# The exact field names watchdog._check_settlement reads. Named here so a rename on either side
# fails with the reason rather than as a silent no-op in production.
WATCHDOG_CONTRACT = ("session_settled", "positions_today")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = str(tmp_path / "paper_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    monkeypatch.setattr(paper_loop, "_PAPER_DB", path)
    monkeypatch.setattr(paper_loop, "_DB", [sys.executable, "-m", "cherrypick.meic.db", "--db", path])
    monkeypatch.setattr(paper_loop, "_running_pid", lambda: None)
    monkeypatch.setattr(paper_loop, "_task_installed", lambda: False)
    return path


def _status(capsys) -> dict:
    paper_loop._cmd_status()
    return json.loads(capsys.readouterr().out)


def _open_trade(path, order_id, day, **over):
    row = {
        "trade_date": day,
        "symbol": "SPX",
        "status": "open",
        "risk_profile": "control",
        "era": analytics.CURRENT_ERA,
        "net_credit": 1.8,
        "wing_width": 10,
        "put_strike": 7450.0,
        "call_strike": 7550.0,
        "quantity": 1,
        "ic_order_id": order_id,
        "created_at": "x",
        "updated_at": "x",
    }
    row.update(over)
    conn = sqlite3.connect(path)
    conn.execute(
        f"INSERT INTO ic_trades ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    )
    conn.commit()
    conn.close()


def _today() -> str:
    return paper_loop._now_et().strftime("%Y-%m-%d")


def test_status_emits_the_fields_the_watchdog_reads(ledger, capsys):
    """The whole point. Without these two the settlement check is wired, opted in, and inert."""
    status = _status(capsys)
    for field in WATCHDOG_CONTRACT:
        assert field in status, f"watchdog._check_settlement reads {field!r} and would go silent"


def test_a_clean_book_reports_settled(ledger, capsys):
    status = _status(capsys)
    assert status["session_settled"] is True
    assert status["positions_today"] == 0


def test_an_unsettled_position_is_what_the_watchdog_would_warn_on(ledger, capsys):
    _open_trade(ledger, "IC-1", _today())
    _open_trade(ledger, "IC-2", _today())

    status = _status(capsys)

    assert status["session_settled"] is False
    assert status["positions_today"] == 2
    # The exact condition in watchdog._check_settlement.
    assert status["session_settled"] is False and (status["positions_today"] or 0) > 0


def test_yesterdays_open_position_is_not_todays_problem(ledger, capsys):
    """`get_open_trades` scopes to today, and the check is about whether THIS session settled. A
    stale row from an earlier day would otherwise raise a warning every evening forever."""
    _open_trade(ledger, "IC-OLD", "2020-01-02")
    status = _status(capsys)
    assert status["session_settled"] is True
    assert status["positions_today"] == 0


def test_an_unmarkable_position_explains_itself(ledger, capsys):
    """`data_reason` is optional and the watchdog supplies its own wording without it. Sent only
    when the ledger actually says something — a guess reads as a diagnosis."""
    _open_trade(ledger, "IC-1", _today(), unmarked_iterations=4)
    assert "could not be marked" in _status(capsys)["data_reason"]


def test_no_data_reason_is_invented_for_an_ordinary_open_position(ledger, capsys):
    _open_trade(ledger, "IC-1", _today())
    assert "data_reason" not in _status(capsys)


def test_the_existing_status_fields_are_unchanged(ledger, capsys):
    """Additive only: `--status` is the orchestrator's configured `status_argv` for this module and
    predates the settlement fields."""
    status = _status(capsys)
    assert set(status) >= {"daemon_running", "pid", "scheduled_task", "open_positions"}
    assert status["open_positions"] == status["positions_today"]
