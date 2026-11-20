"""The Builder's payoff chart: the structure an automation's rules would take, placed as `decide`
places it, and its P&L at expiry, on live quotes or else our own last recorded session."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from cherrypick.core import structures
from market import CALENDAR
from test_builderform import _counts
from test_paper_loop import at, world

from cherrypick.automations import builderform, config, enrolment, feed, pricing
from cherrypick.automations.model import Leg


def _condor() -> list[Leg]:
    return [
        Leg("long_put", "P", 1, 90.0),
        Leg("short_put", "P", -1, 95.0),
        Leg("short_call", "C", -1, 105.0),
        Leg("long_call", "C", 1, 110.0),
    ]


def test_the_curve_is_drawn_through_each_strike_and_crosses_zero_at_the_breakevens():
    points = pricing.payoff_curve(_condor(), 2.0, 80.0, 120.0)
    assert [x for x, _ in points] == [80.0, 90.0, 95.0, 105.0, 110.0, 120.0]
    assert [y for _, y in points] == [-3.0, -3.0, 2.0, 2.0, -3.0, -3.0]
    assert pricing.breakevens(points) == [93.0, 107.0]


def test_a_strike_outside_the_range_is_not_drawn_but_still_counts():
    points = pricing.payoff_curve(_condor(), 2.0, 92.0, 108.0)
    assert [x for x, _ in points] == [92.0, 95.0, 105.0, 108.0]
    assert points[0][1] == 2.0 - 3.0 and points[-1][1] == 2.0 - 3.0


def _payoff(w, when, **kw):
    return builderform.payoff(w.config, w.arms["iron-fly-1000"], when, conn=w.conn, evts=CALENDAR, **kw)


def test_on_live_quotes_it_takes_the_legs_decide_takes_and_writes_nothing(tmp_path):
    w = world(tmp_path, keep=())
    before = _counts(w.conn)
    today = builderform.today(w.config, w.arms["iron-fly-1000"], at(10, 0), conn=w.conn,
                              cache_path=w.cache_path, evts=CALENDAR)  # fmt: skip
    out = _payoff(w, at(10, 0), cache_path=w.cache_path)
    assert out["state"] == "placed" and out["source"]["kind"] == "live"
    placed = [{k: v for k, v in g.items() if k != "offset"} for g in out["legs"]]
    assert placed == today["entry"]["legs"] and out["expiry"] == today["entry"]["expiry"]
    assert {g["offset"] for g in out["legs"]} and 0 in {r["offset"] for r in out["chain"]}
    assert len(out["sketch"]["legs"]) == 4
    assert out["prices"] == today["entry"]["prices"]
    pnl = [y for _, y in out["curve"]]
    assert max(pnl) == out["max_profit"] == round(today["entry"]["max_profit"] * 100, 2)
    assert -min(pnl) == out["max_loss"] == round(today["entry"]["max_loss"] * 100, 2)
    xs = [x for x, _ in out["curve"]]
    assert xs == sorted(xs) and xs[0] < out["spot"] < xs[-1]
    assert len(out["breakevens"]) == 2
    shown = {r["strike"] for r in out["chain"]}
    assert {g["strike"] for g in out["legs"]} <= shown
    assert _counts(w.conn) == before
    assert not list(w.replay.glob("*.jsonl"))


def test_out_of_hours_it_places_on_the_recorded_session_and_says_so(tmp_path):
    w = world(tmp_path, keep=())
    arm = {**w.arms["iron-fly-1000"], "enabled": True}
    spec = config.symbols(w.config, {arm["name"]: arm})[arm["symbol"].upper()]
    snap, _ = feed.snapshot_for(w.cache_path, w.conn, spec, arm["symbol"].upper(), at(10, 0),
                                config.defaults(w.config), [arm], events=CALENDAR)  # fmt: skip
    asked = []

    def recorded(a, today, evts):
        asked.append((a["name"], today))
        return snap, {"kind": "recorded", "at": "2026-01-02T10:00", "source": "recorded-0dte"}, None

    out = _payoff(w, at(10, 0), cache_path=str(tmp_path / "missing.db"), recorded=recorded)
    assert out["state"] == "placed" and out["source"]["kind"] == "recorded" and asked
    assert len(out["legs"]) == 4


def test_with_neither_it_names_both_reasons(tmp_path):
    w = world(tmp_path, keep=())
    out = _payoff(w, at(10, 0), cache_path=str(tmp_path / "missing.db"),
                  recorded=lambda a, t, e: (None, None, "no store"))  # fmt: skip
    assert out["state"] == "not_placed" and out["live"] and out["recorded"] == "no store"
    assert "curve" not in out
    # Credit or debit is the rule's, so it is said even where no session places the legs.
    assert out["entry_kind"] == structures.entry_kind(w.arms["iron-fly-1000"]) == "credit"


def test_the_recorded_session_is_never_read_from_a_licensed_store(tmp_path, monkeypatch):
    from cherrypick.core import home, sources

    arm = world(tmp_path, keep=()).arms["iron-fly-1000"]
    data = tmp_path / "data"
    data.mkdir()
    (data / "vendor.db").write_text("")
    window = {"source": "vendor-0dte", "licensed": True, "end": "2026-01-02"}
    monkeypatch.setattr(enrolment, "windows", lambda *a, **k: [window])
    monkeypatch.setattr(
        sources,
        "by_name",
        lambda n: SimpleNamespace(licensed=True, probe=SimpleNamespace(backend="sqlite", where="vendor.db")),
    )
    monkeypatch.setattr(home, "data_dir", lambda *a, **k: data)
    snap, source, why = builderform.recorded_structure(arm, date(2026, 1, 5), CALENDAR)
    assert snap is None and source is None and "none of our own stores" in why


def test_a_later_expiry_is_placed_on_live_quotes_only(tmp_path):
    arm = world(tmp_path, keep=()).arms["gld-call-spread-20d"]
    snap, source, why = builderform.recorded_structure(arm, date(2026, 1, 5), CALENDAR)
    assert snap is None and "live quotes only" in why


def test_the_chain_example_keeps_the_strikes_near_each_leg_and_the_spot_and_marks_gaps():
    from cherrypick.automations.model import Chain

    rows = [
        {"strike": float(k), "right": r, "bid": 1.0, "ask": 1.2, "delta": 0.3}
        for k in range(80, 125)
        for r in "PC"
    ]
    out = builderform.chain_slice(Chain("2026-01-02", rows), [90.0, 110.0], 100.4)
    ks = [r["strike"] for r in out]
    assert ks == [
        88.0,
        89.0,
        90.0,
        91.0,
        92.0,
        98.0,
        99.0,
        100.0,
        101.0,
        102.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
    ]
    assert [r["gap"] for r in out].count(True) == 2 and out[5]["gap"] and out[10]["gap"]
    assert out[0]["put"] == {"bid": 1.0, "ask": 1.2, "delta": 0.3}
