"""The 2026-10-28 boundary (universe.py): from that session an earnings entry opens only on a name the
suite's liquidity verdict calls tradable.

Each test breaks one thing the gate depends on -- the date, the name's verdict, the file -- and shows
the gate following, because a gate that silently passes everything reads exactly like a day on which
every name was liquid.
"""

import datetime as dt
import json
import types

import pytest

from cherrypick.earnings import rank_strategies, scanner, universe
from cherrypick.earnings import strat_test_harness as r

DAY = "2026-10-28"


def _verdict(updated_on=DAY, **names):
    return {
        "names": {
            s: {"verdict": v, "reasons": [f"{s} reason"] if v != "tradable" else []} for s, v in names.items()
        },
        "reference": {"SPY": {"verdict": "tradable", "reasons": []}},
        "updated_on": updated_on,
    }


def test_the_gate_binds_from_its_session_and_not_before():
    v = _verdict(AAA="thin")
    assert universe.refusal("AAA", "2026-10-27", v) is None
    assert universe.refusal("AAA", DAY, v).startswith("not_tradable: thin")


def test_a_tradable_name_and_a_tradable_reference_fund_open():
    v = _verdict(AAA="tradable")
    assert universe.refusal("AAA", DAY, v) is None
    assert universe.refusal("SPY", DAY, v) is None


def test_a_thin_illiquid_or_unknown_name_is_refused_and_says_why():
    v = _verdict(THIN="thin", ILL="illiquid")
    assert universe.refusal("THIN", DAY, v) == f"not_tradable: thin; THIN reason (verdict {DAY})"
    assert universe.refusal("ILL", DAY, v) == f"not_tradable: illiquid; ILL reason (verdict {DAY})"
    assert universe.refusal("NEW", DAY, v) == f"not_tradable: unjudged (verdict {DAY})"


def test_a_share_class_is_matched_in_either_spelling():
    v = _verdict(**{"BRK.B": "tradable"})
    assert universe.refusal("BRK/B", DAY, v) is None


def test_a_missing_or_stale_verdict_refuses_everything_loudly(tmp_path):
    missing = universe.load(tmp_path / "verdicts.json")
    assert universe.refusal("AAA", DAY, missing).startswith("liquidity_verdict_unavailable: no verdict file")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert universe.refusal("AAA", DAY, universe.load(bad)).startswith("liquidity_verdict_unavailable")
    # Written 10-22 (Thu): 10-23, 10-26, 10-27 are three sessions -- still fresh on 10-27 -- and the
    # fourth, 10-28, is one too many.
    old = _verdict(updated_on="2026-10-22", AAA="tradable")
    assert universe.sessions_since("2026-10-22", "2026-10-27") == 3
    assert universe.refusal("AAA", DAY, old).startswith(
        "liquidity_verdict_unavailable: verdict written 2026-10-22"
    )
    assert universe.refusal("AAA", DAY, _verdict(updated_on="2026-10-23", AAA="tradable")) is None


def test_load_reads_the_technicals_file_shape(tmp_path):
    path = tmp_path / "verdicts.json"
    doc = {"updated_on": DAY, "names": {"AAA": {"verdict": "tradable"}}, "reference": {}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert universe.load(path) == {"names": doc["names"], "reference": {}, "updated_on": DAY}


def test_the_tradable_set_and_spellings():
    v = _verdict(AAA="tradable", THIN="thin", **{"BRK.B": "tradable"})
    assert universe.tradable(v) == {"AAA", "BRK.B", "SPY"}
    assert universe.spelled_in("BRK/B", universe.tradable(v)) and not universe.spelled_in(
        "THIN", universe.tradable(v)
    )


# --------------------------------------------------------------------------- the forward preview
def test_the_preview_moves_onto_the_verdict_on_10_28_not_before(monkeypatch):
    from cherrypick.earnings import symbol_watch

    monkeypatch.setattr(scanner, "fetch_watch_universe", lambda: {"TASTY"})
    monkeypatch.setattr(universe, "load", lambda path=None: _verdict(updated_on="2026-10-27", AAA="tradable"))
    assert symbol_watch.preview_universe("2026-10-27") == {"TASTY"}
    assert symbol_watch.preview_universe("2026-10-28") == {"AAA", "SPY"}


def test_a_preview_without_a_usable_verdict_scans_everyone(monkeypatch):
    from cherrypick.earnings import symbol_watch

    monkeypatch.setattr(universe, "load", lambda path=None: {"error": "no verdict file (verdicts.json)"})
    assert symbol_watch.preview_universe("2026-10-28") is None


# --------------------------------------------------------------------------- the harness
_CAL = [{"symbol": "AAA", "date": "2026-10-29", "timing": "After market close"}]


def _drive(monkeypatch, today, verdict):
    rows = []

    class _Today(dt.date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(r, "_date", _Today)
    monkeypatch.setattr(rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(r, "_capture_market_context", lambda day: None)
    monkeypatch.setattr(r, "_save_entry_review", lambda *a, **k: None)
    monkeypatch.setattr(
        scanner, "_load_config", lambda *a, **k: {"strategies": {}, "strat_test_portfolio": "per_strategy"}
    )
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config, **k: list(_CAL))
    monkeypatch.setattr(
        r,
        "_scan_stream",
        lambda *a, **k: [
            (_CAL[0], [{"name": "iron_fly", "reject_reasons": [], "accepted": True, "criteria": {}}], None)
        ],
    )
    monkeypatch.setattr(r, "_log_scan_row", lambda *a, **kw: rows.append((a, kw)))
    monkeypatch.setattr(r, "_past_entry_window", lambda config: False)
    monkeypatch.setattr(universe, "load", lambda path=None: verdict)
    return rows


def test_the_harness_drops_a_thin_name_at_execution_and_keeps_its_screen_row(monkeypatch):
    rows = _drive(monkeypatch, dt.date(2026, 10, 28), _verdict(AAA="thin"))
    monkeypatch.setitem(r._ORDER_FNS, "iron_fly", lambda *a: pytest.fail("built an order for a thin name"))
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert result["opened"] == []
    assert [s["reason"] for s in result["skipped"]] == [f"not_tradable: thin; AAA reason (verdict {DAY})"]
    assert [kw.get("stage") for _, kw in rows] == ["screen", "execution"]
    assert result["universe"] == {"updated_on": DAY, "unavailable": None, "gated": True}


def test_the_harness_reaches_the_order_for_a_tradable_name(monkeypatch):
    _drive(monkeypatch, dt.date(2026, 10, 28), _verdict(AAA="tradable"))
    reached = []
    monkeypatch.setitem(
        r._ORDER_FNS, "iron_fly", lambda *a: reached.append(1) or {"ok": False, "error": "stub"}
    )
    r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert reached == [1]


def test_before_the_boundary_a_thin_name_still_reaches_its_order(monkeypatch):
    """The guard, shown to fire only from its date: on 10-27 the order build is reached."""
    _drive(monkeypatch, dt.date(2026, 10, 27), _verdict(AAA="thin"))
    reached = []
    monkeypatch.setitem(
        r._ORDER_FNS, "iron_fly", lambda *a: reached.append(1) or {"ok": False, "error": "stub"}
    )
    result = r.cmd_run_entries(types.SimpleNamespace(date=None))
    assert reached == [1] and result["universe"]["gated"] is False


# --------------------------------------------------------------------------- the ranked (live) path
def _ranked(monkeypatch, today, verdict):
    class _Today(dt.date):
        @classmethod
        def today(cls):
            return today

    cal = [
        {"symbol": "AAA", "date": "2026-10-29", "timing": "After market close"},
        {"symbol": "THIN", "date": "2026-10-29", "timing": "After market close"},
    ]

    def evaluate(symbol, earnings_date, timing, config):
        viable = [{"name": "iron_fly", "composite_score": 1.0 if symbol == "AAA" else 2.0}]
        return {
            "symbol": symbol,
            "earnings_date": str(earnings_date),
            "earnings_timing": timing,
            "strategies": [],
            "viable": viable,
            "best_strategy": "iron_fly",
            "best_score": viable[0]["composite_score"],
        }

    def select(ranked, config):
        return {"selected": ranked, "skipped": []}

    monkeypatch.setattr(scanner, "_date", _Today)
    monkeypatch.setattr(rank_strategies, "_ensure_dolt_running", lambda: True)
    monkeypatch.setattr(rank_strategies, "_verify_tastytrade_connection", lambda: True)
    monkeypatch.setattr(scanner, "_load_config", lambda *a, **k: {"enable_live_trading": False})
    monkeypatch.setattr(scanner, "fetch_entry_window_calendar", lambda config, **k: list(cal))
    monkeypatch.setattr(rank_strategies.symbol_watch, "covered_symbols", lambda *a, **k: set())
    monkeypatch.setattr(rank_strategies, "_evaluate_and_rank_symbol", evaluate)
    monkeypatch.setattr(scanner, "select_positions", select)
    monkeypatch.setattr(rank_strategies, "_log_symbol_decision", lambda *a, **k: None)
    monkeypatch.setattr(rank_strategies, "_save_entry_review", lambda *a, **k: None)
    monkeypatch.setattr(universe, "load", lambda path=None: verdict)
    out = rank_strategies.cmd_get_ranked_symbols(types.SimpleNamespace(date=None))
    return out, {s["symbol"]: s for s in out["symbols"]}


def test_the_ranked_path_never_selects_a_thin_name_however_it_scores(monkeypatch):
    out, by = _ranked(monkeypatch, dt.date(2026, 10, 28), _verdict(AAA="tradable", THIN="thin"))
    assert out["selected"] == ["AAA"]
    assert by["THIN"]["outcome"] == "not_tradable"
    assert by["THIN"]["reason"].startswith("not_tradable: thin")
    assert by["AAA"]["outcome"] == "selected"


def test_before_the_boundary_the_ranked_path_ranks_every_name(monkeypatch):
    out, _ = _ranked(monkeypatch, dt.date(2026, 10, 27), _verdict(AAA="tradable", THIN="thin"))
    assert sorted(out["selected"]) == ["AAA", "THIN"]


def test_a_missing_verdict_selects_nothing_and_every_row_says_why(monkeypatch):
    out, by = _ranked(monkeypatch, dt.date(2026, 10, 28), {"error": "no verdict file (verdicts.json)"})
    assert out["selected"] == []
    assert {s["outcome"] for s in by.values()} == {"liquidity_verdict_unavailable"}
