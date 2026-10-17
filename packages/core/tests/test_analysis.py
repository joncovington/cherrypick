"""core.analysis: streaks, drawdown episodes, profit durability, the reshuffle stress test, and the
per-arm reading and CLI built from them (docs/strategy-analysis-plan.md, Phase 1)."""

import argparse
import sqlite3

import pytest

from cherrypick.core import analysis, ledgers
from cherrypick.core.analysis import __main__ as cli


# --------------------------------------------------------------------------- streaks
def test_streaks_longest_runs_and_current():
    s = analysis.streaks([10, 5, -3, -4, -1, 7, 2])
    assert s["longest_win"] == 2
    assert s["longest_loss"] == 3
    assert s["current"] == {"kind": "win", "length": 2}


def test_a_flat_trade_ends_a_run_and_is_neither():
    s = analysis.streaks([5, 5, 0, 5])
    assert s["longest_win"] == 2
    assert s["current"] == {"kind": "win", "length": 1}
    assert analysis.streaks([5, 0])["current"] is None


def test_streaks_empty():
    assert analysis.streaks([]) == {"longest_win": 0, "longest_loss": 0, "current": None}


# --------------------------------------------------------------------------- drawdown episodes
def test_episode_from_a_high_through_a_low_to_a_new_high():
    dated = [("2026-10-01", 100), ("2026-10-02", -40), ("2026-10-05", -20), ("2026-10-06", 30),
             ("2026-10-07", 50)]  # fmt: skip
    [e] = analysis.drawdown_episodes(dated)
    assert e["peak_session"] == "2026-10-01" and e["peak_equity"] == 100
    assert e["low_session"] == "2026-10-05" and e["low_equity"] == 40
    assert e["depth"] == 60
    assert e["recovered_session"] == "2026-10-07"
    assert (e["sessions_falling"], e["sessions_recovering"]) == (2, 2)
    # Calendar days, across the weekend between the 2nd and the 5th.
    assert (e["days_falling"], e["days_recovering"]) == (4, 2)
    assert e["open"] is False


def test_losing_from_the_first_trade_is_a_drawdown_from_the_start():
    [e] = analysis.drawdown_episodes([("2026-10-01", -10), ("2026-10-02", -5)])
    assert e["peak_session"] is None and e["peak_equity"] == 0
    assert e["depth"] == 15 and e["sessions_falling"] == 2
    assert e["open"] is True
    # A recovery that has not happened has no length, not the length so far.
    assert e["recovered_session"] is None and e["sessions_recovering"] is None


def test_regaining_the_high_exactly_ends_the_episode():
    episodes = analysis.drawdown_episodes([("2026-10-01", 10), ("2026-10-02", -10), ("2026-10-03", 10)])
    assert len(episodes) == 1 and episodes[0]["open"] is False


def test_separate_episodes_are_listed_in_order():
    dated = [("2026-10-01", 10), ("2026-10-02", -5), ("2026-10-03", 10), ("2026-10-06", -20)]
    first, second = analysis.drawdown_episodes(dated)
    assert first["depth"] == 5 and not first["open"]
    assert second["peak_session"] == "2026-10-03" and second["depth"] == 20 and second["open"]


def test_no_episodes_on_a_rising_path():
    assert analysis.drawdown_episodes([("2026-10-01", 5), ("2026-10-02", 5)]) == []


# --------------------------------------------------------------------------- durability
def test_durability_removes_the_largest_winners_only():
    nets = [100, 50, 10, -20] + [-1] * 16  # 20 trades, net 124
    d = analysis.durability(nets, fractions=(0.05, 0.10))
    assert d["net"] == 124 and d["trades"] == 20
    five, ten = d["without_top"]
    assert (five["asked"], five["removed"], five["net"]) == (1, 1, 24)
    assert (ten["asked"], ten["removed"], ten["net"]) == (2, 2, -26)
    assert five["sign_flips"] is False and ten["sign_flips"] is True


def test_durability_says_when_there_were_fewer_winners_than_asked():
    d = analysis.durability([30, -1, -1, -1], fractions=(0.5,))
    [row] = d["without_top"]
    assert row["asked"] == 2 and row["removed"] == 1 and row["net"] == -3


def test_durability_empty():
    d = analysis.durability([])
    assert d["net"] == 0 and all(r["removed"] == 0 for r in d["without_top"])


# --------------------------------------------------------------------------- stress
def test_stress_is_withheld_on_a_short_sample():
    s = analysis.stress([1.0, -2.0, 3.0])
    assert s["reordered"] is None and s["grouped"] is None
    assert "fewer than" in s["withheld"]
    assert s["historical"] == 2.0


def test_stress_is_deterministic_and_reads_the_quantile():
    values = [10.0, -30.0, 5.0, 20.0, -15.0] * 6
    a = analysis.stress(values, resamples=300)
    b = analysis.stress(values, resamples=300)
    assert a == b
    assert a["withheld"] is None
    # Ordering the losses together is always possible, so some shuffle must go deeper than the
    # recorded path, and the 95th percentile of hundreds of shuffles is past the recorded figure.
    assert a["reordered"] > a["historical"]
    assert a["grouped"] > 0


def test_stress_only_reorders_never_resamples():
    # One -50 among +10s: every ORDER of these sessions has a max drawdown of exactly 50 (the loss
    # always falls from the high before it). A with-replacement draw could hold the -50 twice and
    # read 100, which is a different sample, not a different order -- the bug this guards.
    values = [10.0] * 24 + [-50.0]
    s = analysis.stress(values, resamples=500)
    assert s["reordered"] == 50.0 and s["grouped"] == 50.0


# --------------------------------------------------------------------------- one arm
def _rec(net, session, **kw):
    return {"arm": "control", "net_pnl": net, "session": session, "slippage": None, "capital": None,
            "max_profit": None, **kw}  # fmt: skip


def test_arm_reading_orders_by_session_then_entry_time_and_skips_unscored():
    records = [
        _rec(-5.0, "2026-10-02", entry_time="2026-10-02T10:00"),
        _rec(20.0, "2026-10-01", entry_time="2026-10-01T11:00"),
        _rec(-3.0, "2026-10-02", entry_time="2026-10-02T09:30"),
        _rec(None, "2026-10-03"),
    ]
    r = analysis.arm_reading(records)
    assert r["version"] == analysis.VERSION
    assert r["reading"]["sample"] == 3 and r["reading"]["unscored"] == 1
    assert r["best_trade"] == {"net": 20.0, "session": "2026-10-01", "entry_time": "2026-10-01T11:00"}
    assert r["worst_trade"]["net"] == -5.0
    assert r["streaks"]["current"] == {"kind": "loss", "length": 2}
    # Per session the two losses on the 2nd are one losing day.
    assert r["session_streaks"]["current"] == {"kind": "loss", "length": 1}
    assert r["session_streaks"]["longest_win"] == 1
    assert r["drawdowns"]["count"] == 1 and r["drawdowns"]["open"]["depth"] == 8.0
    assert r["coverage"] == {"trades": 3, "entry_time": 3, "exit_time": 0, "entry_spot": 0, "exit_spot": 0}


def test_arm_reading_of_nothing():
    r = analysis.arm_reading([])
    assert r["best_trade"] is None and r["drawdowns"]["count"] == 0
    assert r["stress"]["withheld"]


# --------------------------------------------------------------------------- the flies trade fields
def _flies_db(path, *, with_trade_columns=True):
    conn = sqlite3.connect(path)
    extra = ", entry_time TEXT, exit_time TEXT, underlying_at_entry REAL, settlement_price REAL"
    conn.execute(
        "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, entry_mode TEXT, gross_pnl REAL, "
        f"fees REAL, trade_date TEXT, status TEXT{extra if with_trade_columns else ''})"
    )
    if with_trade_columns:
        conn.executemany(
            "INSERT INTO fly_positions VALUES (?,?,?,?,?,?,'settled',?,?,?,?)",
            [
                ("SPX", "control", "outright", 120.0, 4.0, "2026-10-14", "2026-10-14T10:05:00",
                 "2026-10-14T16:15:00", 6650.5, 6671.25),
                ("SPX", "control", "outright", -80.0, 4.0, "2026-10-15", "2026-10-15T10:05:00",
                 "2026-10-15T16:15:00", None, 6702.0),
            ],
        )  # fmt: skip
    else:
        conn.execute(
            "INSERT INTO fly_positions VALUES ('SPX','control','outright',10.0,1.0,'2026-10-14','settled')"
        )
    conn.commit()
    return conn


def test_flies_reader_supplies_the_trade_fields(tmp_path):
    conn = _flies_db(tmp_path / "f.db")
    conn.row_factory = sqlite3.Row
    first, second = sorted(ledgers.READERS["fly_book"](conn), key=lambda r: r["session"])
    assert first["entry_time"] == "2026-10-14T10:05:00" and first["exit_time"] == "2026-10-14T16:15:00"
    assert (first["entry_spot"], first["exit_spot"]) == (6650.5, 6671.25)
    # An unrecorded spot stays None, never zero.
    assert second["entry_spot"] is None and second["exit_spot"] == 6702.0


def test_flies_reader_on_a_ledger_older_than_the_columns(tmp_path):
    conn = _flies_db(tmp_path / "f.db", with_trade_columns=False)
    conn.row_factory = sqlite3.Row
    [r] = ledgers.READERS["fly_book"](conn)
    assert all(r[f] is None for f in analysis.TRADE_FIELDS)


# --------------------------------------------------------------------------- CLI
def test_cli_reads_a_flies_ledger(tmp_path):
    _flies_db(tmp_path / "f.db").close()
    out = cli.cmd_read(argparse.Namespace(db=str(tmp_path / "f.db"), schema="fly_book", start=None, end=None))
    assert out["ok"] and out["version"] == analysis.VERSION and out["n_records"] == 2
    control = out["groups"]["control"]
    assert control["reading"]["net_pnl"] == 32.0  # (120 - 4) + (-80 - 4)
    assert control["coverage"]["entry_spot"] == 1


def test_cli_refuses_an_era_on_a_schema_without_one(tmp_path):
    _flies_db(tmp_path / "f.db").close()
    args = argparse.Namespace(db=str(tmp_path / "f.db"), schema="fly_book", start=None, end=None, era="v2")
    out = cli.cmd_read(args)
    assert out["ok"] is False and "carry no era" in out["error"]
    args.era = "ALL"
    assert cli.cmd_read(args)["ok"] is True


def test_cli_refuses_an_unknown_schema(tmp_path):
    out = cli.cmd_read(argparse.Namespace(db=str(tmp_path / "x.db"), schema="nope", start=None, end=None))
    assert out == {"ok": False, "error": "unknown schema 'nope'"}


@pytest.mark.parametrize("argv", [["read", "--db", "x.db", "--schema", "nope"]])
def test_cli_main_rejects_a_schema_it_does_not_know(argv):
    with pytest.raises(SystemExit):
        cli.main(argv)
