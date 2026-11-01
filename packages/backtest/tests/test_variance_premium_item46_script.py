"""Hypothesis 46's inputs (scripts/variance_premium_item46.py): the names, the cycles and the path requests.
Invented store keys only, no store.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "variance_premium_item46", SCRIPTS / "variance_premium_item46.py"
)
s46 = importlib.util.module_from_spec(_spec)
sys.modules["variance_premium_item46"] = s46
_spec.loader.exec_module(s46)


def test_the_groups_are_the_declared_seven_names_and_do_not_overlap():
    assert s46.GROUPS["equity_index"] == ("SPXW", "SPY", "QQQ", "IWM")
    assert s46.GROUPS["non_equity"] == ("GLD", "SLV", "TLT")
    assert len(set(s46.NAMES)) == len(s46.NAMES) == 7


def test_a_monthly_is_the_third_friday_unless_it_is_a_holiday():
    assert s46.monthly_expiry(2026, 10) == date(2026, 10, 16)
    assert s46.monthly_expiry(2022, 4) == date(2022, 4, 14)  # Good Friday 04-15: the Thursday
    assert s46.monthly_expiry(2025, 4) == date(2025, 4, 17)  # Good Friday 04-18
    assert s46.monthly_expiry(2016, 3) == date(2016, 3, 18)


def test_the_cycles_run_from_the_first_to_the_last_declared_expiry_one_a_month():
    cs = s46.cycles()
    assert cs[0] == s46.FIRST_EXPIRY and cs[-1] == s46.LAST_EXPIRY
    assert len(cs) == 128  # 2016-03 .. 2026-10
    assert len({(d.year, d.month) for d in cs}) == len(cs)
    # A window that ends before its month's monthly leaves that month out.
    assert s46.cycles(date(2026, 9, 1), date(2026, 10, 15))[-1] == date(2026, 9, 18)


def test_each_request_is_the_stores_66_day_path_to_expiry():
    r = s46.path_request("SPY", date(2026, 3, 20))
    assert r == {"symbol": "SPY", "expiration": "2026-03-20", "start": "2026-01-13", "through": "2026-03-20"}
    longest = max(s46.TENORS)
    assert (
        date(2026, 3, 20) - date.fromisoformat(r["start"])
    ).days > longest  # every tenor's entry is inside


def test_every_name_gets_every_cycle():
    reqs = s46.build_requests()
    assert len(reqs) == 7 * 128
    assert {r["symbol"] for r in reqs} == set(s46.NAMES)
    assert reqs[0]["symbol"] == "SPXW" and reqs[-1]["symbol"] == "TLT"


def test_held_counts_only_the_exact_complete_key():
    reqs = s46.build_requests(("SPY",), [date(2026, 3, 20), date(2026, 4, 17)])
    exact = {("SPY", "2026-03-20", "2026-01-13", "2026-03-20")}
    assert s46.held(reqs, exact) == 1
    shorter = {("SPY", "2026-03-20", "2026-03-02", "2026-03-20")}  # an 18-day path is not the same request
    assert s46.held(reqs, shorter) == 0


def test_the_time_split_falls_inside_the_cycles():
    cs = s46.cycles()
    before = [d for d in cs if d < s46.TIME_SPLIT]
    assert 0 < len(before) < len(cs)


# ------------------------------------------------------------------------------------------------
# The coverage step's building blocks, on invented chains priced by Black at a known forward.


def chain(fwd=100.0, vol=0.20, years=30 / 365, strikes=range(70, 131), half=0.05):
    from cherrypick.core import bs

    out = []
    for k in strikes:
        for right in ("C", "P"):
            p = bs.price(fwd, float(k), years, vol, right, 0.0)
            out.append((float(k), right, max(0.0, round(p - half, 2)), round(p + half, 2)))
    return out


def test_a_mid_needs_an_ask_above_zero_and_an_uncrossed_bid():
    assert s46.mid(1.0, 1.2) == 1.1
    assert s46.mid(0.0, 0.05) == 0.025
    assert s46.mid(0.0, 0.0) is None
    assert s46.mid(1.3, 1.2) is None
    assert s46.mid(None, 1.2) is None


def test_the_forward_is_parity_where_call_and_put_meet():
    assert abs(s46.forward(chain(fwd=100.0)) - 100.0) < 0.02
    assert abs(s46.forward(chain(fwd=103.4)) - 103.4) < 0.06
    calls_only = [r for r in chain() if r[1] == "C"]
    assert s46.forward(calls_only) is None


def test_the_entry_is_the_first_trading_day_on_or_after_expiry_minus_the_tenor():
    assert s46.entry_session(date(2026, 10, 16), 30) == date(2026, 9, 16)
    assert s46.entry_session(date(2022, 4, 14), 60) == date(2022, 2, 14)  # a Sunday: the Monday
    assert s46.entry_session(date(2026, 4, 17), 60) == date(2026, 2, 17)  # Presidents' Day: the Tuesday


def test_each_leg_is_picked_at_its_strike_or_delta():
    from cherrypick.core import bs

    y = 30 / 365
    ch = chain(years=y)
    straddle = s46.pick_leg(ch, 100.0, y, "straddle")
    assert [(c["strike"], c["right"]) for c in straddle] == [(100.0, "C"), (100.0, "P")]
    for leg, (right, target) in (("put25", ("P", 0.25)), ("put10", ("P", 0.10)), ("call25", ("C", 0.25))):
        (c,) = s46.pick_leg(ch, 100.0, y, leg)
        assert c["right"] == right
        assert abs(abs(bs.delta(100.0, c["strike"], y, c["iv"], right, 0.0)) - target) < 0.03
    # A put nobody bids is never sold: with every put bid at zero, no put leg and no straddle.
    no_put_bid = [(k, r, 0.0 if r == "P" else b, a) for k, r, b, a in ch]
    assert s46.pick_leg(no_put_bid, 100.0, y, "put25") is None
    assert s46.pick_leg(no_put_bid, 100.0, y, "straddle") is None
    assert s46.pick_leg(no_put_bid, 100.0, y, "call25") is not None


def test_the_realised_variance_term_by_hand():
    # 0.5 x 0.02 x (1 + 4) / 100 x 1e4 = 5 bp
    assert abs(s46.rv_term_bp(0.02, [100.0, 101.0, 99.0]) - 5.0) < 1e-9
    assert s46.rv_term_bp(0.02, [100.0]) is None


def test_a_cycle_counts_once_with_its_names_equal_weighted():
    per_name = {"SPXW": {"2026-09-18": 4.0, "2026-10-16": 2.0}, "SPY": {"2026-09-18": 6.0}}
    assert s46.group_cycle_means(per_name, ("SPXW", "SPY", "QQQ")) == {"2026-09-18": 5.0, "2026-10-16": 2.0}


def test_a_cell_under_eighty_cycles_is_flagged_and_the_first_holm_step_is_stricter():
    few = s46.cell_mde([1.0, 2.0, 3.0] * 26)
    assert few["cycles"] == 78 and not few["enough"]
    many = s46.cell_mde([1.0, 2.0, 3.0] * 27)
    assert many["enough"]
    assert many["mde_bp_holm_first"] > many["mde_bp_holm_last"] > 0


def test_a_cycle_drops_a_missing_entry_and_counts_a_gap():
    expiry = date(2026, 10, 16)
    days = s46.trading_days(date(2026, 10, 9), expiry)
    sessions = {
        d: chain(fwd=100.0 + i, years=max(s46.years_to(d, expiry), 1 / 365))
        for i, d in enumerate(days)
        if d != date(2026, 10, 13)
    }
    terms, notes = s46.cycle_terms(sessions, expiry)
    assert notes[7] == {"gaps": 1}
    assert set(terms[7]) == set(s46.LEGS)
    assert all(v > 0 for v in terms[7].values())
    assert notes[30] == {"drop": "entry_missing"}
    sessions.pop(expiry)
    _, notes = s46.cycle_terms(sessions, expiry)
    assert notes[7] == {"drop": "expiry_missing"}


def test_the_store_reader_translates_the_stores_rights_so_a_forward_exists():
    # The chain store writes CALL/PUT; read raw, no call meets a put and every cycle is "no_forward".
    import sqlite3

    from cherrypick.backtest import chainfetch

    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    req = s46.path_request("SPY", date(2026, 10, 16))
    store_side = {"C": "CALL", "P": "PUT"}
    for k, r, b, a in chain():
        conn.execute(
            "INSERT INTO eod_chain_quotes (symbol, session, expiration, strike, right, bid, ask)"
            " VALUES ('SPY', '2026-10-09', '2026-10-16', ?, ?, ?, ?)",
            (k, store_side[r], b, a),
        )
    conn.execute(
        "INSERT INTO eod_chain_requests (symbol, session, expiration, status, fetched_at)"
        " VALUES ('SPY', '2026-10-09', '2026-10-16', 'complete', 0)"
    )
    sessions = s46._path_sessions(conn, req)
    (rows,) = sessions.values()
    assert {r for _, r, _, _ in rows} == {"C", "P"}
    assert abs(s46.forward(rows) - 100.0) < 0.02
    raw = [(k, store_side[r], b, a) for k, r, b, a in rows]
    assert s46.forward(raw) is None
