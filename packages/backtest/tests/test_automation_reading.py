"""An automation's paper record set against its pinned Builder run (`automationref.reading`,
`read automation-reading`): each trade's place in the run's usual range, the running net in its band,
the sample and the entry rates. Synthetic runs and a synthetic paper ledger only."""

from __future__ import annotations

import json
import sqlite3

from cherrypick.backtest import automationref, paths, readout

NAME = "mine"
RUN = "builder-mine-2611201000"


def _doc(nets: list[float]) -> dict:
    trades = []
    for i, n in enumerate(nets):
        day = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        trades.append(
            {
                "arm": NAME,
                "session": day,
                "exit_session": day,
                "exit_minute": 960,
                "exit_kind": "settlement",
                "gross": n,
                "fees": 0,
                "settlement": 0,
                "slippage": {"verdict": 0},
                "net": {"verdict": n, "mid": n},
            }  # fmt: skip
        )
    reasons = {"entered": len(nets), "vix_max": len(nets) // 4}
    return {
        "name": RUN,
        "symbol": "SPX",
        "sessions": ["2026-01-01", "2026-06-30"],
        "summary": {NAME: {"sessions": sum(reasons.values()), "reasons": reasons}},
        "trades": trades,
    }


#: 100 trades from -50 to 49: the usual range is about -40 to 39.
RUN_NETS = [float(x) for x in range(-50, 50)]


def _paper(*nets: float) -> list[dict]:
    return [{"session": f"2026-11-{10 + i:02d}", "net": n} for i, n in enumerate(nets)]


def test_each_trade_is_placed_in_the_runs_usual_range_with_its_percentile():
    r = automationref.reading(_doc(RUN_NETS), NAME, _paper(-45, 0, 45, 10, -5), resamples=300)
    rows = r["paper"]["rows"]
    assert [x["place"] for x in rows] == ["below", "inside", "above", "inside", "inside"]
    assert rows[1]["percentile"] == 50.5
    assert [x["running"] for x in rows] == [-45.0, -45.0, 0.0, 10.0, 5.0]
    assert r["paper"]["counts"] == {"below": 1, "inside": 3, "above": 1}


def test_the_state_is_the_running_nets_place_in_the_band_once_there_are_enough_trades():
    good = automationref.reading(_doc(RUN_NETS), NAME, _paper(1, 2, 3, 4, 5), resamples=300)
    assert good["state"] == "inside" and good["paper"]["running_place"] == "inside"
    bad = automationref.reading(_doc(RUN_NETS), NAME, _paper(-49, -49, -49, -49, -49), resamples=300)
    assert bad["state"] == "below"
    few = automationref.reading(_doc(RUN_NETS), NAME, _paper(1, 2), resamples=300)
    assert few["state"] == "too_few" and few["paper"]["running_place"] is not None
    none = automationref.reading(_doc(RUN_NETS), NAME, [], resamples=300)
    assert none["state"] == "no_trades" and none["paper"]["band_at"] is None


def test_the_band_reaches_past_the_paper_record():
    paper = _paper(*([1.0] * 30))
    r = automationref.reading(_doc(RUN_NETS), NAME, paper, resamples=100)
    assert len(r["reference"]["band"]) == 30 + automationref.AHEAD
    assert r["paper"]["band_at"]["k"] == 30


def test_the_declared_sample_wins_over_the_computed_one():
    up = [30.0, -10.0] * 50
    declared = automationref.reading(_doc(up), NAME, _paper(1), declared=40, resamples=100)
    assert declared["sample"] == {"needed": 40, "basis": "declared", "have": 1}
    computed = automationref.reading(_doc(up), NAME, _paper(1), resamples=100)
    assert computed["sample"]["basis"] == "computed" and computed["sample"]["needed"] > 0


def test_paper_entry_rate_counts_entered_and_missed_days_only():
    r = automationref.reading(
        _doc(RUN_NETS), NAME, [], {"entered": 6, "missed": 2, "pending": 1}, resamples=50
    )
    assert r["paper"]["entry"] == {"entered": 6, "missed": 2, "rate": 0.75}
    assert r["reference"]["entry_rate"] == 0.8


def test_a_run_with_too_few_trades_gives_no_reading():
    r = automationref.reading(_doc([5.0] * 10), NAME, _paper(1, 2, 3, 4, 5, 6))
    assert r["state"] == "no_reference" and "rows" not in r["paper"]


def _ledger(path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ensemble_positions (position_id TEXT, arm TEXT, symbol TEXT, quantity INTEGER, "
        "entry_value REAL, max_profit REAL, capital REAL, gross_pnl REAL, fees REAL, settlement REAL, "
        "slippage REAL, net_pnl REAL, exit_reason TEXT, exit_session TEXT, entry_session TEXT, status TEXT, "
        "entry_time TEXT, exit_time TEXT, entry_spot REAL, exit_spot REAL)"
    )
    for pid, arm, net, day, status in [
        ("a", NAME, 12.0, "2026-11-11", "closed"),
        ("b", NAME, -30.0, "2026-11-10", "closed"),
        ("c", "other", 99.0, "2026-11-10", "closed"),
        ("d", NAME, None, None, "open"),
    ]:
        conn.execute(
            "INSERT INTO ensemble_positions (position_id, arm, symbol, quantity, net_pnl, gross_pnl, fees, "
            "settlement, slippage, exit_session, entry_session, status) "
            "VALUES (?, ?, 'SPX', 1, ?, ?, 0, 0, 0, ?, ?, ?)",
            (pid, arm, net, net, day, day, status),
        )
    conn.execute("CREATE TABLE ensemble_entries (arm TEXT, symbol TEXT, entry_session TEXT, outcome TEXT)")
    for arm, outcome in [
        (NAME, "entered"),
        (NAME, "entered"),
        (NAME, "missed"),
        (NAME, "pending"),
        ("other", "missed"),
    ]:
        conn.execute("INSERT INTO ensemble_entries VALUES (?, 'SPX', '2026-11-10', ?)", (arm, outcome))
    conn.commit()
    conn.close()


def test_the_paper_record_is_this_automations_closed_trades_in_close_order(tmp_path):
    db = tmp_path / "paper_trades.db"
    _ledger(db)
    paper, entries = automationref.paper_record(str(db), NAME)
    assert paper == [{"session": "2026-11-10", "net": -30.0}, {"session": "2026-11-11", "net": 12.0}]
    assert entries == {"entered": 2, "missed": 1, "pending": 1}
    assert automationref.paper_record(str(tmp_path / "none.db"), NAME) == ([], {})


def test_read_sets_the_ledger_against_the_named_run(tmp_path, monkeypatch):
    own = tmp_path / "own"
    own.mkdir()
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: tmp_path / "lic" if licensed else own)
    (own / f"{RUN}.json").write_text(json.dumps(_doc(RUN_NETS)), encoding="utf-8")
    db = tmp_path / "paper_trades.db"
    _ledger(db)
    monkeypatch.setenv("AUTOMATIONS_DB_PATH", str(db))
    out = readout.automation_reading(NAME, RUN, 40)
    assert out["ok"] is True and out["licensed"] is False and out["run"] == RUN
    assert out["paper"]["trades"] == 2 and out["sample"]["basis"] == "declared"
    assert readout.automation_reading(NAME, "builder-mine-2601010000")["ok"] is False
