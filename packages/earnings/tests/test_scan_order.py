"""The 2026-11-05 boundary: the entry scan spends its budget on tradable names first.

The liquidity verdict (universe.py) refuses most of the calendar at execution, but the screen used to
run on every name in calendar order first, so a binding `entry_scan_budget_seconds` left tradable
names unscreened. Each test breaks one half of the new order and shows the run following it.
"""

import types

import pytest

from cherrypick.earnings import rank_strategies, scanner, universe
from cherrypick.earnings import strat_test_harness as r

DAY = "2026-11-05"


def _entry(symbol):
    return {"symbol": symbol, "date": "2026-11-06", "timing": "After market close"}


def _verdict(**names):
    return {
        "names": {s: {"verdict": v, "reasons": []} for s, v in names.items()},
        "reference": {},
        "updated_on": DAY,
    }


def _accepted(strategy="iron_fly"):
    return [{"name": strategy, "reject_reasons": [], "accepted": True, "criteria": {}}]


def _drive(monkeypatch, calendar, verdict, today="2026-11-05", **config):
    import datetime as dt

    rows, reviews = [], []
    monkeypatch.setattr(scanner, "et_today", lambda: dt.date.fromisoformat(today))
    monkeypatch.setattr(rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(r, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(r, "_save_entry_review", lambda *a, **k: reviews.append(a))
    monkeypatch.setattr(
        scanner,
        "_load_config",
        lambda *a, **k: {
            "strategies": {name: {} for name in rank_strategies._REGISTRY_BY_NAME},
            "strat_test_portfolio": "per_strategy",
            **config,
        },
    )
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda cfg, **k: [dict(e) for e in calendar])
    monkeypatch.setattr(r.symbol_watch, "prefilter_symbols", lambda cal, cfg, session=None: (cal, {}))
    monkeypatch.setattr(r, "_log_scan_row", lambda *a, **kw: rows.append((a, kw)))
    monkeypatch.setattr(r, "_past_entry_window", lambda cfg: False)
    monkeypatch.setattr(universe, "load", lambda path=None: verdict)
    return rows, reviews


CAL = [_entry(s) for s in ("REF1", "TRAD1", "REF2", "TRAD2", "TRAD3")]
VERDICT = _verdict(REF1="thin", TRAD1="tradable", REF2="illiquid", TRAD2="tradable", TRAD3="tradable")


def test_every_tradable_name_is_screened_before_any_refused_one(monkeypatch):
    """(a) One worker and a stub screen: the symbols reach evaluate_symbol in dispatch order."""
    _drive(monkeypatch, CAL, VERDICT, entry_scan_workers=1)
    called = []

    def screen(symbol, d, t, cfg, *skip):
        called.append(symbol)
        return []

    monkeypatch.setattr(rank_strategies, "evaluate_symbol", screen)
    r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert called == ["TRAD1", "TRAD2", "TRAD3", "REF1", "REF2"]


def test_a_budget_that_binds_drops_refused_names_only(monkeypatch):
    """(a) The calendar reaches the scan tradable-first, so the tail the budget drops is refused."""
    _drive(monkeypatch, CAL, VERDICT)
    seen = {}

    def stream(calendar, *a, **k):
        seen["order"] = [e["symbol"] for e in calendar]
        for e in calendar[:3]:
            yield (e, _accepted(), None)
        for e in calendar[3:]:
            yield (e, None, "entry_scan_budget_exceeded")

    monkeypatch.setattr(r, "_scan_stream", stream)
    for name in r._ORDER_FNS:
        monkeypatch.setitem(r._ORDER_FNS, name, lambda *a: {"ok": False, "error": "stub"})
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert seen["order"] == ["TRAD1", "TRAD2", "TRAD3", "REF1", "REF2"]
    unreached = [s["symbol"] for s in result["skipped"] if s["reason"] == "entry_scan_budget_exceeded"]
    assert unreached == ["REF1", "REF2"]


def test_a_refused_name_screened_with_leftover_budget_is_recorded_and_never_opened(monkeypatch):
    """(b) Its screen row and the not_tradable drop are written, after the tradable names' rows."""
    rows, reviews = _drive(monkeypatch, CAL[:2], VERDICT)
    monkeypatch.setattr(
        r, "_scan_stream", lambda calendar, *a, **k: [(e, _accepted(), None) for e in calendar]
    )
    built = []
    monkeypatch.setitem(
        r._ORDER_FNS, "iron_fly", lambda sym, *a: built.append(sym) or {"ok": False, "error": "stub"}
    )
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert built == ["TRAD1"]
    assert result["opened"] == []
    logged = [(a[1], kw["stage"], kw["outcome"], kw["reason"]) for a, kw in rows]
    assert [x[:3] for x in logged] == [
        ("TRAD1", "screen", "accepted"),
        ("TRAD1", "execution", "dropped"),
        ("REF1", "screen", "accepted"),
        ("REF1", "execution", "dropped"),
    ]
    assert logged[3][3].startswith("not_tradable: thin")
    assert [a[1] for a in reviews] == ["TRAD1", "REF1"]


def test_an_unreached_refused_name_is_recorded_as_budget_exceeded(monkeypatch):
    rows, reviews = _drive(monkeypatch, CAL[:2], VERDICT)
    monkeypatch.setattr(
        r,
        "_scan_stream",
        lambda calendar, *a, **k: (
            [(e, None, "entry_scan_budget_exceeded") for e in calendar[1:]] + [(calendar[0], [], None)]
        ),
    )
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert [s["symbol"] for s in result["skipped"]] == ["REF1"]
    assert [(a[1], a[5]) for a in reviews if a[1] == "REF1"] == [("REF1", ["entry_scan_budget_exceeded"])]


def test_a_missing_verdict_keeps_the_calendar_order(monkeypatch):
    """Everything is refused, loudly, so nothing is reordered and nothing is skipped quietly."""
    _drive(monkeypatch, CAL, {"error": "no verdict file (verdicts.json)"})
    seen = {}

    def stream(calendar, *a, **k):
        seen["order"] = [e["symbol"] for e in calendar]
        return []

    monkeypatch.setattr(r, "_scan_stream", stream)
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert seen["order"] == [e["symbol"] for e in CAL]
    assert result["universe"]["unavailable"]


def test_before_the_universe_gate_the_calendar_order_stands(monkeypatch):
    _drive(monkeypatch, CAL, VERDICT, today="2026-10-27")
    seen = {}

    def stream(calendar, *a, **k):
        seen["order"] = [e["symbol"] for e in calendar]
        return []

    monkeypatch.setattr(r, "_scan_stream", stream)
    r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert seen["order"] == [e["symbol"] for e in CAL]


# --------------------------------------------------------------------------- retired strategies
def test_the_scan_is_told_which_strategies_are_retired(monkeypatch):
    """(c) Dated: before 10-15 none, from 10-26 all three."""
    for today, expected in (
        ("2026-10-14", set()),
        ("2026-10-20", {"atm_calendar", "double_calendar"}),
        ("2026-11-05", {"atm_calendar", "double_calendar", "broken_wing_butterfly"}),
    ):
        _drive(monkeypatch, CAL[:1], VERDICT, today=today)
        seen = {}

        def stream(calendar, config, workers, timeout, budget, skip=frozenset(), _seen=seen):
            _seen["skip"] = set(skip)
            return []

        monkeypatch.setattr(r, "_scan_stream", stream)
        r.cmd_run_entries(types.SimpleNamespace(date=None))
        assert seen["skip"] == expected, today


def test_evaluate_symbol_runs_no_screen_for_a_skipped_strategy(monkeypatch):
    fetched = []

    def entry(name):
        def fetch(*a):
            fetched.append(name)
            return {"ok": True, "criteria": {}}

        return {
            "name": name,
            "strategy_config_fn": lambda cfg: {},
            "fetch_criteria_fn": fetch,
            "apply_tiering_fn": lambda crit, cfg: {"accepted": False, "reject_reasons": ["x"]},
        }

    monkeypatch.setattr(rank_strategies, "STRATEGY_REGISTRY", [entry("live_one"), entry("old_one")])
    monkeypatch.setattr(scanner, "fetch_avg_volume", lambda s, c: None)
    monkeypatch.setattr(scanner, "fetch_iv_rv_ratio", lambda s, c: {"ok": False})
    monkeypatch.setattr(
        scanner,
        "compute_winrate",
        lambda s, c, n: {"winrate": None, "sample_size": 0, "realized_move_quarters": []},
    )
    monkeypatch.setattr(scanner, "move_stats_from_quarters", lambda *a: {})
    monkeypatch.setattr(scanner, "apply_common_signals", lambda *a: None)
    monkeypatch.setattr(scanner, "compute_composite_score", lambda *a: 0.0)
    monkeypatch.setattr(scanner, "begin_tt_cache", lambda: None)
    monkeypatch.setattr(scanner, "end_tt_cache", lambda: None)
    monkeypatch.setattr(rank_strategies, "_has_listed_options", lambda s: True)

    results = rank_strategies.evaluate_symbol(
        "AAA", "2026-11-06", "After market close", {}, frozenset({"old_one"})
    )
    assert [x["name"] for x in results] == ["live_one"]
    assert fetched == ["live_one"]


def test_a_retired_strategy_the_screen_still_returns_is_not_opened(monkeypatch):
    """The execution drop stays as the backstop behind the skip."""
    _drive(monkeypatch, CAL[1:2], VERDICT)
    monkeypatch.setattr(
        r, "_scan_stream", lambda calendar, *a, **k: [(e, _accepted("atm_calendar"), None) for e in calendar]
    )
    monkeypatch.setitem(r._ORDER_FNS, "atm_calendar", lambda *a: pytest.fail("opened a retired strategy"))
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert [s["reason"] for s in result["skipped"]] == ["strategy_retired"]
