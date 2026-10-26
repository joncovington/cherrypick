"""The run cards (cards.py): drawdown periods, the calendar, session and trade anatomy, exits, the edge
trend, and the account lens. Synthetic series only, per the licence."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.backtest import __main__ as cli
from cherrypick.backtest import cards, paths, readout, results


def _series(nets, start=1, trades=1):
    return [{"session": f"2024-01-{start + i:02d}", "net": n, "trades": trades} for i, n in enumerate(nets)]


def test_drawdowns_run_from_the_peak_to_the_session_that_regains_it():
    path = cards.equity_path(_series([10, -5, -10, 20, -3, 5]))
    assert [p["cum"] for p in path] == [10, 5, -5, 15, 12, 17]
    assert [p["drawdown"] for p in path] == [0, 5, 15, 0, 3, 0]
    first, second = cards.drawdown_periods(path)
    assert first == {
        "peak": "2024-01-01",
        "peak_cum": 10,
        "depth": 15,
        "trough": "2024-01-03",
        "recovered": "2024-01-04",
        "sessions_to_trough": 2,
        "sessions_to_recover": 1,
    }
    assert (second["peak"], second["depth"], second["recovered"]) == ("2024-01-04", 3, "2024-01-06")


def test_a_drawdown_from_the_start_has_no_peak_and_an_open_one_has_no_recovery():
    (d,) = cards.drawdown_periods(cards.equity_path(_series([-4, -6, 3])))
    assert d["peak"] is None and d["depth"] == 10 and d["sessions_to_trough"] == 2
    assert d["recovered"] is None and d["sessions_to_recover"] is None


def test_only_the_deepest_drawdowns_are_kept():
    nets = [1, -1] * 3 + [1, -9, 20]
    periods = cards.drawdown_periods(cards.equity_path(_series(nets)), top=2)
    assert [d["depth"] for d in periods] == [9, 1]


def test_the_calendar_sums_months_and_gives_each_year_its_own_drawdown():
    series = [
        {"session": "2023-12-28", "net": 5.0, "trades": 1},
        {"session": "2023-12-29", "net": -8.0, "trades": 1},
        {"session": "2024-01-02", "net": 0.0, "trades": 0},
        {"session": "2024-01-03", "net": 4.0, "trades": 2},
    ]
    cal = cards.calendar(series)
    assert cal["months"] == {"2023-12": -3.0, "2024-01": 4.0}
    y23, y24 = cal["years"]
    assert (y23["net"], y23["max_drawdown"], y23["winning"]) == (-3.0, 8.0, 1)
    assert (y24["sessions"], y24["traded"], y24["max_drawdown"]) == (2, 1, 0.0)


def test_a_session_that_did_not_trade_is_neither_a_win_nor_a_loss():
    series = _series([30, -10, -20]) + [{"session": "2024-01-09", "net": 0.0, "trades": 0}]
    a = cards.session_anatomy(series)
    assert (a["count"], a["wins"], a["losses"], a["win_rate"]) == (3, 1, 2, 0.3333)
    assert (a["avg_win"], a["avg_loss"], a["payoff_ratio"]) == (30, -15, 2.0)
    assert (a["best_session"], a["worst_session"]) == ("2024-01-01", "2024-01-03")


def test_a_trade_without_a_recorded_net_is_counted_apart():
    t = cards.trade_anatomy([{"net_pnl": 5.0}, {"net_pnl": None}, {"net_pnl": -1.0}])
    assert (t["count"], t["not_recorded"], t["wins"]) == (2, 1, 1)


def test_exits_group_by_outcome_and_are_absent_where_none_is_recorded():
    trades = [
        {"outcome": "expired", "net_pnl": 10.0},
        {"outcome": "expired", "net_pnl": -2.0},
        {"outcome": "stop", "net_pnl": -30.0},
        {"outcome": None, "net_pnl": 1.0},
    ]
    expired, stop, unrecorded = cards.exits(trades)
    assert expired == {
        "outcome": "expired", "count": 2, "share": 0.5, "win_rate": 0.5, "avg_net": 4.0, "total_net": 8.0
    }  # fmt: skip
    assert (stop["total_net"], unrecorded["outcome"]) == (-30.0, cards.NOT_RECORDED)
    assert cards.exits([{"net_pnl": 1.0}]) is None


def test_the_edge_trend_names_a_clear_change_and_calls_a_flat_series_noise():
    wobble = [1.0, -1.0, 0.5, -0.5]
    flat = _series([wobble[i % 4] for i in range(40)])
    assert cards.edge_trend(flat)["verdict"] == "within noise"
    step = _series([wobble[i % 4] - 10 for i in range(20)] + [wobble[i % 4] + 10 for i in range(20)])
    trend = cards.edge_trend(step)
    assert trend["verdict"] == "improving" and trend["change"] == 20.0
    assert trend["split"] == step[20]["session"]
    assert cards.edge_trend(_series([1.0] * (cards.MIN_TREND_SESSIONS - 1))) is None


def test_the_account_restates_the_same_dollars_without_compounding():
    path = cards.equity_path(
        [
            {"session": "2024-01-01", "net": 100.0, "trades": 1},
            {"session": "2024-12-30", "net": -55.0, "trades": 1},
        ]
    )
    acct = cards.account(path, 1000.0)
    assert (acct["ending"], acct["return"]) == (1045.0, 0.045)
    assert acct["max_drawdown"] == 0.05  # 55 below a peak equity of 1100
    assert acct["years"] == 1.0 and acct["annual_return"] == 0.045
    assert acct["return_to_drawdown"] == 0.9
    assert acct["ruined_on"] is None


def test_an_account_the_drawdown_empties_says_when():
    path = cards.equity_path(_series([50.0, -120.0, -40.0, 500.0]))
    acct = cards.account(path, 100.0)
    assert acct["ruined_on"] == "2024-01-03"  # equity 100 + 50 - 120 - 40 = -10
    assert acct["max_drawdown"] > 1


def test_session_risk_reads_flies_book_peaks_and_otherwise_sums_max_losses():
    flies = [
        {"book_id": "a", "book_peak_risk": 300.0, "capital": None},
        {"book_id": "a", "book_peak_risk": 300.0, "capital": None},
        {"book_id": "b", "book_peak_risk": 200.0, "capital": None},
    ]
    assert cards.session_risk(flies) == 500.0
    assert cards.session_risk([{"capital": 400.0}, {"capital": 450.0}, {"capital": None}]) == 850.0
    assert cards.session_risk([{"capital": None}]) is None


def test_each_arm_defaults_to_its_own_peak_risk_and_a_chosen_capital_is_one_account_for_all():
    series = {"control": _series([10.0, -5.0]), "narrow": _series([2.0, 1.0])}
    trades = {
        "control": {"2024-01-01": [{"capital": 9000.0}], "2024-01-02": [{"capital": 10000.0}]},
        "narrow": {"2024-01-01": [{"capital": 400.0}, {"capital": 500.0}]},
    }
    out = cards.cards(series, trades)
    assert out["account"] == {"capital": None, "basis": "peak_risk"}
    assert out["arms"]["control"]["peak_risk"] == 10000.0
    # The narrow arm reads against what IT needed, not the widest arm's peak.
    assert out["arms"]["narrow"]["account"]["capital"] == 900.0
    chosen = cards.cards(series, trades, capital=50000.0)
    assert chosen["account"] == {"capital": 50000.0, "basis": "chosen"}
    assert {a["account"]["capital"] for a in chosen["arms"].values()} == {50000.0}
    assert chosen["arms"]["narrow"]["peak_risk"] == 900.0
    bare = cards.cards({"control": _series([1.0])}, {"control": {}})
    assert bare["arms"]["control"]["account"] is None and bare["arms"]["control"]["peak_risk"] is None


def test_premium_reads_fee_drag_and_capture_over_the_trades_that_carry_a_credit():
    trades = [
        {"entry_credit": 100.0, "max_profit": 100.0, "fees": 4.0, "net_pnl": 96.0},
        {"entry_credit": 100.0, "max_profit": 100.0, "fees": 6.0, "net_pnl": -206.0},
        {"entry_credit": None, "max_profit": None, "fees": 2.0, "net_pnl": 5.0},
    ]
    p = cards.premium(trades)
    assert (p["n"], p["premium"], p["fees"], p["fee_drag"]) == (2, 200.0, 10.0, 0.05)
    assert (p["capture_rate"], p["capture_n"]) == (-0.55, 2)
    assert cards.premium([{"entry_credit": None, "net_pnl": 1.0}]) is None


@pytest.mark.parametrize("raw", ["0", "-5", "nan", "inf", "lots"])
def test_a_capital_that_is_not_a_positive_number_is_refused(raw):
    with pytest.raises(ValueError):
        cards.parse_capital(raw)


RUN = "20261017-120000-abcdef"


def _meic_ledger(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (symbol TEXT, arm TEXT, pnl REAL, fees REAL, exit_time TEXT, "
        "wing_width REAL, net_credit REAL, quantity INTEGER, dollar_multiplier REAL, exit_reason TEXT)"
    )
    conn.executemany("INSERT INTO ic_trades VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_a_run_is_read_back_with_its_cards():
    runs = paths.results_dir() / "runs"
    (runs / RUN).mkdir(parents=True)
    report = {"run_id": RUN, "module": "meic", "arms": {"control": {}}}
    (runs / f"{RUN}.json").write_text(json.dumps(report), encoding="utf-8")
    conn = results.connect()
    for day, net in (("2024-01-02", 20.0), ("2024-01-03", -50.0)):
        conn.execute(
            "INSERT INTO session_results VALUES (?,?,?,?,?,?,?,?)",
            (RUN, "control", "h", day, net, net, 1.0, 1),
        )
    conn.commit()
    conn.close()
    _meic_ledger(
        runs / RUN / "2024-01-02.db",
        [("SPX", "control", 21.0, 1.0, "2024-01-02T16:00", 5.0, 1.0, 1, 100.0, "expired_settlement")],
    )
    _meic_ledger(
        runs / RUN / "2024-01-03.db",
        [("SPX", "control", -49.0, 1.0, "2024-01-03T13:00", 5.0, 1.0, 1, 100.0, "per_side_stop")],
    )
    out = readout.run_report(RUN)
    assert out["ok"] and out["report"] == report
    c = out["cards"]
    assert c["ok"] and c["account"] == {"capital": None, "basis": "peak_risk"}
    arm = c["arms"]["control"]
    assert arm["peak_risk"] == 400.0 and arm["account"]["capital"] == 400.0
    assert arm["premium"]["premium"] == 200.0 and arm["premium"]["fee_drag"] == 0.01
    assert [e["outcome"] for e in arm["exits"]] == ["expired_settlement", "per_side_stop"]
    assert arm["max_drawdown"] == 50.0 and arm["account"]["max_drawdown"] == 0.119  # 50 / 420
    assert cli.main(["read", "run", "--id", RUN, "--capital", "-1"]) == 1


def test_the_report_still_reads_when_its_cards_cannot_be_built():
    runs = paths.results_dir() / "runs"
    runs.mkdir(parents=True)
    (runs / f"{RUN}.json").write_text(json.dumps({"run_id": RUN, "module": "meic"}), encoding="utf-8")
    out = readout.run_report(RUN)
    assert out["ok"] and out["report"] == {"run_id": RUN, "module": "meic"}
    assert out["cards"]["ok"] is False and out["cards"]["error"]
