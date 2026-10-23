"""verdict.py: the strategy verdict the console's strategies page renders."""

from datetime import datetime

from cherrypick.earnings import verdict


def _epoch(day: str, hour: int = 15) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:35:00").replace(tzinfo=verdict.ET).timestamp()


def _trade(arm, symbol, day, pnl, *, cost=10.0, cap=500.0, reason="profit_target", hold=1, **extra):
    return {
        "order_id": f"{arm}-{symbol}-{day}",
        "arm": arm,
        "strategy": arm.split(":")[-1],
        "symbol": symbol,
        "opened_at": _epoch(day),
        "closed_at": _epoch(day) + 86_400,
        "pnl": pnl,
        "entry_cost": cost / 2,
        "exit_cost": cost / 2,
        "capital_at_risk": cap,
        "entry_credit": 2.0,
        "exit_reason": reason,
        "hold_days": hold,
        **extra,
    }


def test_net_subtracts_entry_and_exit_cost_only():
    # settlement is already inside exit_cost: subtracting settlement_fees again double-counts it
    t = _trade("strat_test:iron_fly", "A", "2026-10-01", 100.0, cost=20.0, settlement_fees=5.0)
    assert verdict.net_of(t) == 80.0


def test_ci_is_deterministic():
    rows = [_trade("strat_test:iron_fly", f"S{i}", "2026-10-01", (i % 5 - 2) * 50.0) for i in range(30)]
    assert verdict.clustered_ci(rows) == verdict.clustered_ci(list(rows))


def test_ci_resamples_events_not_trades():
    # Ten trades on ONE event are one draw: no interval can be formed from a single event.
    one_event = [_trade(f"strat_test:s{i}", "ACN", "2026-09-30", -100.0 * i) for i in range(10)]
    ci = verdict.clustered_ci(one_event)
    assert ci["events"] == 1 and ci["expectancy"] is None
    two = one_event + [_trade("strat_test:s0", "ORCL", "2026-09-09", 500.0)]
    assert verdict.clustered_ci(two)["events"] == 2


def test_ci_brackets_a_clear_loser_below_zero():
    rows = [_trade("strat_test:atm_calendar", f"S{i}", "2026-10-01", -100.0 - i) for i in range(20)]
    lo, hi = verdict.clustered_ci(rows)["expectancy"]
    assert hi < 0


def test_flag_rules_and_their_bounds():
    tick = _trade("strat_test:iron_fly", "LEN", "2026-09-16", -40.0, hold=0, reason="pead_loser")
    tick_after_fix = _trade("strat_test:iron_fly", "X", "2026-10-16", -40.0, hold=0)
    stranded = _trade("strat_test:iron_fly", "CRM", "2026-08-26", -2341.0, reason="expired", hold=3)
    stranded_but_stopped = _trade("strat_test:iron_fly", "SNPS", "2026-08-26", -1300.0, reason="stop_loss")
    assert verdict.flag_of(tick) == "entry_tick_close"
    assert verdict.flag_of(tick_after_fix) is None
    assert verdict.flag_of(stranded) == "stranded_expiry"
    assert verdict.flag_of(stranded_but_stopped) is None


def test_flagged_rows_excluded_by_default_and_kept_on_request():
    rows = [
        _trade("strat_test:iron_fly", "CRM", "2026-08-26", -2341.0, reason="expired", hold=3),
        _trade("strat_test:iron_fly", "ORCL", "2026-09-09", 500.0),
    ]
    out = verdict.build(rows)
    assert out["arms"][0]["n"] == 1
    assert out["excluded"] == [{"rule": "stranded_expiry", "n": 1, "net": -2351.0}]
    assert [f["rule"] for f in out["flagged"]] == ["stranded_expiry"]
    assert verdict.build(rows, include_flagged=True)["arms"][0]["n"] == 2


def test_flagged_lists_rows_outside_the_scope_too():
    rows = [_trade("strat_test:iron_fly", "CRM", "2026-08-26", -2341.0, reason="expired", hold=3)]
    out = verdict.build(rows, since="2026-09-01")
    assert out["excluded"] == [] and len(out["flagged"]) == 1


def test_twins_are_never_pooled_and_pair_on_symbol_strategy_session():
    rows = [
        _trade("strat_test:iron_fly", "ACN", "2026-09-30", -2157.0),
        _trade("advised:take-earlier:iron_fly", "ACN", "2026-09-30", -2277.0),
        _trade("advised:take-earlier:iron_fly", "ZZZ", "2026-09-30", 999.0),  # no control: unpaired
    ]
    out = verdict.build(rows)
    assert [a["arm"] for a in out["arms"]] == ["strat_test:iron_fly"]
    assert out["arms"][0]["net"] == -2167.0
    (tw,) = out["twins"]
    assert tw["pairs"] == 1 and tw["diff"] == -120.0 and tw["twin_better"] == 0


def test_concentration_and_green_first_stops():
    rows = [
        _trade(
            "strat_test:iron_condor", "A", "2026-10-01", -1000.0, reason="stop_loss", max_unrealized_pnl=50.0
        ),
        _trade(
            "strat_test:iron_condor", "B", "2026-10-02", -500.0, reason="stop_loss", max_unrealized_pnl=-10.0
        ),
        _trade("strat_test:iron_condor", "C", "2026-10-03", 300.0),
        _trade("strat_test:iron_condor", "D", "2026-10-04", 200.0),
    ]
    a = verdict.build(rows)["arms"][0]
    assert a["stops"] == {"n": 2, "net": -1520.0, "green_first": 1, "green_first_net": -1010.0}
    assert a["concentration"]["net_ex_worst3"] == 290.0
    assert a["cumulative"][-1]["cumulative"] == a["net"]


def test_terciles_split_on_the_covariate():
    rows = [
        _trade(
            "strat_test:iron_fly",
            f"S{i}",
            "2026-10-01",
            100.0 if i >= 6 else -100.0,
            entry_context={"term_structure": float(i)},
        )
        for i in range(9)
    ]
    t = verdict.terciles(rows, "term_structure")
    assert [b["n"] for b in t["buckets"]] == [3, 3, 3]
    assert t["buckets"][0]["ror_pct"] < 0 < t["buckets"][2]["ror_pct"]
