"""Item 31's study (scripts/earnings_item31.py): the end-of-day request files, the control pairing, the
replay of the module's own structures and management over invented chains, the fills and their
stresses, the nine arms, Holm, the pass rule, results.json as the board reads it, and the replay check
on paper entries. Invented events, chains and bars only: no Dolt, no store of real data.

Each rule and gate is shown firing and shown failing, by breaking the invariant it holds, because a
check that cannot fire reads as coverage it is not. The tests that run the module's functions need the
earnings package and are skipped without it (CI installs per job).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs, robustness

from cherrypick.backtest import chainfetch

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "earnings_item31.py"


def _module():
    spec = importlib.util.spec_from_file_location("earnings_item31_study", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e31 = _module()


@pytest.fixture
def earnings():
    return pytest.importorskip("cherrypick.earnings.management")


@pytest.fixture(autouse=True)
def few_resamples(monkeypatch):
    monkeypatch.setattr(e31, "RESAMPLES", 400)


# --------------------------------------------------------------------------- invented chains


def _store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    return conn


def _rows(
    spot: float,
    years: float,
    vol: float,
    *,
    call_vol: float | None = None,
    spread: float = 0.04,
    strikes=range(70, 131),
    volume: float = 100.0,
) -> list[dict]:
    """A chain at the close: Black-Scholes mids at zero rate, a quote `spread` wide (3% of mid when
    that is more), the bid never below zero. `call_vol` gives the calls their own vol (a skew)."""
    out = []
    for k in strikes:
        for right in ("C", "P"):
            v = call_vol if (right == "C" and call_vol is not None) else vol
            mid = bs.price(spot, float(k), years, v, right, 0.0)
            half = max(spread, 0.03 * mid) / 2
            out.append(
                {
                    "strike": float(k),
                    "right": right,
                    "bid": max(0.0, mid - half),
                    "ask": mid + half,
                    "close": mid,
                    "volume": volume,
                }
            )
    return out


def put_chain(conn, symbol: str, session: date, expiry: date, rows: list[dict]) -> None:
    chainfetch.write_chain(conn, (symbol, session.isoformat(), expiry.isoformat()), rows)


def _bars(symbol: str, first: date, last: date, close=100.0, volume=2_000_000.0, closes=None):
    """A close and volume for every session from `first` to `last`; `closes` maps a date to its close."""
    closes = closes or {}
    out, d = {}, first
    while d <= last:
        if e31.cal.is_trading_day(d):
            out[d] = (closes.get(d, close), volume)
        d += timedelta(days=1)
    return {symbol: out}


SYM = "TST"
ENTRY = date(2024, 3, 4)  # a Monday: the report after the close, the reaction Tuesday, the weekly Friday


class World:
    """A store, bars and a Study over them, with helpers to lay chains down."""

    def __init__(
        self, entry=ENTRY, closes=None, volume=2_000_000.0, close=100.0, cfg=None, moves=None, ivrv=True
    ):
        self.entry_date = entry
        self.front = e31.front_expiry(entry, "after_close", entry)
        self.back = e31.back_expiry(self.front, e31.weekly_candidates(entry))
        self.conn = _store()
        bars = _bars(SYM, entry - timedelta(days=130), self.front + timedelta(days=40), close, volume, closes)
        iv = {SYM: [(entry - timedelta(days=i), 1.0, 1.5) for i in range(1, 8)]} if ivrv else {}
        self.data = e31.MarketData(bars, iv)
        self.cfg = cfg or e31.study_config()
        self.study = e31.Study(self.data, e31.Chains(self.conn), self.cfg, moves or {})

    @property
    def sessions(self) -> list[date]:
        return e31.sessions_between(e31.cal.next_trading_day(self.entry_date), self.front)

    def event(self) -> dict:
        return e31.event_row(SYM, self.entry_date, "after_close")

    def lay_entry(self, front_vol=1.0, back_vol=0.5, spot=100.0, back=True, **kw):
        yf = (self.front - self.entry_date).days / 365.0
        put_chain(self.conn, SYM, self.entry_date, self.front, _rows(spot, yf, front_vol, **kw))
        if back:
            yb = (self.back - self.entry_date).days / 365.0
            put_chain(self.conn, SYM, self.entry_date, self.back, _rows(spot, yb, back_vol))

    def lay(self, session: date, vol: float, spot: float = 100.0, **kw):
        years = (self.front - session).days / 365.0
        put_chain(self.conn, SYM, session, self.front, _rows(spot, years if years > 0 else 1e-6, vol, **kw))

    def trade(self, strategy="iron_fly", role="event", control=None, **kw):
        return self.study.trade(strategy, self.event(), role, control, **kw)

    def managed(self, vols, strategy="iron_fly", spots=None, **kw):
        """Lay the entry and then one chain per later session at the given vols; return the record."""
        self.lay_entry()
        for s, v in zip(self.sessions, vols, strict=False):
            self.lay(s, v, (spots or {}).get(s, 100.0), **kw)
        return self.trade(strategy)


# --------------------------------------------------------------------------- expiries and controls


def test_weekly_candidates_use_the_thursday_when_the_friday_is_a_holiday():
    got = e31.weekly_candidates(date(2021, 3, 29), 14)  # Good Friday 2021-04-02
    assert date(2021, 4, 1) in got and date(2021, 4, 2) not in got


def test_the_front_expiry_is_the_weekly_on_or_after_the_reaction_date():
    thu, fri, mon = date(2024, 3, 7), date(2024, 3, 8), date(2024, 3, 4)
    assert e31.front_expiry(thu, "after_close", thu) == fri, "reaction is Friday, the same Friday"
    assert e31.front_expiry(fri, "after_close", fri) == date(2024, 3, 15), "reaction Saturday, next Friday"
    assert e31.front_expiry(mon, "before_open", date(2024, 3, 1)) == fri
    assert e31.front_expiry(mon, "after_close", mon) == fri


def test_front_and_back_expiry_are_the_modules_own_selection(earnings):
    from cherrypick.earnings import scanner

    for report in (
        date(2024, 3, 4),
        date(2024, 3, 7),
        date(2024, 3, 8),
        date(2021, 3, 31),
        date(2023, 12, 20),
    ):
        for timing in ("after_close", "before_open"):
            entry = e31.entry_session(report, timing)
            cands = e31.weekly_candidates(entry)
            front, _ = scanner.select_front_expiration(cands, report, e31.TIMING_TEXT[timing])
            assert e31.front_expiry(report, timing, entry) == front
            assert e31.back_expiry(front, cands) == scanner.select_back_expiration(cands, front, 21)


def _ev(report=date(2024, 3, 4), timing="after_close", symbol=SYM):
    return e31.event_row(symbol, report, timing)


SPAN = (date(2020, 1, 22), date(2026, 10, 1))


def test_the_control_is_the_same_weekday_four_weeks_earlier_with_the_same_days_to_expiry():
    ev = _ev()
    c = e31.control_for(ev, [date(2024, 3, 4)], SPAN)
    assert c["shift_days"] == -28 and c["entry_session"] == "2024-02-05"
    assert date.fromisoformat(c["entry_session"]).weekday() == ENTRY.weekday()
    dte_event = (e31.event_dates(ev)["front"] - ENTRY).days
    assert (
        date.fromisoformat(c["front_expiration"]) - date.fromisoformat(c["entry_session"])
    ).days == dte_event


def test_a_control_window_holding_a_report_moves_to_four_weeks_later_then_five_earlier_then_none():
    ev = _ev()
    own = date(2024, 3, 4)
    early = date(2024, 2, 6)  # a report inside the earlier window
    later = date(2024, 4, 1)
    far = date(2024, 1, 30)
    assert e31.control_for(ev, [own, early], SPAN)["shift_days"] == 28
    assert e31.control_for(ev, [own, early, later], SPAN)["shift_days"] == -35
    assert e31.control_for(ev, [own, early, later, far], SPAN) is None


def test_a_report_three_sessions_before_the_control_session_also_disqualifies_it():
    ev = _ev()
    three_before = date(2024, 1, 31)  # three sessions before 2024-02-05
    assert e31.control_for(ev, [three_before], SPAN)["shift_days"] == 28
    four_before = date(2024, 1, 30)
    assert e31.control_for(ev, [four_before], SPAN)["shift_days"] == -28


def test_a_control_the_calendar_cannot_show_clear_is_refused():
    ev = _ev(date(2020, 2, 10))
    assert e31.control_for(ev, [], (date(2020, 1, 22), date(2026, 10, 1)))["shift_days"] == 28
    assert e31.control_for(ev, [], (date(2020, 2, 1), date(2020, 3, 1))) is None
    late = _ev(date(2026, 9, 28))
    assert e31.control_for(late, [], (date(2020, 1, 22), date(2026, 10, 1))) is not None


def test_a_before_open_control_keeps_the_entry_the_session_before_the_report():
    ev = _ev(date(2024, 3, 5), "before_open")
    c = e31.control_for(ev, [], SPAN)
    assert c["entry_session"] == "2024-02-05" and c["report_date"] == "2024-02-06"
    assert e31.cal.previous_trading_day(date.fromisoformat(c["report_date"])) == date.fromisoformat(
        c["entry_session"]
    )
    friday = _ev(date(2024, 3, 11), "before_open")  # Monday report, Friday entry
    assert e31.control_for(friday, [], SPAN)["entry_session"] == "2024-02-09"


def test_the_calendar_document_keeps_a_blank_timing_report_so_it_blocks_a_control():
    doc = e31.calendar_document(
        [(SYM, date(2024, 3, 4), "After market close"), (SYM, date(2024, 2, 6), None)]
    )
    reports, span = e31.reports_from_document(doc)
    assert date(2024, 2, 6) in reports[SYM]
    assert e31.control_for(_ev(), reports[SYM], SPAN)["shift_days"] == 28


# --------------------------------------------------------------------------- the request files


def _events(n=3):
    return [_ev(date(2024, 3, 4) + timedelta(days=7 * i)) for i in range(n)]


def test_the_plan_asks_for_each_events_front_path_back_chain_and_control_path():
    ev = _ev()
    plan = e31.request_plan([ev], {SYM: [date(2024, 3, 4)]}, SPAN)
    d = e31.event_dates(ev)
    assert (SYM, d["entry"].isoformat(), d["back"].isoformat()) in plan["singles"]
    assert (SYM, d["front"].isoformat(), "2024-03-04", d["front"].isoformat()) in plan["paths"]
    control = e31.control_for(ev, [date(2024, 3, 4)], SPAN)
    cf = control["front_expiration"]
    assert (SYM, cf, control["entry_session"], cf) in plan["paths"]
    assert len(plan["singles"]) == 1 and len(plan["paths"]) == 2 and plan["no_control"] == 0


def test_the_plan_counts_an_event_with_no_control_and_asks_once_for_a_repeated_request():
    ev = _ev()
    bad = [date(2024, 3, 4), date(2024, 2, 6), date(2024, 4, 1), date(2024, 1, 30)]
    plan = e31.request_plan([ev, ev], {SYM: bad}, SPAN)
    assert plan["no_control"] == 2
    assert len(plan["paths"]) == 1, "no control path, and the event's own path once"


def test_what_is_stored_is_taken_off_the_plan_a_single_a_path_and_a_path_answered_session_by_session():
    ev = _ev()
    plan = e31.request_plan([ev], {SYM: [date(2024, 3, 4)]}, SPAN)
    conn = _store()
    d = e31.event_dates(ev)
    front, entry, back = d["front"], d["entry"], d["back"]
    assert len(e31.minus_stored(plan, e31.stored_requests(conn))["paths"]) == 2, "an empty store asks for all"
    chainfetch.write_chain(conn, (SYM, entry.isoformat(), back.isoformat()), [])  # empty counts as answered
    for s in e31.sessions_between(entry, front):
        chainfetch.write_chain(
            conn, (SYM, s.isoformat(), front.isoformat()), [{"strike": 100.0, "right": "C"}]
        )
    todo = e31.minus_stored(plan, e31.stored_requests(conn))
    assert todo["singles"] == [] and len(todo["paths"]) == 1, "the event's path is answered one by one"
    wide = (
        SYM,
        front.isoformat(),
        (entry - timedelta(days=10)).isoformat(),
        (front + timedelta(days=3)).isoformat(),
    )
    conn.execute("INSERT INTO eod_chain_paths VALUES (?, ?, ?, ?, 'complete', 1, 1, NULL, 0)", wide)
    cf = e31.control_for(ev, [date(2024, 3, 4)], SPAN)["front_expiration"]
    plan2 = {"singles": [], "paths": [(SYM, cf, "2024-02-05", cf)], "no_control": 0}
    assert len(e31.minus_stored(plan2, e31.stored_requests(conn))["paths"]) == 1, (
        "another expiry's path is no cover"
    )
    cover = {
        "singles": [],
        "paths": [(SYM, front.isoformat(), entry.isoformat(), front.isoformat())],
        "no_control": 0,
    }
    assert e31.minus_stored(cover, e31.stored_requests(conn))["paths"] == [], "a wider stored path covers it"


def test_the_request_files_are_what_fetch_chains_and_fetch_chain_paths_read(tmp_path):
    plan = e31.request_plan(_events(), {SYM: []}, SPAN)
    chains, paths = e31.request_files(e31.minus_stored(plan, {"singles": set(), "paths": set()}))
    a, b = tmp_path / "c.json", tmp_path / "p.json"
    a.write_text(json.dumps(chains), encoding="utf-8")
    b.write_text(json.dumps(paths), encoding="utf-8")
    assert len(chainfetch.read_requests(a)) == len(plan["singles"])
    assert len(chainfetch.read_path_requests(b)) == len(plan["paths"])


# --------------------------------------------------------------------------- the readings: G1, G2


def test_g1_needs_an_expected_move_of_1_2_times_the_mean_reaction_and_four_earlier_reports():
    moves = [(date(2023, 1, 1) + timedelta(days=90 * i), 0.05) for i in range(8)]
    report = date(2025, 1, 1)
    assert e31.g1_status(moves, report, 0.06) is True, "exactly 1.2 x 5% clears"
    assert e31.g1_status(moves, report, 0.0599) is False, "just under does not"
    assert e31.g1_status(moves[:3], report, 0.2) is None, "three earlier reports: no G1 trade"
    assert e31.g1_status(moves[:4], report, 0.2) is True
    assert e31.g1_status(moves, report, None) is None


def test_g1_reads_only_the_previous_eight_reports_before_the_event():
    moves = [(date(2022, 1, 1) + timedelta(days=90 * i), 0.01 if i < 4 else 0.10) for i in range(12)]
    report = moves[-1][0] + timedelta(days=90)
    assert e31.prior_moves(moves, report) == [0.10] * 8, "the four old, small moves are past the eight"
    assert e31.g1_status(moves, report, 0.12) is True and e31.g1_status(moves, report, 0.11) is False
    later_move = [*moves, (report + timedelta(days=1), 0.9)]
    assert e31.prior_moves(later_move, report) == [0.10] * 8, "a later report is never read"
    assert e31.prior_moves(moves, moves[5][0]) == [0.01] * 4 + [0.10], "five earlier reports: all of them"


def test_reaction_moves_use_the_entry_sessions_close_against_the_reaction_sessions_and_skip_a_split():
    data = e31.MarketData(
        {SYM: {date(2024, 3, 4): (100.0, 1), date(2024, 3, 5): (110.0, 1), date(2024, 3, 6): (50.0, 1)}}
    )
    got = e31.reaction_moves(SYM, [(date(2024, 3, 4), "After market close")], data)
    assert got == [(date(2024, 3, 4), pytest.approx(0.10))]
    split = e31.reaction_moves(SYM, [(date(2024, 3, 5), "After market close")], data)
    assert split == [], "a halving looks like a split and is left out"
    assert e31.reaction_moves(SYM, [(date(2024, 3, 4), None)], data) == [], "an unstated timing is not read"


def test_g2_passes_a_credit_four_times_its_crossing_cost_and_fails_a_thinner_one():
    quotes = [{"bid": 1.00, "ask": 1.05}, {"bid": 1.00, "ask": 1.05}, {"bid": 0.10, "ask": 0.12}]
    assert e31.crossing_cost(quotes) == pytest.approx(12.0)
    assert e31.g2_status(quotes, 0.48) is True, "12 is 25% of 48"
    assert e31.g2_status(quotes, 0.47) is False
    assert e31.g2_status(quotes, 0.0) is False
    wider = [{"bid": 1.00, "ask": 1.10}, {"bid": 1.00, "ask": 1.10}, {"bid": 0.10, "ask": 0.12}]
    assert e31.g2_status(wider, 0.48) is False, "widening the spreads breaks the gate"


# --------------------------------------------------------------------------- the module's own code


def test_the_fake_broker_windows_strikes_as_the_brokers_helper_does():
    broker = pytest.importorskip("cherrypick.core.broker")
    from types import SimpleNamespace

    strikes = [float(k) for k in range(70, 131)]
    for count, around in ((3, 100.2), (40, 100.0), (3, None), (15, 69.0), (3, 131.0)):
        opts = [SimpleNamespace(strike_price=k) for k in strikes]
        want = {o.strike_price for o in broker.atm_window(opts, count, around)}
        assert e31._window(strikes, count, around) == want


def test_the_replay_swaps_the_modules_readers_and_puts_them_back_even_on_an_error(earnings):
    from cherrypick.earnings import scanner
    from cherrypick.earnings.strategies import iron_fly

    before = (
        scanner.call_tt,
        scanner.fetch_iv_rv_ratio,
        scanner.compute_winrate,
        scanner.exit_basis,
        iron_fly.date,
    )
    with pytest.raises(RuntimeError), e31.replay_env(lambda a: {}, {"ok": True}, "mid", date(2024, 3, 4)):
        assert iron_fly.date.today() == date(2024, 3, 4), "'today' is the entry session"
        assert scanner.exit_basis({"opened_at": 0}) == "mid", "the fill is the replay's, not the row's clock"
        raise RuntimeError
    after = (
        scanner.call_tt,
        scanner.fetch_iv_rv_ratio,
        scanner.compute_winrate,
        scanner.exit_basis,
        iron_fly.date,
    )
    assert before == after


def test_the_deployed_thresholds_copied_here_are_what_the_modules_functions_need(earnings):
    from cherrypick.earnings.strategies import directional_credit_spread, iron_condor, iron_fly

    cfg = e31.study_config()
    full = {
        "price": 100.0, "term_structure": -0.1, "expected_move_dollars": 5.0, "expected_move_pct": 0.05,
        "atm_delta_abs": 0.5, "skew_abs": 0.05, "front_expiration_days": 4, "chain_complete": True,
        "combined_open_interest": 0, "bid_ask_spread_pct": 0.01, "has_weekly_options": True,
        "avg_volume": 2e6,
    }  # fmt: skip
    for name, mod in (("iron_fly", iron_fly), ("iron_condor", iron_condor),
                      ("directional_credit_spread", directional_credit_spread)):  # fmt: skip
        assert mod.apply_tiering(dict(full), cfg["strategies"][name]) == {
            "accepted": True,
            "reject_reasons": [],
        }
    assert cfg["strategies"]["iron_fly"]["profit_target_pct"] == 0.5
    assert cfg["strategies"]["iron_fly"]["stop_loss_credit_multiple"] == 1.5


def test_the_dollar_volume_bar_is_the_liquidity_verdicts():
    tradable = pytest.importorskip("cherrypick.technicals.tradable")
    assert e31.MIN_DOLLAR_VOLUME == tradable.MIN_DOLLAR_VOLUME


# --------------------------------------------------------------------------- admission


def test_the_iron_fly_is_built_at_the_money_with_wings_sized_from_the_credit(earnings):
    w = World()
    w.lay_entry()
    for s in w.sessions:
        w.lay(s, 0.5)
    r = w.trade("iron_fly")
    assert r["ok"] and r["admitted"] and r["reject"] == []
    shorts = sorted(k[6:] for k, side in r["strikes"].items() if side == "S")
    assert shorts == ["C00100000", "P00100000"], "both shorts at the money"
    longs = {k[6]: int(k[7:]) / 1000 for k, side in r["strikes"].items() if side == "B"}
    assert longs["C"] > 100 > longs["P"]
    assert r["capital_at_risk"] == pytest.approx(
        (max(longs["C"] - 100, 100 - longs["P"]) - r["credit"]) * 100, abs=1
    )


def test_the_iron_condor_sells_the_expected_move_boundaries(earnings):
    w = World()
    w.lay_entry()
    for s in w.sessions:
        w.lay(s, 0.5)
    r = w.trade("iron_condor")
    assert r["ok"] and r["admitted"]
    shorts = {k[6]: int(k[7:]) / 1000 for k, side in r["strikes"].items() if side == "S"}
    em = 100 * r["em_pct"]
    assert shorts["C"] == pytest.approx(100 + em, abs=1) and shorts["P"] == pytest.approx(100 - em, abs=1)


def test_the_directional_spread_sells_the_side_with_the_richer_25_delta_vol(earnings):
    for kw, side in (({"call_vol": 1.15}, "C"), ({"call_vol": 0.85}, "P")):
        w = World()
        w.lay_entry(**kw)
        for s in w.sessions:
            w.lay(s, 0.5)
        r = w.trade("directional_credit_spread")
        assert r["ok"] and r["admitted"], r["reject"]
        assert {k[6] for k, s_ in r["strikes"].items() if s_ == "S"} == {side}
        assert len(r["strikes"]) == 2


def test_a_skew_under_the_floor_is_refused_as_no_directional_signal(earnings):
    w = World()
    w.lay_entry()
    assert "insufficient_skew_signal" in w.trade("directional_credit_spread")["reject"]


def test_each_screen_refuses_when_its_invariant_is_broken(earnings):
    def refused(world, strategy="iron_fly", **kw):
        world.lay_entry(**kw)
        return world.trade(strategy)["reject"]

    assert "term_structure_insufficient" in refused(World(), back_vol=1.0)
    assert "expected_move_pct_below_minimum" in refused(World(), "iron_condor", front_vol=0.4)
    assert "expected_move_below_minimum" in refused(World(close=100), front_vol=0.01)
    assert "avg_volume_below_minimum" in refused(World(volume=1_000_000.0))
    assert "pit_dollar_volume" in refused(World(volume=400_000.0))
    wide = refused(World(), spread=1.0)
    assert "bid_ask_spread_too_wide" in wide and "pit_spread" in wide
    # the point-in-time spread (7.5%) is tighter than the module's own (15%)
    mid = refused(World(), spread=0.5)
    assert "pit_spread" in mid and "bid_ask_spread_too_wide" not in mid
    assert refused(World()) == [], "the unbroken chain is admitted"


def test_a_price_under_ten_dollars_is_refused(earnings):
    w = World(close=8.0)
    w.lay_entry(spot=8.0, strikes=range(4, 13))
    assert "price_below_minimum" in w.trade()["reject"]


def test_the_volume_and_open_interest_floors_are_not_reproduced_and_say_so(earnings):
    w = World()
    w.lay_entry(volume=1.0)  # a handful of contracts in the whole chain
    r = w.trade()
    assert r["admitted"] and r["front_volume"] < 500, "the option volume floor is not applied"
    assert e31.DEFAULTS["min_combined_open_interest"] == 0
    assert e31.SCREEN["combined_option_volume"] == "off"


def test_missing_chains_and_bars_drop_the_event_with_a_reason(earnings):
    assert World().trade()["drop"] == "no_front_chain"
    w = World()
    w.lay_entry(back=False)
    assert w.trade()["drop"] == "no_back_chain"
    w2 = World(closes={ENTRY: 0.0})
    w2.lay_entry()
    assert w2.trade()["drop"] == "no_underlying_bar"


def test_a_structure_over_the_paper_budget_is_skipped_and_counted(earnings):
    w = World(cfg={**e31.study_config(), "available_capital_paper_mode": 1000})
    w.lay_entry()
    r = w.trade()
    assert r["admitted"] and r["drop"] == "over_budget" and not r["ok"]


def test_the_control_is_built_where_the_screens_would_refuse_the_event(earnings):
    w = World()
    w.lay_entry(front_vol=0.4, back_vol=0.4, back=False)
    for s in w.sessions:
        w.lay(s, 0.3)
    event = w.trade("iron_condor")
    assert event["drop"] == "no_back_chain"
    control = {"report_date": "2024-03-04", "entry_session": "2024-03-04", "timing": "after_close"}
    r = w.trade("iron_condor", role="control", control=control)
    assert r["ok"] and r["role"] == "control" and r["admitted"] is None, "no screen was applied"


# --------------------------------------------------------------------------- management and fills


def test_a_profit_target_closes_the_first_morning_on_the_working_price(earnings):
    r = World().managed([0.45, 0.4, 0.3, 0.2])
    ex = r["exits"]["managed"]
    assert ex["reason"] == "profit_target" and ex["session"] == "2024-03-05" and ex["hold_sessions"] == 1
    assert ex["net"] > 0


def test_a_stop_closes_at_one_and_a_half_times_the_credit(earnings):
    w = World()
    r = w.managed([0.6, 0.6, 0.6, 0.6], spots={s: 120.0 for s in w.sessions})
    ex = r["exits"]["managed"]
    assert ex["reason"] == "stop_loss" and ex["net"] < 0


def test_a_loser_below_the_stop_closes_the_first_morning_at_the_pead_gate_and_a_winner_does_not(earnings):
    w = World()
    r = w.managed([0.8, 0.8, 0.8, 0.8], spots={s: 108.0 for s in w.sessions})
    assert r["exits"]["managed"]["reason"] == "pead_loser"
    assert r["exits"]["managed"]["session"] == "2024-03-05"
    # broken on purpose: with the gate off the same path carries the loser, and a shallow winner is
    # carried by the gate on
    assert r["exits"]["no_pead"]["reason"] != "pead_loser"
    won = World().managed([1.05, 1.05, 1.05, 1.05])
    assert won["exits"]["managed"]["reason"] != "pead_loser"


def test_a_carried_winner_is_closed_on_the_third_later_session(earnings):
    r = World().managed([1.05, 1.05, 1.05, 1.05])
    ex = r["exits"]["managed"]
    assert ex["reason"] == "max_hold" and ex["hold_sessions"] == 3 and ex["session"] == "2024-03-07"


def test_an_expiry_day_closes_at_its_end_of_day_mark_even_on_a_wide_market(earnings):
    entry = date(2024, 3, 5)  # Tuesday: the expiry is the third later session
    w = World(entry=entry)
    w.lay_entry()
    sessions = w.sessions
    assert sessions[-1] == w.front and len(sessions) == 3
    for s in sessions[:-1]:
        w.lay(s, 1.05)
    w.lay(sessions[-1], 1.05, spread=0.5)  # wings quoted wide: the spread gate would refuse any other close
    ex = w.trade()["exits"]["managed"]
    assert ex["reason"] == "expiry_close" and ex["session"] == w.front.isoformat()


def test_a_close_the_spread_gate_refuses_waits_for_the_next_mark_but_the_expiry_close_does_not(earnings):
    w = World()
    w.lay_entry()
    s = w.sessions
    w.lay(s[0], 0.4, spread=0.5)  # the target is met, the wings quoted wide
    for later in s[1:]:
        w.lay(later, 0.3)
    ex = w.trade()["exits"]["managed"]
    assert ex["reason"] == "profit_target" and ex["session"] == s[1].isoformat(), "waited one mark"
    # broken on purpose: tight quotes the first morning and it closes at once
    tight = World().managed([0.4, 0.3, 0.3, 0.3])["exits"]["managed"]
    assert tight["session"] == s[0].isoformat()


def test_a_leg_without_a_quote_is_marked_at_its_last_earlier_quote_and_counted(earnings):
    w = World()
    w.lay_entry()
    s = w.sessions
    w.lay(s[0], 1.0, strikes=range(90, 111))  # the 125 and 75 wings have no quote today
    for later in s[1:]:
        w.lay(later, 1.0)
    ex = w.trade()["exits"]["held_to_expiry"]
    assert ex["stale_marks"] == 2, "the two wings, once"
    full = World()
    full.lay_entry()
    for later in full.sessions:
        full.lay(later, 1.0)
    assert full.trade()["exits"]["held_to_expiry"]["stale_marks"] == 0, "a full chain carries nothing"


def test_an_expiry_day_with_no_rows_settles_at_intrinsic_with_the_settlement_fee(earnings):
    w = World(closes={date(2024, 3, 8): 110.0})
    w.lay_entry()
    for s in w.sessions[:-1]:
        w.lay(s, 1.05)
    ex = w.trade()["exits"]["held_to_expiry"]
    assert ex["reason"] == "settled" and ex["settlement"] == 5.0, "one in-the-money symbol, $5"
    assert ex["exit_fees"] == 0 and ex["exit_slippage"] == 0, "nothing was traded to close"
    assert ex["gross"] == pytest.approx((w.trade()["credit"] - 10.0) * 100)


def test_a_split_on_the_first_later_session_drops_the_trade_and_a_later_one_ends_it_the_session_before(
    earnings,
):
    s0 = date(2024, 3, 5)
    w = World(closes={s0: 50.0})
    w.lay_entry()
    assert w.trade()["drop"] == "split_on_first_session"
    w2 = World(closes={date(2024, 3, 7): 50.0})
    w2.lay_entry()
    for s in w2.sessions:
        w2.lay(s, 1.05)
    ex = w2.trade()["exits"]["managed"]
    assert ex["reason"] == "split" and ex["session"] == "2024-03-06"


def test_the_net_columns_add_up_and_the_fills_and_stress_read_as_declared(earnings):
    r = World().managed([0.45, 0.4, 0.3, 0.2])
    ex = r["exits"]["managed"]
    parts = ex["gross"] - ex["entry_fees"] - ex["entry_slippage"] - ex["exit_fees"] - ex["exit_slippage"]
    assert ex["net"] == pytest.approx(parts - ex["settlement"])
    assert ex["nets"]["mid_fees"] == pytest.approx(ex["gross"] - ex["entry_fees"] - ex["exit_fees"])
    assert ex["nets"]["mid_fees"] > ex["nets"]["working"], "the haircut is a cost on the working price"
    assert ex["nets"]["stress"] < ex["nets"]["working"], "1.5x fees and a 25% haircut cost more"
    assert ex["entry_fees"] == pytest.approx(4 * 1.0 + 4 * 0.14), "$1 a contract to open, the pass-throughs"
    assert ex["exit_fees"] == pytest.approx(4 * 0.14), "none to close"
    assert ex["share"] == pytest.approx(ex["net"] / r["capital_at_risk"])
    assert ex["nets"]["natural"] != ex["nets"]["working"]


def test_the_stress_config_scales_fees_and_the_haircut_and_nothing_else():
    cfg = e31.study_config()
    s = e31.stress_config(cfg)["tastytrade_costs"]
    assert s["commission_open_per_contract"] == 1.5 and s["commission_cap_per_leg"] == 15.0
    assert s["clearing_fee_per_contract"] == pytest.approx(0.15)
    assert s["slippage_frac_of_spread"] == 0.25 and s["slippage_cap_frac_of_mid"] == 0.15
    assert cfg["tastytrade_costs"]["slippage_frac_of_spread"] == 0.125, "the working config is untouched"


def test_the_exit_readings_are_the_first_morning_the_expiry_close_and_the_ungated_rule(earnings):
    r = World().managed([1.05, 1.05, 1.05, 1.05])
    ex = r["exits"]
    assert ex["first_morning"]["session"] == "2024-03-05" and ex["first_morning"]["hold_sessions"] == 1
    assert ex["held_to_expiry"]["session"] == "2024-03-08" and ex["held_to_expiry"]["hold_sessions"] == 4
    assert set(ex) == set(e31.EXIT_READINGS)


# --------------------------------------------------------------------------- the whole replay


def test_the_replay_builds_the_event_and_its_control_and_stubs_a_missing_control(earnings):
    w = World()
    w.lay_entry()
    for s in w.sessions:
        w.lay(s, 0.45)
    ev = w.event()
    c = e31.control_for(ev, [date(2024, 3, 4)], SPAN)
    cs, cf = date.fromisoformat(c["entry_session"]), date.fromisoformat(c["front_expiration"])
    yf = (cf - cs).days / 365.0
    put_chain(
        w.conn, SYM, cs, cf, _rows(100.0, yf, 0.6)
    )  # the control session has an ordinary, cheaper chain
    for s in e31.sessions_between(e31.cal.next_trading_day(cs), cf):
        put_chain(w.conn, SYM, s, cf, _rows(100.0, max((cf - s).days, 0) / 365.0 or 1e-6, 0.5))
    w.study.data.bars[SYM].update({d: (100.0, 2_000_000.0) for d in e31.sessions_between(cs, cf)})
    w.study.data._days[SYM] = sorted(w.study.data.bars[SYM])
    recs = e31.run_study([ev], {SYM: [date(2024, 3, 4)]}, SPAN, w.study)
    by = {(r["strategy"], r["role"]): r for r in recs}
    assert by[("iron_fly", "event")]["ok"] and by[("iron_fly", "control")]["ok"]
    ctl = by[("iron_fly", "control")]
    assert ctl["entry_session"] == c["entry_session"] and ctl["event_report"] == ev["report_date"]
    assert ctl["credit"] < by[("iron_fly", "event")]["credit"], "the control's own quotes set its credit"
    assert all(r["role"] == "event" for r in recs if r["strategy"] == "directional_credit_spread"), (
        "an event that was not built gets no control"
    )
    blocked = [date(2024, 3, 4), date(2024, 2, 6), date(2024, 4, 1), date(2024, 1, 30)]
    none = e31.run_study([ev], {SYM: blocked}, SPAN, w.study)
    stub = [r for r in none if r["role"] == "control" and r["ok"] is False]
    assert stub and {r["drop"] for r in stub} == {"no_control_session"}


# --------------------------------------------------------------------------- synthetic records


def _names(half: int, n: int) -> list[str]:
    out, i = [], 0
    while len(out) < n:
        s = f"N{i:03d}"
        if robustness.name_half(s) == half:
            out.append(s)
        i += 1
    return out


NAMES = [x for pair in zip(_names(0, 40), _names(1, 40), strict=True) for x in pair]


def _sessions(start: date, n: int, step: int = 5) -> list[date]:
    out, d = [], start
    while len(out) < n:
        out.append(d)
        for _ in range(step):
            d = e31.cal.next_trading_day(d)
    return out


def rec(symbol, session: date, value, *, strategy="iron_fly", role="event", window="design", g1=True, g2=True,
        stress=None, over=None):  # fmt: skip
    ex = {
        "session": session.isoformat(), "reason": "profit_target", "hold_sessions": 1, "stale_marks": 0,
        "net": value * 1000.0, "share": value, "shares": {f: value for f in e31.FILL_READINGS},
    }  # fmt: skip
    if stress is not None:
        ex["shares"]["stress"] = stress
    out = {
        "symbol": symbol, "strategy": strategy, "role": role, "event_report": session.isoformat(),
        "report_date": session.isoformat(), "entry_session": session.isoformat(), "timing": "after_close",
        "year": session.year, "window": window, "time_half": 0 if session <= e31.HALF_END else 1,
        "name_half": robustness.name_half(symbol), "ok": True, "drop": None, "admitted": True, "reject": [],
        "g1": g1, "g2": g2, "em_pct": 0.05, "term": -0.1, "atm_spread_pct": 0.02, "dispersion": 0.05,
        "exits": {r: {**ex, "shares": dict(ex["shares"])} for r in e31.EXIT_READINGS},
    }  # fmt: skip
    return {**out, **(over or {})}


def pairs_for(n, event_value, control_value, *, start=date(2020, 2, 3), strategy="iron_fly", window="design",
              step=5, **kw):  # fmt: skip
    """n events, one a session, each with a control: values are functions of (i, symbol)."""
    out = []
    for i, s in enumerate(_sessions(start, n, step)):
        sym = NAMES[i % len(NAMES)]
        out.append(rec(sym, s, event_value(i, sym), strategy=strategy, window=window, **kw))
        out.append(rec(sym, s, control_value(i, sym), strategy=strategy, role="control", window=window, **kw))
    return out


def noise(i):
    return ((i * 37) % 11 - 5) / 100.0


def winning(i, sym):
    return 0.05 + noise(i)


def flat(i, sym):
    return noise(i)


HOLD = date(2024, 1, 3)


def passing_records(**kw):
    return pairs_for(160, winning, flat, **kw) + pairs_for(
        110, winning, flat, start=HOLD, window="holdout", step=4
    )


def verdict_of(records, arm="iron_fly"):
    return e31.evaluate_arms(e31.index_records(records))[arm]


def test_a_winning_arm_passes_the_design_checks_and_the_holdout():
    v = verdict_of(passing_records())
    assert v["checks"] and all(v["checks"].values()), v["checks"]
    assert v["verdict"] == "PASS" and v["design"]["pairs"] == 160


def test_the_holdout_is_read_and_a_negative_holdout_fails_a_design_pass():
    recs = pairs_for(160, winning, flat) + pairs_for(
        110, lambda i, s: -0.05 + noise(i), flat, start=HOLD, window="holdout", step=4
    )
    v = verdict_of(recs)
    assert v["design_pass"] and v["verdict"] == "FAIL" and v["holdout_ok"] is False


def test_a_holdout_under_a_hundred_pairs_is_no_verdict_and_so_is_a_design_under_a_hundred_and_fifty():
    short = pairs_for(160, winning, flat) + pairs_for(99, winning, flat, start=HOLD, window="holdout", step=4)
    assert verdict_of(short)["verdict"] == "NO VERDICT"
    assert verdict_of(pairs_for(149, winning, flat))["verdict"] == "NO VERDICT"
    assert verdict_of(pairs_for(150, winning, flat))["enough_design"] is True


def test_net_positive_in_each_name_half_is_required():
    def lopsided(i, sym):
        return winning(i, sym) if robustness.name_half(sym) == 0 else -0.08 + noise(i)

    v = verdict_of(pairs_for(160, lopsided, flat))
    assert v["checks"]["net_each_name_half"] is False and v["verdict"] == "FAIL"


def test_net_positive_in_each_time_half_is_required():
    def early_only(i, sym):
        return winning(i, sym) if i < 60 else -0.06 + noise(i)

    v = verdict_of(pairs_for(160, early_only, flat))
    assert v["checks"]["net_each_time_half"] is False


def test_the_paired_difference_must_be_positive_in_each_time_half():
    def pair_flips(i, sym):
        return 0.06 + noise(i)

    def control(i, sym):
        return 0.02 + noise(i) if i < 80 else 0.09 + noise(i)

    v = verdict_of(pairs_for(160, pair_flips, control))
    assert v["checks"]["pair_each_time_half"] is False


def test_the_stress_must_stay_positive():
    v = verdict_of(pairs_for(160, winning, flat, stress=-0.02))
    assert v["checks"]["stress_positive"] is False and v["verdict"] == "FAIL"


def test_the_net_must_survive_losing_the_best_five_trades():
    def tail(i, sym):
        return 3.0 if i < 5 else -0.02 + noise(i) / 10

    v = verdict_of(pairs_for(160, tail, flat))
    assert v["checks"]["best_five_removed_positive"] is False
    assert e31.drop_best([1, 5, 2, 9, 3, 7, 4], 5) == [1, 2], "the five largest go"
    assert e31.drop_best([1.0, 2.0], 5) == []


def test_net_positive_with_a_control_that_earns_as_much_is_pays_not_for_the_report():
    v = verdict_of(pairs_for(160, winning, winning))
    assert v["checks"]["net_t_holm"] and not v["checks"]["pair_t_holm"]
    assert v["verdict"] == "FAIL" and v["reading"] == "pays, not for the report"


def test_a_pair_that_is_positive_while_the_net_is_not_is_the_costs_eating_the_report():
    v = verdict_of(pairs_for(160, lambda i, s: -0.02 + noise(i) / 5, lambda i, s: -0.09 + noise(i) / 5))
    assert v["checks"]["pair_t_holm"] and not v["checks"]["net_t_holm"]
    assert v["reading"] == "the report is worth something but the costs eat it"


def test_holm_across_nine_arms_refuses_a_p_that_would_pass_alone(monkeypatch):
    def fake(values, clusters, **kw):
        n = len(values)
        return {"n": n, "sessions": n, "mean": 0.05 if n else None, "t": 3.0, "p": 0.01}

    monkeypatch.setattr(e31, "cluster_t", fake)
    recs = passing_records()
    arms = e31.evaluate_arms(e31.index_records(recs))
    # p = 0.01 is under 0.05 but not under 0.05 / 9 for the smallest of nine
    assert arms["iron_fly"]["checks"]["net_t_holm"] is False
    monkeypatch.setattr(e31, "cluster_t", lambda v, c, **kw: {**fake(v, c), "p": 0.001})
    assert e31.evaluate_arms(e31.index_records(recs))["iron_fly"]["checks"]["net_t_holm"] is True


def test_an_arm_under_the_minimum_counts_p_of_one_in_the_family(monkeypatch):
    seen = {}
    real = e31.core_stats.holm

    def spy(p, alpha=0.05):
        seen.setdefault("p", dict(p))
        return real(p, alpha)

    monkeypatch.setattr(e31.core_stats, "holm", spy)
    verdict_of(pairs_for(120, winning, flat))
    assert len(seen["p"]) == 9 and set(seen["p"].values()) == {1.0}, "nine arms, 120 pairs is under 150"


def test_cluster_t_clusters_by_session_seeds_and_reads_a_flat_series():
    vals = [0.1, 0.12, 0.08, 0.11, 0.09, 0.1] * 5
    spread = [f"s{i}" for i in range(len(vals))]
    few = [f"s{i % 3}" for i in range(len(vals))]
    a, b = e31.cluster_t(vals, spread), e31.cluster_t(vals, few)
    assert a["mean"] == pytest.approx(b["mean"]) and a["t"] > 5 and a["p"] < 0.05
    assert b["sessions"] == 3 and b["t"] < a["t"], "thirty trades on three nights are three observations"
    assert e31.cluster_t(vals, spread) == a, "seeded"
    zero = e31.cluster_t([0.0] * 6, ["a", "b", "c", "a", "b", "c"])
    assert zero["t"] == 0 and zero["p"] == 1.0
    assert e31.cluster_t([], [])["mean"] is None
    flat_pos = e31.cluster_t([0.1] * 6, ["a", "b", "c", "d", "e", "f"])
    assert flat_pos["t"] == float("inf") and flat_pos["p"] < 0.05


def test_the_net_is_judged_on_every_closed_event_and_the_pair_only_on_events_with_a_closed_control():
    recs = pairs_for(160, winning, flat)
    idx = e31.index_records(recs)
    events, pairs = e31.arm_sample(idx, "iron_fly", "", "design")
    assert len(events) == len(pairs) == 160
    broken = [r for r in recs if not (r["role"] == "control" and r["entry_session"] < "2020-06-01")]
    ev2, pr2 = e31.arm_sample(e31.index_records(broken), "iron_fly", "", "design")
    assert len(ev2) == 160 and len(pr2) < 160, "an unpaired event stays in the net reading"


def test_g1_keeps_events_that_clear_it_and_their_controls_keep_the_events_status():
    recs = []
    for i, s in enumerate(_sessions(date(2020, 2, 3), 20)):
        sym = NAMES[i]
        recs.append(rec(sym, s, 0.05, g1=i % 2 == 0))
        recs.append(rec(sym, s, 0.0, role="control", g1=False))  # the control's own flag is not read
    events, pairs = e31.arm_sample(e31.index_records(recs), "iron_fly", "g1", "design")
    assert len(events) == len(pairs) == 10
    none = [{**r, "g1": None} for r in recs]
    assert e31.arm_sample(e31.index_records(none), "iron_fly", "g1", "design") == ([], [])


def test_g2_is_applied_to_the_control_on_its_own_quotes():
    recs = []
    for i, s in enumerate(_sessions(date(2020, 2, 3), 10)):
        sym = NAMES[i]
        recs.append(rec(sym, s, 0.05, g2=True))
        recs.append(rec(sym, s, 0.0, role="control", g2=i < 6))
    events, pairs = e31.arm_sample(e31.index_records(recs), "iron_fly", "g2", "design")
    assert len(events) == 10 and len(pairs) == 6, "four controls fail G2: their events lose the pair"
    ungated, ungated_pairs = e31.arm_sample(e31.index_records(recs), "iron_fly", "", "design")
    assert len(ungated_pairs) == 10


def _half_rich(n, start, window, step):
    """Every third event clears G1 and wins; the rest lose. The control earns nothing. (Every third,
    because the names alternate name-halves and every other would put the winners in one half.)"""
    out = []
    for i, s in enumerate(_sessions(start, n, step)):
        sym, rich = NAMES[i % len(NAMES)], i % 3 == 0
        out.append(rec(sym, s, 0.06 + noise(i) if rich else -0.07 + noise(i), window=window, g1=rich))
        out.append(rec(sym, s, noise(i), role="control", window=window, g1=rich))
    return out


def test_a_gated_arm_that_passes_when_its_ungated_arm_fails_is_recorded_as_the_gates_pass():
    recs = _half_rich(540, date(2020, 2, 3), "design", 2) + _half_rich(330, HOLD, "holdout", 2)
    arms = e31.evaluate_arms(e31.index_records(recs))
    assert arms["iron_fly"]["verdict"] == "FAIL", "the losers drag the ungated arm under"
    assert arms["iron_fly+g1"]["verdict"] == "PASS"
    assert arms["iron_fly+g1"]["gate_passes_ungated_fails"] is True
    assert arms["iron_fly+g2"]["verdict"] == "FAIL", "G2 is on for every trade here, so it is the ungated arm"
    # broken on purpose: when the ungated arm passes too, the flag is off
    good = e31.evaluate_arms(e31.index_records(passing_records()))
    assert good["iron_fly+g1"]["gate_passes_ungated_fails"] is False


def test_the_wide_name_cap_the_top_five_and_the_covid_months_are_reported():
    events = [rec("AAA", s, 0.05) for s in _sessions(date(2020, 2, 3), 60, 2)] + [
        rec(NAMES[i], s, -0.01) for i, s in enumerate(_sessions(date(2021, 1, 4), 40))
    ]
    c = e31.concentration(events)
    assert c["largest_name"] == "AAA" and c["largest_share"] == pytest.approx(0.6)
    assert c["names_above_cap"] == 1 and c["top5_share"] == pytest.approx(0.64)
    assert c["mean_without_top5"] == pytest.approx(-0.01), "the five most frequent names are taken out"
    r = e31.reported_readings(events, [], set(), None)
    assert r["without_2020_02_to_06"] != r["fills"]["working"], "the spring of 2020 is taken out"
    assert r["macro"]["cpi"] is None, "no CPI calendar was supplied"


def test_the_macro_week_split_reads_fomc_and_cpi_weeks():
    fomc, cpi = {date(2024, 3, 20)}, {date(2024, 3, 12)}
    assert e31.macro_week(date(2024, 3, 18), fomc, cpi) == {"fomc": True, "cpi": False}
    assert e31.macro_week(date(2024, 3, 11), fomc, cpi) == {"fomc": False, "cpi": True}
    assert e31.macro_week(date(2024, 3, 11), fomc, None)["cpi"] is None
    events = [rec(NAMES[0], date(2024, 3, 19), 0.1), rec(NAMES[1], date(2024, 3, 12), -0.1)]
    m = e31.macro_split(events, fomc, cpi)
    assert m["fomc"]["in_week"]["mean"] == pytest.approx(0.1) and m["cpi"]["in_week"][
        "mean"
    ] == pytest.approx(-0.1)


def test_terciles_and_the_event_premium_check():
    got = e31.tercile_means([(float(i), float(i)) for i in range(9)])
    assert got == [1.0, 4.0, 7.0]
    assert e31.tercile_means([(1.0, 1.0)]) == [None, None, None]
    day = date(2024, 1, 2)
    pairs = [
        (rec("A", day, 0, over={"em_pct": a}), rec("A", day, 0, over={"em_pct": b}))
        for a, b in ((0.05, 0.06), (0.05, 0.04), (0.05, 0.05))
    ]
    assert e31._event_premium(pairs) == pytest.approx(2 / 3), "equal or lower is not richer"


def test_drops_are_counted_by_reason_and_year_and_a_gap_between_the_halves_is_flagged():
    recs = []
    for i, s in enumerate(_sessions(date(2020, 2, 3), 80, 10)):
        dropped = (i < 40 and i % 10 == 0) or (i >= 40 and i % 2 == 0)
        recs.append(
            rec(NAMES[i], s, 0.0, over={"ok": not dropped, "drop": "no_front_chain" if dropped else None})
        )
    out = e31.drop_summary(recs)
    gap = out["paired_halves"]["iron_fly"]
    assert gap["flag"] is True and gap["gap"] > 0.05
    assert any("no_front_chain" in k for k in out["by_reason"]) and out["by_year"]


# --------------------------------------------------------------------------- results.json, the board


def test_results_json_carries_a_verdict_for_each_arm_and_a_window_the_board_reads():
    orch = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    result = e31.clean(e31.summarise_results(passing_records(), set(), None))
    json.dumps(result)  # no infinity or NaN survives
    words = orch.verdicts(result)
    assert sorted(w["arm"] for w in words) == sorted(e31.ARMS) and len(words) == 9
    assert {w["verdict"] for w in words} <= {"pass", "fail", "no verdict"}
    assert next(w for w in words if w["arm"] == "iron_fly")["verdict"] == "pass"
    assert isinstance(result["window"], str) and result["basis"] == "end_of_day"


def test_clean_turns_infinity_and_nan_into_none():
    assert e31.clean({"a": float("inf"), "b": [float("nan"), 1.0], "c": (2,)}) == {
        "a": None,
        "b": [None, 1.0],
        "c": [2],
    }


def test_report_renders_every_arm_the_checks_and_the_limits():
    result = e31.clean(e31.summarise_results(passing_records(), set(), None))
    md = e31.report(result)
    for arm in e31.ARMS:
        assert f"| {arm} |" in md
    assert "Design checks" in md and "Declared limits" in md and "end-of-day" in md.lower()
    assert "**Arms passing:** iron_fly" in md


def test_the_declared_limits_name_the_floors_that_are_not_reproduced():
    text = " ".join(e31.LIMITS).lower()
    assert "open interest" in text and "dispersion" in text and "hindsight" in text and "monday" in text


# --------------------------------------------------------------------------- the replay check


def _ledger(tmp_path) -> Path:
    path = tmp_path / "paper.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (order_id TEXT, strategy TEXT, symbol TEXT, expiration TEXT, legs_json TEXT, "
        "entry_credit REAL, opened_at REAL, arm TEXT, pnl REAL, exit_debit REAL, status TEXT)"
    )
    legs = json.dumps([{"symbol": "TST   240308C00100000", "action": "Sell to Open"}])
    opened = e31.datetime.combine(date(2026, 8, 3), e31.time(15, 40), e31.ET).timestamp()
    insert = "INSERT INTO trades VALUES (?, 'iron_fly', 'TST', '2026-08-07', ?, 2.5, ?, ?, ?, ?, 'closed')"
    # the first row's outcome is poisoned: it must never reach the check
    conn.execute(insert, ("a", legs, opened, "strat_test:iron_fly", 999.0, 7.0))
    conn.execute(insert, ("b", legs, opened, "advised:x:iron_fly", 1, 1))
    conn.execute(insert, ("c", legs, opened - 90 * 86400, "strat_test:iron_fly", 1, 1))
    conn.commit()
    conn.close()
    return path


def test_the_replay_check_reads_entry_columns_only_and_only_the_control_arms_in_the_window(tmp_path):
    rows = e31.paper_entries(_ledger(tmp_path))
    assert len(rows) == 1, "the advised twin and the entry before 2026-07-15 are not read"
    assert set(rows[0]) == {"symbol", "strategy", "session", "expiration", "legs", "credit"}
    assert rows[0]["session"] == "2026-08-03"
    assert not set(e31.ENTRY_COLUMNS) & set(e31.OUTCOME_COLUMNS)


def test_the_entries_only_guard_fires_on_an_outcome_column():
    e31.assert_entry_only(e31.ENTRY_COLUMNS)
    for col in ("pnl", "exit_debit", "closed_at", "status", "exit_reason"):
        with pytest.raises(ValueError, match="entries only"):
            e31.assert_entry_only((*e31.ENTRY_COLUMNS, col))


def test_the_entries_only_guard_stands_between_the_reader_and_a_planted_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(e31, "ENTRY_COLUMNS", (*e31.ENTRY_COLUMNS, "pnl"))
    with pytest.raises(ValueError, match="entries only"):
        e31.paper_entries(_ledger(tmp_path))


def test_an_entry_agrees_when_the_structure_matches_and_the_credit_is_within_a_tick():
    paper = {"legs": [{"symbol": "TST   240308C00100000", "action": "Sell to Open"}], "credit": 2.50}
    same = {"legs": paper["legs"], "credit": 2.51}
    assert e31.compare_entry(paper, same) is None
    assert e31.compare_entry(paper, {**same, "credit": 2.52}) == "credit"
    other = {"legs": [{"symbol": "TST   240308C00105000", "action": "Sell to Open"}], "credit": 2.5}
    assert e31.compare_entry(paper, other) == "structure"
    assert e31.compare_entry(paper, None) == "not_built"


def test_the_check_stops_the_study_under_twenty_events_or_ninety_percent():
    def results(n, bad):
        return [{"disagreement": "credit" if i < bad else None} for i in range(n)]

    assert e31.evaluate_replay_check(results(20, 2))["ok"] is True, "18 of 20 is 90%"
    assert e31.evaluate_replay_check(results(20, 3))["ok"] is False, "17 of 20 is under 90%"
    assert e31.evaluate_replay_check(results(19, 0))["ok"] is False, "19 events is under the minimum"
    assert e31.evaluate_replay_check([])["ok"] is False


def test_replay_events_match_a_paper_entry_to_a_stated_timing_report_by_its_entry_session():
    entries = [{"symbol": "TST", "session": "2026-08-03"}, {"symbol": "TST", "session": "2026-09-01"}]
    rows = {"TST": [(date(2026, 8, 3), "After market close"), (date(2026, 9, 1), None)]}
    got = e31.replay_events(entries, rows)
    assert len(got) == 1 and got[0][1]["entry_session"] == "2026-08-03"


def test_an_entry_only_replay_builds_the_order_and_compares_to_a_paper_entry(earnings):
    w = World()
    w.lay_entry()
    r = w.trade(role="replay", entry_only=True)
    assert r["ok"] and r["built"]["credit"] > 0 and len(r["built"]["legs"]) == 4
    assert "exits" not in r, "no later session is read"
    paper = {"legs": r["built"]["legs"], "credit": r["built"]["credit"] + 0.005}
    assert e31.compare_entry(paper, r["built"]) is None
    assert e31.compare_entry({**paper, "credit": r["built"]["credit"] + 0.05}, r["built"]) == "credit"
    paper_other = {"legs": r["built"]["legs"][:2], "credit": r["built"]["credit"]}
    assert e31.compare_entry(paper_other, r["built"]) == "structure"


def test_the_fake_broker_answers_the_three_commands_and_refuses_the_rest(earnings):
    w = World()
    w.lay_entry()
    tt = e31.FakeTT(w.study.chains, SYM, ENTRY, 100.0, e31.weekly_candidates(ENTRY))
    assert tt(["get_quote", "--symbol", SYM])["price"] == 100.0
    assert w.front.isoformat() in tt(["get_option_chain", "--symbol", SYM])["chain"]
    one = tt(["get_option_chain", "--symbol", SYM, "--expiration", w.front.isoformat(), "--include_greeks",
              "--include_quotes", "--strike_count", "3", "--around_price", "100.0"])  # fmt: skip
    atm = [e for e in one["chain"][w.front.isoformat()] if e["strike_price"] == 100.0]
    assert len(one["chain"][w.front.isoformat()]) == 14 and {e["option_type"] for e in atm} == {"C", "P"}
    call = next(e for e in atm if e["option_type"] == "C")
    assert call["iv"] == pytest.approx(1.0, abs=0.02) and 0.45 < call["delta"] < 0.6 and call["mid"] > 0
    assert tt(["get_option_chain", "--symbol", SYM, "--expiration", "2030-01-04"])["ok"] is False
    assert tt(["get_option_chain", "--symbol", SYM, "--include_quotes"])["ok"] is False
    assert tt(["get_market_metrics", "--symbol", SYM])["ok"] is False
    quotes_only = tt(
        ["get_option_chain", "--symbol", SYM, "--expiration", w.front.isoformat(), "--include_quotes"]
    )
    assert "iv" not in quotes_only["chain"][w.front.isoformat()][0], "greeks only when asked for"


def test_a_row_with_no_two_sided_quote_has_no_mid_and_no_greeks(earnings):
    w = World()
    rows = _rows(100.0, 4 / 365.0, 1.0)
    for r in rows:
        if r["strike"] == 100.0 and r["right"] == "C":
            r["bid"], r["ask"] = None, 0.0
    put_chain(w.conn, SYM, ENTRY, w.front, rows)
    tt = e31.FakeTT(w.study.chains, SYM, ENTRY, 100.0, e31.weekly_candidates(ENTRY))
    got = tt(["get_option_chain", "--symbol", SYM, "--expiration", w.front.isoformat(), "--include_greeks",
              "--include_quotes", "--strike_count", "1", "--around_price", "100.0"])  # fmt: skip
    call = next(
        e for e in got["chain"][w.front.isoformat()] if e["strike_price"] == 100.0 and e["option_type"] == "C"
    )
    assert call["mid"] is None and call["delta"] is None and call["iv"] is None


def test_the_macro_calendar_reads_the_curated_fomc_years_and_a_hand_placed_file(tmp_path):
    fomc, cpi = e31.macro_calendar(tmp_path / "none.json")
    assert date(2025, 1, 29) in fomc and cpi is None
    path = tmp_path / "macro_calendar.json"
    path.write_text(json.dumps({"fomc": ["2021-06-16"], "cpi": ["2021-06-10"]}), encoding="utf-8")
    fomc2, cpi2 = e31.macro_calendar(path)
    assert date(2021, 6, 16) in fomc2 and cpi2 == {date(2021, 6, 10)}


def test_requests_writes_both_files_reports_the_count_and_skips_what_the_store_holds(
    tmp_path, monkeypatch, capsys
):
    from cherrypick.backtest import settings

    out = tmp_path / "item31"
    out.mkdir()
    events = _events(3)
    (out / "events.json").write_text(json.dumps({"events": events}), encoding="utf-8")
    rows = [(SYM, date.fromisoformat(e["report_date"]), "After market close") for e in events]
    rows += [
        ("OTHER", date(2020, 1, 22), "After market close"),
        ("OTHER", date(2026, 9, 1), None),
    ]  # the span
    monkeypatch.setattr(e31, "dolt_all_reports", lambda names, host, port: rows)
    e31.main(["requests", "--out-dir", str(out)])
    first = json.loads(capsys.readouterr().out)
    assert first["events"] == 3 and first["chain_requests"] == 3 and first["path_requests"] == 6
    assert first["total_requests"] == 9 and first["stored_singles_skipped"] == 0
    chains = chainfetch.read_requests(out / "eod-chain-requests.json")
    paths = chainfetch.read_path_requests(out / "eod-path-requests.json")
    assert len(chains) == 3 and len(paths) == 6
    conn = chainfetch.connect(settings.load(None))
    try:
        chainfetch.write_chain(conn, chains[0], [])
    finally:
        conn.close()
    e31.main(["requests", "--out-dir", str(out)])
    second = json.loads(capsys.readouterr().out)
    assert second["chain_requests"] == 2 and second["stored_singles_skipped"] == 1
    assert (out / "calendar.json").exists()


def test_the_study_refuses_unless_the_pilot_chose_the_end_of_day_basis(tmp_path):
    for pilot in ({"decided": True, "basis": "intraday"}, {"decided": False, "basis": None}, {}):
        (tmp_path / "pilot.json").write_text(json.dumps(pilot), encoding="utf-8")
        with pytest.raises(SystemExit, match="end-of-day"):
            e31.main(["study", "--out-dir", str(tmp_path)])
