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


def _long(nets, trades=1):
    from datetime import date, timedelta

    d0 = date(2024, 1, 1)
    return [
        {"session": (d0 + timedelta(days=i)).isoformat(), "net": n, "trades": trades}
        for i, n in enumerate(nets)
    ]


def _checks(series):
    out = cards.arm_cards(series, [], None)
    return {c["check"]: c for c in out["health"]["checks"]}, out["health"]["flags"]


def test_the_longest_losing_run_skips_sessions_that_did_not_trade():
    s = _series([-1, -1, 0, -1, 2, -1])
    s[2]["trades"] = 0  # neither breaks nor extends the run
    assert cards.longest_losing_run(s) == 3


def test_the_longest_drawdown_counts_sessions_from_its_peak_to_recovery_or_the_end():
    path = cards.equity_path(_series([5, -1, -1, -1, 4, -2, -1]))
    # 5 is the peak; it is regained at the fifth session (4 sessions later); the last fall is open.
    assert cards.longest_drawdown(path) == {"sessions": 4, "recovered": True}
    open_ = cards.equity_path(_series([5, -1, -1, -1, -1, -1, 1]))
    assert cards.longest_drawdown(open_) == {"sessions": 6, "recovered": False}
    assert cards.longest_drawdown(cards.equity_path(_series([1, 2, 3]))) == {"sessions": 0, "recovered": True}


def test_a_result_carried_by_five_rare_wins_is_flagged_on_every_check_it_trips():
    # The long-shot shape: a big win every 40 sessions, a small loss every other session.
    nets = [500.0 if i % 40 == 39 else -10.0 for i in range(200)]
    checks, flags = _checks(_long(nets))
    assert checks["best_sessions"]["flag"] and checks["best_sessions"]["rest_mean"] == -10.0
    assert checks["win_rate"]["flag"] and checks["win_rate"]["value"] == 0.025
    assert checks["losing_run"]["flag"] and checks["losing_run"]["value"] == 39
    assert not checks["drawdown_length"]["flag"]  # each win recovers the 39 losses before it
    assert "fee_drag" not in checks  # no trade carries a credit: the check is left out, not passed
    assert flags == 3


def test_a_steady_result_raises_no_flag():
    nets = [12.0 if i % 3 else -8.0 for i in range(120)]
    checks, flags = _checks(_long(nets))
    assert flags == 0 and not any(c["flag"] for c in checks.values())
    assert checks["edge_trend"]["value"] == "within noise"


def test_a_long_drawdown_and_a_decaying_edge_are_flagged():
    nets = [10.0] * 60 + [-3.0 if i % 4 else 1.0 for i in range(80)]
    checks, _ = _checks(_long(nets))
    assert checks["drawdown_length"]["flag"] and checks["drawdown_length"]["recovered"] is False
    assert checks["edge_trend"]["flag"] and checks["edge_trend"]["value"] == "decaying"


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
    assert out["account"] == {
        "capital": None,
        "basis": "peak_risk",
        "risk_pct": 2.0,
        "risk_unit": "max_loss",
        "dd_pct": 30.0,
    }
    assert out["arms"]["control"]["peak_risk"] == 10000.0
    # The narrow arm reads against what IT needed, not the widest arm's peak.
    assert out["arms"]["narrow"]["account"]["capital"] == 900.0
    chosen = cards.cards(series, trades, capital=50000.0)
    assert chosen["account"] == {
        "capital": 50000.0,
        "basis": "chosen",
        "risk_pct": 2.0,
        "risk_unit": "max_loss",
        "dd_pct": 30.0,
    }
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
    assert c["ok"] and c["account"] == {
        "capital": None,
        "basis": "peak_risk",
        "risk_pct": 2.0,
        "risk_unit": "max_loss",
        "dd_pct": 30.0,
    }
    arm = c["arms"]["control"]
    assert arm["peak_risk"] == 400.0 and arm["account"]["capital"] == 400.0
    assert arm["premium"]["premium"] == 200.0 and arm["premium"]["fee_drag"] == 0.01
    assert [e["outcome"] for e in arm["exits"]] == ["expired_settlement", "per_side_stop"]
    assert arm["max_drawdown"] == 50.0 and arm["account"]["max_drawdown"] == 0.119  # 50 / 420
    assert arm["settings"] == {"recorded": False}  # no spec in the report, no trial row for it
    assert cli.main(["read", "run", "--id", RUN, "--capital", "-1"]) == 1
    assert cli.main(["read", "run", "--id", RUN, "--risk-pct", "0"]) == 1
    assert cli.main(["read", "run", "--id", RUN, "--risk-pct", "101"]) == 1
    assert cli.main(["read", "run", "--id", RUN, "--risk-unit", "delta"]) == 1
    assert cli.main(["read", "run", "--id", RUN, "--capital", "50000", "--dd-pct", "0"]) == 1


def _spec(**params):
    base = {"short_delta_target": 0.16, "entry_window_end": "14:00", "stop": 1.0}
    return {
        "module": "meic",
        "arm": "x",
        "params": {**base, **params},
        "cadence": 60,
        "fill_model": "mid-haircut",
    }


def test_a_grid_arm_reads_as_the_one_setting_it_tests():
    s = cards.arm_settings({"control": _spec(), "d20": _spec(short_delta_target=0.2), "same": _spec()})
    assert s["control"] == {
        "recorded": True,
        "cadence": 60,
        "fill_model": "mid-haircut",
        "base": None,
        "differs": {},
    }
    assert s["d20"]["base"] == "control"
    assert s["d20"]["differs"] == {"short_delta_target": {"arm": 0.2, "base": 0.16}}
    assert s["same"]["differs"] == {}


def test_a_param_only_one_side_has_differs_and_an_unrecorded_spec_is_not_no_difference():
    extra = _spec(gate="vix")
    s = cards.arm_settings({"control": _spec(), "gated": extra, "lost": None})
    assert s["gated"]["differs"] == {"gate": {"arm": "vix", "base": None}}
    assert s["lost"] == {"recorded": False}
    alone = cards.arm_settings({"d20": _spec(short_delta_target=0.2)})  # no control spec to read against
    assert alone["d20"]["base"] is None and alone["d20"]["differs"] == {}


def test_a_report_reads_its_own_specs_and_an_older_one_its_trial_rows():
    runs = paths.results_dir() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    old = {
        "run_id": RUN,
        "module": "meic",
        "arms": {"control": {"spec_hash": "hc"}, "d20": {"spec_hash": "hx"}},
    }
    conn = results.connect()
    results.record_trial(conn, module="meic", spec_hash="hc", arm="control", spec=_spec(), run_id="r0",
                         sessions=10, sharpe=None)  # fmt: skip
    conn.commit()
    conn.close()
    specs = readout._arm_specs(old, "meic")
    assert specs["control"]["params"]["short_delta_target"] == 0.16 and specs["d20"] is None
    new = {"arms": {"d20": {"spec_hash": "hx", "spec": _spec(short_delta_target=0.2)}}}
    assert readout._arm_specs(new, "meic")["d20"]["params"]["short_delta_target"] == 0.2


def test_the_report_still_reads_when_its_cards_cannot_be_built():
    runs = paths.results_dir() / "runs"
    runs.mkdir(parents=True)
    (runs / f"{RUN}.json").write_text(json.dumps({"run_id": RUN, "module": "meic"}), encoding="utf-8")
    out = readout.run_report(RUN)
    assert out["ok"] and out["report"] == {"run_id": RUN, "module": "meic"}
    assert out["cards"]["ok"] is False and out["cards"]["error"]


def test_the_trade_log_is_oldest_first_and_leaves_an_unrecorded_field_as_none():
    log = cards.trade_log(
        {
            "2024-01-03": [{"session": "2024-01-03", "exit_time": "2024-01-03T13:00", "net_pnl": -5.0}],
            "2024-01-02": [
                {
                    "session": "2024-01-02",
                    "exit_time": "2024-01-02T16:00",
                    "net_pnl": 2.0,
                    "outcome": "expired",
                },
                {"session": "2024-01-02", "exit_time": "2024-01-02T11:00", "net_pnl": 1.0, "outcome": "stop"},
            ],
        }
    )
    assert [(t["exit_time"], t["outcome"]) for t in log] == [
        ("2024-01-02T11:00", "stop"),
        ("2024-01-02T16:00", "expired"),
        ("2024-01-03T13:00", None),
    ]
    assert set(log[0]) == set(cards.TRADE_LOG_FIELDS) and log[0]["capital"] is None


def test_one_arms_trades_are_read_back_from_the_runs_ledgers_and_another_arms_are_not():
    runs = paths.results_dir() / "runs"
    (runs / RUN).mkdir(parents=True)
    report = {"run_id": RUN, "module": "meic", "arms": {"control": {}, "other": {}}}
    (runs / f"{RUN}.json").write_text(json.dumps(report), encoding="utf-8")
    conn = results.connect()
    for arm in ("control", "other"):
        conn.execute(
            "INSERT INTO session_results VALUES (?,?,?,?,?,?,?,?)",
            (RUN, arm, "h", "2024-01-02", 1.0, 1.0, 1.0, 1),
        )
    conn.commit()
    conn.close()
    _meic_ledger(
        runs / RUN / "2024-01-02.db",
        [
            ("SPX", "control", -49.0, 1.0, "2024-01-02T13:00", 5.0, 1.0, 1, 100.0, "per_side_stop"),
            ("SPX", "other", 21.0, 1.0, "2024-01-02T16:00", 5.0, 1.0, 1, 100.0, "expired_settlement"),
        ],
    )
    out = readout.run_trades(RUN, "control")
    assert out["ok"] and out["arm"] == "control"
    (t,) = out["trades"]
    assert (t["outcome"], t["gross_pnl"], t["net_pnl"], t["capital"]) == (
        "per_side_stop",
        -49.0,
        -50.0,
        400.0,
    )
    assert readout.run_trades(RUN, "nope") == {"ok": False, "error": f"run {RUN} has no arm 'nope'"}
    assert readout.run_trades("not a run", "control")["ok"] is False
    assert cli.main(["read", "trades", "--id", RUN, "--arm", "nope"]) == 1


def _risked(series, risk=1000.0):
    """Each session that traded held one condor with `risk` max loss (`capital`, as core.ledgers names it)."""
    return {s["session"]: [{"capital": risk}] for s in series if s["trades"]}


def test_the_compounded_lens_resizes_each_session_on_the_equity_at_that_point():
    series = _series([100.0, 100.0])
    c = cards.compounded(series, _risked(series), start=100_000, risk_pct=2.0)
    # 2% of 100,000 = 2,000 at risk = 2 units; then 2% of 100,200 still floors to 2 whole units.
    assert [p["whole"] for p in c["path"]] == [100_200.0, 100_400.0]
    assert c["fractional"]["ending"] == round(100_000 + 200 + 100 * (100_200 * 0.02 / 1000), 2)
    assert c["whole"]["units_min"] == 2 and c["whole"]["units_max"] == 2
    assert c["start"] == 100_000 and c["risk_pct"] == 2.0


def test_a_small_account_floors_to_no_trade_and_the_session_is_counted_not_hidden():
    series = _series([100.0, -50.0])
    c = cards.compounded(series, _risked(series), start=10_000, risk_pct=2.0)  # 200 at risk < one unit
    assert c["whole"]["ending"] == 10_000 and c["whole"]["sessions_rounded_to_none"] == 2
    assert c["fractional"]["ending"] != 10_000


def test_a_session_with_no_recorded_risk_cannot_be_sized_and_is_left_out_of_both_curves():
    series = _series([100.0, 100.0])
    risks = {"2024-01-01": [{"capital": 1000.0}], "2024-01-02": [{"capital": None}]}
    c = cards.compounded(series, risks, start=100_000)
    assert c["sessions_unsized"] == 1 and c["whole"]["ending"] == 100_200.0


def test_an_account_that_reaches_zero_stops_there():
    series = _series([-60_000.0, 50_000.0])
    c = cards.compounded(series, _risked(series), start=100_000, risk_pct=2.0)
    assert c["whole"]["stopped_on"] == "2024-01-01" and c["whole"]["ending"] == 0.0
    assert c["whole"]["cagr"] == -1.0 and c["whole"]["max_drawdown_pct"] == 1.0


def test_the_lens_needs_a_positive_start_and_risk():
    series = _series([1.0])
    assert cards.compounded(series, _risked(series), start=0) is None
    assert cards.compounded(series, _risked(series), start=1000, risk_pct=0) is None
    assert cards.compounded([], {}, start=1000) is None


def test_the_stop_unit_sizes_by_what_the_stops_would_lose_and_needs_every_position_to_record_it():
    series = _series([100.0, 100.0])
    rows = {
        "2024-01-01": [{"capital": 1000.0, "stop_risk": 250.0}],
        "2024-01-02": [{"capital": 1000.0, "stop_risk": 250.0}, {"capital": 1000.0, "stop_risk": None}],
    }
    c = cards.compounded(series, rows, start=100_000, risk_pct=2.0, unit="stop")
    assert c["unit"] == "stop" and c["whole"]["units_max"] == 8  # 2,000 at risk / 250 a unit
    assert c["sessions_unsized"] == 1  # a session one position of which records no stop
    assert cards.compounded(series, rows, start=100_000, risk_pct=2.0)["whole"]["units_max"] == 2


def test_a_meic_condors_stop_risk_is_both_sides_stopping_at_the_trigger():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE ic_trades (ic_order_id TEXT, net_credit REAL, stop_trigger_original REAL, "
        "quantity INTEGER, dollar_multiplier REAL)"
    )
    conn.executemany(
        "INSERT INTO ic_trades VALUES (?,?,?,?,?)",
        [("a", 2.0, 0.95, 1, 100.0), ("b", 2.0, None, 1, 100.0), ("c", 2.0, 2.0, 2, 100.0)],
    )
    # (2 x 0.95 - 1) x 2.00 x 100 = 180; (2 x 2 - 1) x 2 x 100 x 2 = 1,200; no trigger, no figure.
    assert cards._meic_stop_risk(conn) == {"a": 180.0, "c": 1200.0}
    bare = sqlite3.connect(":memory:")
    bare.execute("CREATE TABLE ic_trades (ic_order_id TEXT, net_credit REAL)")
    assert cards._meic_stop_risk(bare) == {}


def test_a_risk_unit_that_is_not_declared_is_refused():
    assert cards.parse_risk_unit(None) == "max_loss" and cards.parse_risk_unit("stop") == "stop"
    with pytest.raises(ValueError, match="risk unit"):
        cards.parse_risk_unit("delta")


def _year(nets_cycle, n=150):
    """`n` sessions, each one traded condor with a recorded stop: credit $200, ratio 1.0, max loss $800."""
    series = [
        {
            "session": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "net": nets_cycle[i % len(nets_cycle)],
            "trades": 1,
        }
        for i in range(n)
    ]
    rows = {
        s["session"]: [
            {"capital": 800.0, "stop_ratio": 1.0, "credit_dollars": 200.0, "overshoots": [1.0, 1.5]}
        ]
        for s in series
    }
    return series, rows


def test_the_sizing_check_sizes_to_the_drawdown_cap_and_reads_what_that_risks():
    series, rows = _year([50.0, 50.0, -100.0])
    s = cards.sizing(series, rows, account=100_000, dd_pct=25)
    assert s["insufficient"] is False and s["dd_pct"] == 25
    assert s["units"] == int(100_000 * 0.25 // s["drawdown_p99_per_unit"])  # robustness.max_size
    # Overshoots 1.0 and 1.5 on every condor: the 95th percentile is 1.5, so both sides stopped lose
    # (2 x 1.0 x 1.5 - 1) x 200 = 400 a condor, under its 800 max loss.
    assert (
        s["overshoot"]["p95"] == 1.5
        and s["unit_risk_stop_tail"] == 400.0
        and s["unit_risk_max_loss"] == 800.0
    )
    assert s["risk_pct_stop_tail"] == round(100 * s["units"] * 400 / 100_000, 2)


def test_the_stop_tail_never_reads_above_the_max_loss():
    series, rows = _year([50.0, 50.0, -100.0])
    for r in rows.values():
        r[0]["overshoots"] = [9.0]
    assert cards.sizing(series, rows, account=100_000)["unit_risk_stop_tail"] == 800.0


def test_too_little_history_gives_no_size_and_says_so():
    series, rows = _year([50.0, -100.0], n=40)
    s = cards.sizing(series, rows, account=100_000)
    assert s["insufficient"] is True and s["evidence"] == 40 and "units" not in s


def test_a_condors_overshoot_is_its_stopped_sides_cost_over_their_trigger():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE ic_trades (ic_order_id TEXT, net_credit REAL, stop_trigger_original REAL, "
        "put_stop_cost REAL, call_stop_cost REAL, quantity INTEGER, dollar_multiplier REAL)"
    )
    conn.execute("CREATE TABLE ic_spread_legs (ic_order_id TEXT, side TEXT, exit_reason TEXT)")
    conn.executemany(
        "INSERT INTO ic_trades VALUES (?,?,?,?,?,?,?)",
        [("a", 2.0, 1.0, None, 2.6, 1, 100.0), ("b", 2.0, 1.0, None, None, 1, 100.0)],
    )
    conn.executemany(
        "INSERT INTO ic_spread_legs VALUES (?,?,?)",
        [
            ("a", "call", "per_side_stop"),
            ("a", "put", "expired_settlement"),
            ("b", "put", "expired_settlement"),
        ],
    )
    d = cards._meic_stop_detail(conn)
    assert d["a"] == {
        "stop_ratio": 1.0,
        "credit_dollars": 200.0,
        "overshoots": [1.3],
    }  # 2.60 over a 2.00 trigger
    assert d["b"]["overshoots"] == []
