"""The fact set's guarantees. Each of these encodes a mistake this suite has actually made."""

import json
import sqlite3

import pytest

from cherrypick.core import ledgers as _ledgers
from cherrypick.review import facts, reconcile


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point review's own home at a tmp path."""
    monkeypatch.setattr(facts._paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(reconcile._paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(facts._paths, "facts_path", lambda s: tmp_path / f"eod-{s}.json")
    monkeypatch.setattr(reconcile._facts, "read", facts.read)
    return tmp_path


def _earnings_db(path, rows):
    """An earnings ledger. closed_at is epoch seconds, which is the whole point of the
    session-filter regression below."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (symbol TEXT, profile TEXT, strategy TEXT, pnl REAL, entry_cost REAL,"
        " exit_cost REAL, closed_at REAL, opened_at REAL, capital_at_risk REAL,"
        " entry_slippage REAL, exit_slippage REAL, entry_context TEXT)"
    )
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


# --------------------------------------------------------------------------- session scoping


def test_a_session_only_counts_trades_that_closed_in_it(tmp_path, monkeypatch):
    """The regression the reconciliation caught on its first run. The earnings reader deliberately
    does no SQL date pushdown -- closed_at is epoch seconds and the session is a LOCAL day, so a
    SQL bound would shift evening closes into the wrong session. Trusting the reader's bounds
    silently reported every trade the book had ever closed as though it settled today."""
    db = tmp_path / "paper_trades.db"
    _earnings_db(
        db,
        [
            ("CSCO", "p", "iron_fly", 100.0, 5.0, 5.0, _epoch("2026-08-12 15:00"), 0, 1000.0,
             None, None, None),
            ("AMAT", "p", "iron_fly", 999.0, 5.0, 5.0, _epoch("2026-08-05 15:00"), 0, 1000.0,
             None, None, None),
        ],
    )
    got = facts.build_module_facts("earnings", "2026-08-12", db_path=db)
    assert got["results"]["closed"] == 1
    assert got["results"]["gross"] == 100.0


# --------------------------------------------------------------------------- none is not zero


def test_unrecorded_capital_is_null_not_zero(tmp_path):
    """46 of the paper book's 64 trades predate the slippage columns. Averaging 'not recorded' as
    zero is what made the cost model look 90% cheaper than it is."""
    db = tmp_path / "paper_trades.db"
    _earnings_db(
        db, [("X", "p", "iron_fly", 10.0, 1.0, 1.0, _epoch("2026-08-12 15:00"), 0, None, None, None, None)]
    )
    got = facts.build_module_facts("earnings", "2026-08-12", db_path=db)
    assert got["return"]["capital_at_risk"] is None
    assert got["return"]["on_max_risk"] is None


def test_return_on_deployed_is_not_an_alias_for_return_on_risk(tmp_path):
    """For a defined-risk spread the broker's margin IS the max loss, so publishing one number
    under two headings would manufacture a distinction that does not exist."""
    db = tmp_path / "paper_trades.db"
    _earnings_db(
        db, [("X", "p", "iron_fly", 100.0, 0.0, 0.0, _epoch("2026-08-12 15:00"), 0, 1000.0, None, None, None)]
    )
    got = facts.build_module_facts("earnings", "2026-08-12", db_path=db)
    assert got["return"]["on_max_risk"] == pytest.approx(0.1)
    assert got["return"]["on_deployed"] is None


# --------------------------------------------------------------------------- sample honesty


def test_effective_sample_counts_events_not_rows():
    """673 MEIC trades on one session and one symbol are one market day, not 673 observations.
    Reading the raw count as the sample is how 64 earnings trades got read as 64 independent
    events when they are ~14."""
    records = [{"session": "2026-08-12", "symbol": "SPX"} for _ in range(673)]
    assert facts._sample(records) == {"n": 673, "effective_n": 1}


def test_effective_sample_separates_distinct_symbols_and_days():
    records = [
        {"session": "2026-08-12", "symbol": "CSCO"},
        {"session": "2026-08-12", "symbol": "CSCO"},
        {"session": "2026-08-12", "symbol": "AMAT"},
        {"session": "2026-08-11", "symbol": "CSCO"},
    ]
    assert facts._sample(records) == {"n": 4, "effective_n": 3}


# --------------------------------------------------------------------------- measurement breaks


def test_a_module_without_break_tracking_reports_none_not_empty(tmp_path):
    """None and [] are different claims: [] is a module that tracks breaks and has none, None is a
    module that does not track them -- and a trend line through the second is only as good as the
    assumption that nothing changed underneath it. flies has no such table."""
    db = tmp_path / "paper_trades.db"
    _earnings_db(db, [])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert facts._measurement_breaks(conn) is None
    conn.execute("CREATE TABLE measurement_breaks (break_date TEXT)")
    conn.commit()
    assert facts._measurement_breaks(conn) == []
    conn.close()


def test_breaks_are_deduped(tmp_path):
    """One date recorded under three keys is still one break."""
    db = tmp_path / "paper_trades.db"
    _earnings_db(db, [])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE measurement_breaks (break_date TEXT)")
    conn.executemany("INSERT INTO measurement_breaks VALUES (?)", [("2026-08-12",)] * 3)
    conn.commit()
    assert facts._measurement_breaks(conn) == ["2026-08-12"]
    conn.close()


# --------------------------------------------------------------------------- artifact


def test_a_missing_ledger_is_a_reason_not_a_crash(tmp_path):
    got = facts.build_module_facts("earnings", "2026-08-12", db_path=tmp_path / "nope.db")
    assert got["ok"] is False and got["reason"] == "ledger not found"


def test_the_fact_set_is_versioned_and_status_tagged(store, monkeypatch):
    monkeypatch.setattr(facts, "build_module_facts", lambda m, s, db_path=None: {"ok": False, "reason": "x"})
    built = facts.build("2026-08-12", status=facts.STATUS_FINAL)
    assert built["fact_version"] == facts.FACT_VERSION
    assert built["status"] == "final"
    # Driven off MODULES, not a literal: the literal version of this list went stale the day bwb
    # and curve landed, which is the same failure the fact set itself had.
    assert built["suite"]["modules_unreadable"] == list(facts.MODULES)


def test_writing_is_atomic_and_round_trips(store, monkeypatch):
    monkeypatch.setattr(facts, "build_module_facts", lambda m, s, db_path=None: {"ok": False, "reason": "x"})
    built = facts.build("2026-08-12")
    target = facts.write(built)
    assert json.loads(target.read_text(encoding="utf-8"))["session"] == "2026-08-12"
    assert not list(store.glob("*.tmp"))


# --------------------------------------------------------------------------- reconciliation


def test_reconcile_reports_a_mismatch_rather_than_just_failing(store, monkeypatch):
    """A discrepancy is a finding about the ledger, not merely a broken assertion -- so it carries
    both numbers and the delta."""
    facts.write(
        {
            "session": "2026-08-12",
            "modules": {"earnings": {"ok": True, "results": {"closed": 5, "gross": 100.0, "cost": 10.0}}},
        }
    )
    monkeypatch.setattr(
        reconcile, "_independent_totals", lambda m, s: {"closed": 5, "gross": 100.0, "cost": 4.0}
    )
    got = reconcile.check_session("2026-08-12")
    assert got["ok"] is False
    assert got["mismatches"] == [
        {"module": "earnings", "field": "cost", "fact_set": 10.0, "ledger": 4.0, "delta": 6.0}
    ]


def test_reconcile_passes_when_the_ledger_agrees(store, monkeypatch):
    facts.write(
        {
            "session": "2026-08-12",
            "modules": {"earnings": {"ok": True, "results": {"closed": 5, "gross": 100.0, "cost": 10.0}}},
        }
    )
    monkeypatch.setattr(
        reconcile, "_independent_totals", lambda m, s: {"closed": 5, "gross": 100.0, "cost": 10.0}
    )
    assert reconcile.check_session("2026-08-12")["ok"] is True


def test_reconcile_says_so_when_there_is_no_fact_set(store):
    assert reconcile.check_session("1999-01-01")["ok"] is False


# --------------------------------------------------------------------------- which session --final closes
def test_a_final_pass_closes_out_the_prior_trading_day_never_today():
    """The bug that put "nothing closed today" under a page showing 645 closed trades.

    `--final` runs at 10:15 ET, 45 minutes INTO the next session, and its job is to close out the
    PREVIOUS one — earnings settles overnight, so session D is not final until D+1. It defaulted to
    today instead, stamped a 45-minute stub `final`, and that false stamp defeated the narrative's
    own "final sessions only" guard.
    """
    # Wednesday 2026-08-12 -> Tuesday. A plain weekday step, no weekend involved.
    assert facts.session_to_finalise("2026-08-12") == "2026-08-11"
    # Monday 2026-08-17 -> Friday: the weekend is skipped, so Friday is not left unfinalised.
    assert facts.session_to_finalise("2026-08-17") == "2026-08-14"
    # Never the day it was asked about, which is the one answer it can never be.
    for day in ("2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17"):
        assert facts.session_to_finalise(day) != day


def test_the_final_default_is_a_trading_day_even_when_run_on_a_weekend():
    """A manual `build --final` on a Saturday must still name a real session."""
    assert facts.session_to_finalise("2026-08-15") == "2026-08-14"  # Saturday -> Friday
    assert facts.session_to_finalise("2026-08-16") == "2026-08-14"  # Sunday   -> Friday


def test_module_facts_report_how_much_of_the_net_rests_on_one_arm():
    """A module total averages its arms, which is what hides the finding when the arms ARE the
    experiment. flies published +6,748.01 for 2026-08-19 on a session where one seven-fill book
    returned +7,828.42 and the other twelve came to -1,080.41 — the sign of the day was that arm's
    sign, and nothing in the published facts said so."""
    records = [
        {"profile": "width-10", "net_pnl": 7828.42, "session": "2026-08-19"},
        *({"profile": f"other-{i}", "net_pnl": -1080.41 / 12, "session": "2026-08-19"} for i in range(12)),
    ]
    out = _ledgers.concentration(records)

    assert out["sign_flips_without_largest"] is True
    assert out["largest"]["profile"] == "width-10"
    assert out["net_excluding_largest"] == pytest.approx(-1080.41, abs=0.01)


def test_the_fact_set_carries_the_concentration_block():
    """Guards the wiring, not the arithmetic: the helper is tested in core, and a fact set that
    computes it and forgets to publish it is the failure mode this catches."""
    import inspect

    from cherrypick.review import facts as facts_mod

    source = inspect.getsource(facts_mod.build_module_facts)
    assert '"concentration"' in source
    assert "_ledgers.concentration(" in source
