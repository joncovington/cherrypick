"""The held-long lifecycle: a ~1-year long held while a weekly ~0.70-delta short rolls against it.

`shield` (Tom King's rules, early roll included) and `shield_hold` (the same, holding each short to
Friday) share one entry, so they are tested as a pair wherever the difference is the point. The
clock-table cases are the ones the 2026-10-04 design review named: the minute before the roll, the
roll, the deadline and the settle pass on a normal Friday and on the early closes of 2026-11-27 and
2026-12-24.
"""

import copy
from datetime import datetime

import pytest
from cherrypick.core import streamcache

from cherrypick.pmcc import clock, db, engine, management, paper_loop

LEAP = "2027-09-17"
LISTING = ["2026-09-04", "2026-09-11", "2026-09-18", "2027-01-15", "2027-06-18", LEAP, "2028-01-21"]
MONDAY = datetime(2026, 8, 24, 11, 0)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def shield(config, monkeypatch):
    """Both held-long arms on, control off: the arms join `engine.ARMS` only at the boundary commit."""
    monkeypatch.setattr(engine, "ARMS", ("control", "shield", "shield_hold"))
    cfg = copy.deepcopy(config)
    common = {
        "enabled": True,
        "lifecycle": "held_long",
        "short_rule": "delta",
        "long_delta_min": 0.90,
        "long_delta_max": 0.95,
        "allow_extrinsic_fallback": False,
        "deep_window_pct": 0.60,
        "stop_loss_frac": 0.30,
        "account_policy": "ira",
    }
    cfg["arms"] = {
        "control": {"enabled": False},
        "shield": {**common, "early_roll_decay": 0.85, "breach_roll": True},
        "shield_hold": dict(common),
    }
    return cfg


def _weekly(cache, expiration, rows, symbol="TQQQ"):
    for strike, delta, bid, ask in rows:
        cache.option(symbol, expiration, strike, bid=bid, ask=ask, delta=delta)


def _entry_market(cache, symbol="TQQQ", spot=70.60):
    cache.spot(symbol, spot)
    streamcache.write_listed_expirations(cache.conn, symbol, LISTING)
    _weekly(
        cache, LEAP, [(30.0, 0.97, 41.5, 42.5), (35.0, 0.93, 37.4, 38.2), (40.0, 0.89, 33.4, 34.2)], symbol
    )
    _weekly(
        cache,
        "2026-09-04",
        [
            (67.0, 0.79, 4.40, 4.50),
            (68.0, 0.72, 3.60, 3.70),
            (69.0, 0.66, 2.95, 3.05),
            (70.0, 0.58, 2.35, 2.45),
        ],
        symbol,
    )


def _enter(cache, cfg, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _entry_market(cache)
    paper_loop.run_once(cfg, conn, cache_path=cache.path, when=MONDAY)
    return conn


def _position(conn, arm):
    return dict(conn.execute("SELECT * FROM pmcc_positions WHERE arm = ?", (arm,)).fetchone())


def _legs(conn, arm):
    pid = _position(conn, arm)["position_id"]
    return {leg["leg_role"]: leg for leg in db.legs_for(conn, pid)}


def _friday_market(cache, *, next_week=True):
    """Friday 2026-09-04: spot 71, the old 68 short deep in the money with its extrinsic gone."""
    cache.spot("TQQQ", 71.00)
    cache.option("TQQQ", "2026-09-04", 68.0, bid=3.00, ask=3.10, delta=0.97)
    cache.option("TQQQ", LEAP, 35.0, bid=37.8, ask=38.6, delta=0.94)
    if next_week:
        _weekly(
            cache,
            "2026-09-11",
            [(69.0, 0.77, 3.05, 3.15), (70.0, 0.71, 2.40, 2.50), (71.0, 0.64, 1.85, 1.95)],
        )


# --------------------------------------------------------------------------- the clock
def test_the_long_is_picked_from_the_listing_nearest_a_year():
    today = MONDAY.date()
    assert clock.leap_expiration(LISTING, today) == {"long_expiration": LEAP, "long_dte": 389}
    # Nothing listed in [240, 540]: a refusal, never a substitute date.
    assert clock.leap_expiration(["2026-09-18", "2027-01-15"], today) is None
    # A tie takes the nearer date (less capital): 330 and 390 DTE are both 30 from 360.
    tie = ["2027-07-20", "2027-09-18"]
    assert clock.leap_expiration(tie, today)["long_expiration"] == "2027-07-20"


def test_a_standard_monthly_is_preferred_to_a_nearer_quarterly():
    """Probed 2026-10-04: SLV's 2027-09-30 (end of quarter, 361 DTE) lists strikes from 39, its
    2027-09-17 (third Friday, 348 DTE) from 5. Nearest-to-360 alone took the quarterly, which has no
    0.90-0.95-delta strike at a 54.74 spot."""
    today = datetime(2026, 10, 4).date()
    listed = ["2027-06-17", "2027-06-30", "2027-09-17", "2027-09-30", "2028-01-21"]
    assert clock.leap_expiration(listed, today)["long_expiration"] == "2027-09-17"
    # With no monthly in the band, any listed date still serves.
    assert clock.leap_expiration(["2027-09-30"], today)["long_expiration"] == "2027-09-30"
    assert clock.standard_monthly(2027, 6).isoformat() == "2027-06-17"  # Juneteenth observed Friday
    assert clock.standard_monthly(2027, 9).isoformat() == "2027-09-17"


def test_a_short_never_outlives_its_long():
    assert clock.short_expiration(MONDAY.date())["short_expiration"] == "2026-09-04"
    assert clock.short_expiration(MONDAY.date(), cap="2026-09-01") is None


@pytest.mark.parametrize(
    ("day", "close", "settle"),
    [
        (datetime(2026, 9, 4).date(), 16 * 60, 16 * 60 + 20),
        (datetime(2026, 11, 27).date(), 13 * 60, 13 * 60 + 20),
        (datetime(2026, 12, 24).date(), 13 * 60, 13 * 60 + 20),
    ],
)
def test_every_bell_anchored_time_follows_the_session_close(day, close, settle):
    assert clock.session_close_min(day) == close
    assert paper_loop.settle_time_min({}, day) == settle
    assert paper_loop.in_session(close - 1, day) and not paper_loop.in_session(close, day)


# --------------------------------------------------------------------------- the verdict
def _params(**over):
    base = {
        **management.PARAM_DEFAULTS,
        "lifecycle": "held_long",
        "stop_loss_frac": 0.30,
        "early_roll_decay": 0.85,
        "breach_roll": True,
    }
    return {**base, **over}


POSITION = {"long_entry_mid": 37.80, "quantity": 1}
SHORT = {"strike": 68.0, "expiration": "2026-09-04", "tv": 0.50, "entry_tv": 1.05}
PNL_OK = {"net": -100.0}


def _verdict(when, *, short=SHORT, spot=69.0, pnl=PNL_OK, long_dte=389, rolled=False, params=None):
    return management.evaluate_held_long(
        POSITION,
        params or _params(),
        now=when,
        short=short,
        spot=spot,
        pnl=pnl,
        long_dte=long_dte,
        rolled_today=rolled,
        session_close_min=clock.session_close_min(when.date()),
    )


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 9, 4, 14, 59), ("hold", "holding")),
        (datetime(2026, 9, 4, 15, 0), ("roll_short", "expiry")),
        (datetime(2026, 9, 4, 15, 39), ("roll_short", "expiry")),
        (datetime(2026, 9, 4, 15, 40), ("close_short", "roll_deadline")),
        # The day after Thanksgiving closes at 13:00: the roll is due at 12:00, the deadline 12:40.
        (datetime(2026, 11, 27, 11, 59), ("hold", "holding")),
        (datetime(2026, 11, 27, 12, 0), ("roll_short", "expiry")),
        (datetime(2026, 11, 27, 12, 40), ("close_short", "roll_deadline")),
    ],
)
def test_the_expiry_day_clock(when, expected):
    short = {**SHORT, "expiration": when.date().isoformat()}
    v = _verdict(when, short=short, params=_params(early_roll_decay=None, breach_roll=False))
    assert (v.action, v.reason) == expected


def test_the_order_of_the_verdicts():
    wed = datetime(2026, 9, 2, 11, 0)
    # The stop beats every other rule: -30% of a $3,780 long is -$1,134.
    assert _verdict(wed, pnl={"net": -1134.0}, long_dte=10, short=None).reason == "stop_loss"
    assert _verdict(wed, pnl={"net": -1133.0}, long_dte=10, short=None).reason == "long_roll_due"
    assert _verdict(wed, short=None).action == "sell_short"
    assert _verdict(wed, short={**SHORT, "tv": None}).reason == "unpriced_mark"
    # 85% decayed: extrinsic at or under 15% of the 1.05 sold.
    assert _verdict(wed, short={**SHORT, "tv": 0.157}).reason == "decayed"
    assert _verdict(wed, short={**SHORT, "tv": 0.16}).reason == "holding"
    assert _verdict(wed, spot=67.9).reason == "breach"


def test_an_early_roll_fires_at_most_once_a_session():
    wed = datetime(2026, 9, 2, 11, 0)
    assert _verdict(wed, short={**SHORT, "tv": 0.05}, rolled=True).reason == "holding"
    assert _verdict(wed, spot=60.0, rolled=True).reason == "holding"
    # The expiry roll is not an early roll: a short rolled this morning into today's expiry is
    # still rolled out at the roll time.
    fri = datetime(2026, 9, 4, 15, 0)
    assert _verdict(fri, rolled=True, params=_params(early_roll_decay=None)).reason == "expiry"


def test_shield_hold_never_rolls_early():
    wed = datetime(2026, 9, 2, 11, 0)
    hold = _params(early_roll_decay=None, breach_roll=False)
    assert _verdict(wed, short={**SHORT, "tv": 0.01}, spot=50.0, params=hold).reason == "holding"


def test_a_margin_account_may_let_a_far_otm_short_expire_but_an_ira_never_does():
    deadline = datetime(2026, 9, 4, 15, 45)
    short = {**SHORT, "strike": 75.0}
    margin = _verdict(deadline, short=short, spot=70.0, params=_params(account_policy="margin"))
    assert (margin.action, margin.reason) == ("hold", "let_expire")
    ira = _verdict(deadline, short=short, spot=70.0, params=_params(account_policy="ira"))
    assert (ira.action, ira.reason) == ("close_short", "roll_deadline")
    # Near the money, a margin account buys it back too: pin risk.
    near = _verdict(deadline, short=short, spot=74.0, params=_params(account_policy="margin"))
    assert near.action == "close_short"


# --------------------------------------------------------------------------- the engine
def test_the_short_is_the_call_nearest_seventy_delta_above_the_long():
    entries = [
        {"strike_price": k, "streamer_symbol": f"s{k}", "occ_symbol": f"o{k}", "option_type": "call"}
        for k in (30.0, 66.0, 67.0, 68.0, 69.0)
    ]
    quotes = {f"s{k}": {"bid": 1.0, "ask": 1.1, "mid": 1.05} for k in (30.0, 66.0, 67.0, 68.0, 69.0)}
    greeks = {
        "s30.0": {"delta": 0.70},
        "s66.0": {"delta": 0.85},
        "s67.0": {"delta": 0.72},
        "s68.0": {"delta": 0.68},
    }
    pick = engine.select_short_by_delta(entries, quotes, greeks, 70.0, {}, floor_strike=35.0)
    # 30 is below the long, 66 is outside the band, 69 has no delta: 67 and 68 tie at 0.02 -> higher.
    assert pick["ok"] and pick["strike"] == 68.0
    none = engine.select_short_by_delta(entries, quotes, {}, 70.0, {}, floor_strike=35.0)
    assert none == {"ok": False, "reason": "no_short_delta"}


def test_a_roll_with_a_too_wide_buyback_is_refused_and_names_the_leg():
    snapshot = {
        "spot": 71.0,
        "short_expiration": "2026-09-11",
        "short_dte": 7,
        "short_chain": [
            {"strike_price": 70.0, "streamer_symbol": "n", "occ_symbol": "o", "option_type": "call"}
        ],
        "quotes": {"n": {"bid": 2.40, "ask": 2.50, "mid": 2.45}},
        "greeks": {"n": {"delta": 0.71}},
    }
    wide = {"bid": 2.0, "ask": 4.0, "mid": 3.0}
    out = engine.plan_short(snapshot, {}, long_strike=35.0, buyback=wide)
    assert out["ok"] is False and out["reason"] == "spread_too_wide" and out["detail"]["leg"] == "buyback"
    ok = engine.plan_short(snapshot, {}, long_strike=35.0, buyback={"bid": 3.0, "ask": 3.1, "mid": 3.05})
    assert ok["ok"] and ok["net_credit"] == pytest.approx(2.45 - 3.05)


def test_pnl_to_date_adds_every_leg_and_share_and_takes_every_cost():
    position = {"quantity": 1, "fees": 12.0}
    legs = [
        {"leg_role": "long_call", "action": "Buy to Open", "status": "open", "entry_mid": 37.8},
        {
            "leg_role": "short_call_1",
            "action": "Sell to Open",
            "status": "closed",
            "entry_mid": 3.65,
            "close_value": 3.05,
        },
        {"leg_role": "short_call_2", "action": "Sell to Open", "status": "open", "entry_mid": 2.45},
    ]
    marks = {"long_call": 38.2, "short_call_2": 2.00}
    shares = [{"status": "open", "direction": "short", "shares": 100, "basis": 71.0}]
    out = engine.pnl_to_date(position, legs, marks, shares, 70.5)
    assert out == {
        "long": 40.0,
        "short_realised": 60.0,
        "short_open": 45.0,
        "shares": 50.0,
        "gross": 195.0,
        "fees": 12.0,
        "net": 183.0,
    }
    assert engine.pnl_to_date(position, legs, {"long_call": 38.2}, [], 70.5) is None  # an unmarked leg


# --------------------------------------------------------------------------- the lifecycle
def test_the_pair_enters_one_identical_position_from_the_listing(cache, shield, tmp_path):
    conn = _enter(cache, shield, tmp_path)
    for arm in ("shield", "shield_hold"):
        p = _position(conn, arm)
        assert (p["long_strike"], p["long_expiration"]) == (35.0, LEAP)
        assert (p["short_strike"], p["short_expiration"]) == (68.0, "2026-09-04")
    assert db.legs_for(conn, _position(conn, "shield")["position_id"])[0]["entry_mid"] == pytest.approx(37.8)


def test_no_listing_refuses_rather_than_guessing_a_date(cache, shield, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    cache.spot("TQQQ", 70.60)
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=MONDAY)
    assert db.open_positions(conn) == []
    reasons = {r["reason"] for r in conn.execute("SELECT reason FROM pmcc_decisions")}
    assert reasons == {"no_listing"}
    streamcache.write_listed_expirations(cache.conn, "TQQQ", ["2026-09-04", "2027-01-15"])
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=MONDAY)
    assert "no_leap_listed" in {r["reason"] for r in conn.execute("SELECT reason FROM pmcc_decisions")}


def test_the_friday_roll_is_one_ticket_that_keeps_the_long(cache, shield, tmp_path):
    conn = _enter(cache, shield, tmp_path)
    before = _position(conn, "shield_hold")
    _friday_market(cache)
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 14, 59))
    assert _position(conn, "shield_hold")["roll_count"] == 0  # a minute early

    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 15, 0))
    after = _position(conn, "shield_hold")
    legs = _legs(conn, "shield_hold")
    assert after["status"] == "open" and after["roll_count"] == 1
    assert (after["short_strike"], after["short_expiration"]) == (70.0, "2026-09-11")
    old, new = legs["short_call_1"], legs["short_call_2"]
    assert (old["status"], old["close_kind"], old["close_reason"]) == ("closed", "rolled", "roll:expiry")
    assert old["close_value"] == pytest.approx(3.05)
    assert (new["status"], new["strike"], new["expiration"]) == ("open", 70.0, "2026-09-11")
    assert legs["long_call"]["status"] == "open"
    # The ticket's legs carry exactly what the position's fees grew by.
    ticket = old["close_cost"] + old["close_slippage"] + new["entry_cost"] + new["entry_slippage"]
    assert after["fees"] - before["fees"] == pytest.approx(ticket, abs=1e-9)
    event = conn.execute(
        "SELECT detail_json FROM pmcc_management_events WHERE action = 'roll_short' AND position_id = ?",
        (after["position_id"],),
    ).fetchone()
    detail = __import__("json").loads(event["detail_json"])
    assert {k: detail[k] for k in ("old_strike", "new_strike", "old_expiration", "new_expiration")} == {
        "old_strike": 68.0,
        "new_strike": 70.0,
        "old_expiration": "2026-09-04",
        "new_expiration": "2026-09-11",
    }
    assert detail["net_roll_credit"] == pytest.approx(2.45 - 3.05)


def test_shield_rolls_on_decay_where_shield_hold_waits_for_friday(cache, shield, tmp_path):
    conn = _enter(cache, shield, tmp_path)
    _friday_market(cache)
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 11, 0))
    assert _position(conn, "shield")["roll_count"] == 1
    assert _legs(conn, "shield")["short_call_1"]["close_reason"] == "roll:decayed"
    assert _position(conn, "shield_hold")["roll_count"] == 0


def test_an_unplannable_roll_buys_the_short_back_at_the_deadline_and_sells_next_session(
    cache, shield, tmp_path
):
    conn = _enter(cache, shield, tmp_path)
    _friday_market(cache, next_week=False)  # no 2026-09-11 chain: the roll cannot be planned
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 15, 0))
    assert _legs(conn, "shield_hold")["short_call_1"]["status"] == "open"
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 15, 40))
    old = _legs(conn, "shield_hold")["short_call_1"]
    assert (old["status"], old["close_kind"], old["close_reason"]) == ("closed", "traded", "roll_deadline")
    assert _position(conn, "shield_hold")["status"] == "open"

    # Tuesday 09-08 (Monday is Labor Day): the next short is sold into the empty position.
    _weekly(cache, "2026-09-18", [(70.0, 0.71, 2.80, 2.90), (71.0, 0.65, 2.20, 2.30)])
    cache.option("TQQQ", LEAP, 35.0, bid=37.8, ask=38.6, delta=0.94)
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 8, 10, 0))
    new = _legs(conn, "shield_hold")["short_call_2"]
    assert (new["status"], new["expiration"]) == ("open", "2026-09-18")


def test_a_short_spanning_an_ex_date_is_refused_and_the_position_waits_without_one(cache, shield, tmp_path):
    shield["dividends"]["TQQQ"]["ex_dates"] = ["2026-09-08"]
    conn = _enter(cache, shield, tmp_path)
    _friday_market(cache)
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 15, 0))
    old = _legs(conn, "shield_hold")["short_call_1"]
    assert (old["status"], old["close_reason"]) == ("closed", "expiry:ex_dividend_span")
    assert "short_call_2" not in _legs(conn, "shield_hold")

    _weekly(cache, "2026-09-18", [(70.0, 0.71, 2.80, 2.90), (71.0, 0.65, 2.20, 2.30)])
    cache.option("TQQQ", LEAP, 35.0, bid=37.8, ask=38.6, delta=0.94)
    # Tuesday: 09-18 still spans the ex-date (inclusive).
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 8, 10, 0))
    assert "short_call_2" not in _legs(conn, "shield_hold")
    refused = conn.execute(
        "SELECT reason FROM pmcc_decisions WHERE arm = 'shield_hold' AND mode = 'manage'"
    ).fetchall()
    assert "no_short:ex_dividend_span" in {r["reason"] for r in refused}
    # Wednesday: clear of it.
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 9, 10, 0))
    assert _legs(conn, "shield_hold")["short_call_2"]["status"] == "open"


def test_a_settled_short_leaves_the_position_open_and_shares_block_the_next_sale(
    cache, shield, tmp_path, monkeypatch
):
    conn = _enter(cache, shield, tmp_path)
    # The loop missed the roll and the deadline: the backstop settles the ITM short at the print.
    paper_loop.run_settle(
        shield, conn, cache_path=cache.path, when=datetime(2026, 9, 4, 16, 30), price=72.10, day="2026-09-04"
    )
    p = _position(conn, "shield_hold")
    assert p["status"] == "open"  # not short_settled: the held long is never disposed with a short
    assert db.open_assignment_count(conn, p["position_id"]) == 1

    _weekly(cache, "2026-09-18", [(71.0, 0.71, 2.80, 2.90), (72.0, 0.65, 2.20, 2.30)])
    cache.spot("TQQQ", 72.00)
    cache.option("TQQQ", LEAP, 35.0, bid=38.8, ask=39.6, delta=0.94)
    monkeypatch.setattr(paper_loop.provider, "read_spot", lambda *a, **k: None)  # shares cannot be covered
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 8, 10, 0))
    assert "short_call_2" not in _legs(conn, "shield_hold")
    assert "sell_short:shares_open" in {
        r["reason"] for r in conn.execute("SELECT reason FROM pmcc_decisions")
    }

    monkeypatch.undo()
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 8, 10, 5))
    assert db.open_assignment_count(conn, p["position_id"]) == 0
    assert _legs(conn, "shield_hold")["short_call_2"]["status"] == "open"
    assert _position(conn, "shield_hold")["status"] == "open"


def test_the_stop_closes_everything(cache, shield, tmp_path):
    conn = _enter(cache, shield, tmp_path)
    cache.spot("TQQQ", 55.0)
    cache.option("TQQQ", LEAP, 35.0, bid=21.0, ask=21.8, delta=0.88)
    cache.option("TQQQ", "2026-09-04", 68.0, bid=0.02, ask=0.05, delta=0.01)
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=datetime(2026, 9, 1, 11, 0))
    p = _position(conn, "shield_hold")
    assert (p["status"], p["exit_reason"]) == ("closed", "stop_loss")


def test_held_long_entries_are_paced_across_symbols(cache, shield, tmp_path):
    shield["symbols"] = ["TQQQ", "SLV"]
    shield["occ_roots"]["SLV"] = "SLV"
    shield["settlement_style"]["SLV"] = "physical"
    shield["dividends"]["SLV"] = {"declared_through": "2099-12-31", "ex_dates": []}
    for arm in ("shield", "shield_hold"):
        shield["arms"][arm]["entry_symbols_per_session"] = 1
    conn = db.connect(str(tmp_path / "paper.db"))
    _entry_market(cache, "TQQQ")
    _entry_market(cache, "SLV")
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=MONDAY)
    entered = [p["symbol"] for p in db.open_positions(conn)]
    assert len(entered) == 2 and len(set(entered)) == 1  # the pair, on one symbol only
    paced = {
        r["symbol"] for r in conn.execute("SELECT symbol FROM pmcc_decisions WHERE reason = 'entry_pacing'")
    }
    assert len(paced) == 1


# --------------------------------------------------------------------------- the stream request
def _request(cache, cfg, conn, tmp_path, when=MONDAY):
    import json

    from cherrypick.pmcc import stream_request

    path = stream_request.write(
        cfg, conn, str(tmp_path / "paper.db"), cache_path=cache.path, today=when.date()
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_year_long_date_is_asked_for_only_while_a_held_long_arm_can_enter(cache, shield, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _entry_market(cache)
    before = _request(cache, shield, conn, tmp_path)
    assert LEAP in before["expirations"]["TQQQ"]
    paper_loop.run_once(shield, conn, cache_path=cache.path, when=MONDAY)
    after = _request(cache, shield, conn, tmp_path)
    # Both arms hold TQQQ: no entry is possible, and the open long is quoted through leg_sources,
    # so its whole expiration is no longer a window.
    assert LEAP not in after["expirations"]["TQQQ"]
    assert "2026-09-04" in after["expirations"]["TQQQ"]  # the open short, and its next roll date
    assert "TQQQ" not in after.get("window_hints", {})


def test_the_deep_window_is_sized_on_the_year_long_expiry_at_the_arms_own_depth(cache, shield, tmp_path):
    conn = db.connect(str(tmp_path / "paper.db"))
    _entry_market(cache)
    # Dense weekly strikes inside 60% of spot on the short's date must not count; the LEAP's do.
    for strike in range(29, 71):
        cache.option("TQQQ", "2026-09-04", float(strike))
    shield["stream_window"] = {"base_width": 1, "margin": 0, "round_to": 1}
    hints = _request(cache, shield, conn, tmp_path)["window_hints"]
    assert hints["TQQQ"][0] == 3  # [down, up]: 30, 35, 40 on the LEAP; spot 70.60 x 0.40 = 28.24 floor
