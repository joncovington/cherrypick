"""Resolving a position whose options have expired.

This suite exists because the module could not do it at all. Under the 2026-08-12 managed lifecycle
the only exit was to trade out, so the morning after expiration every position entered a loop it
could not leave: the feed answered an expired contract with a zero bid against a stale ask, that
priced as a usable mark carrying a 200% spread, the engine decided to close, and the execution gate
refused the spread -- every tick, forever. 57 positions sat in it for three sessions and recorded no
result at all.

So the properties here are the two halves of that:
  - an expired leg has no price, and pricing one anyway is the defect (`provider`),
  - what it has instead is intrinsic against the settlement print (`settlement`),
and the one that ties them together: the loop must actually resolve such a position rather than hold
it. That last pair is the tests whose whole value is failing, and they were written by breaking the
fix on purpose and watching the position hang.
"""

import argparse
import datetime as dt
import json
import sqlite3

import pytest
from cherrypick.core.streamcache import DDL

from cherrypick.earnings import db_paper, paper_loop, provider, scanner, settlement
from cherrypick.earnings import strat_test_harness as harness
from cherrypick.earnings import strategy_metrics as metrics

ET = paper_loop.ET

TODAY = dt.date(2026, 8, 31)
FRONT = "260828"  # expired last Friday
BACK = "260918"  # still listed


def occ(expiry, kind, strike):
    return f"AAPL  {expiry}{kind}{int(strike * 1000):08d}"


def leg(expiry, kind, strike, action, quantity=1):
    return {
        "symbol": occ(expiry, kind, strike),
        "streamer_symbol": f".AAPL{expiry}{kind}{strike:g}",
        "action": action,
        "quantity": quantity,
    }


# An iron fly, every leg on the expired front month.
EXPIRED_FLY = [
    leg(FRONT, "C", 190, "Sell to Open"),
    leg(FRONT, "P", 190, "Sell to Open"),
    leg(FRONT, "C", 200, "Buy to Open"),
    leg(FRONT, "P", 180, "Buy to Open"),
]

# A double calendar: the front is gone, the back outlives it by three weeks.
CALENDAR = [
    leg(FRONT, "C", 195, "Sell to Open"),
    leg(FRONT, "P", 185, "Sell to Open"),
    leg(BACK, "C", 195, "Buy to Open"),
    leg(BACK, "P", 185, "Buy to Open"),
]


def at(hhmm, day=TODAY):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def trade(legs, order_id="T1", strategy="iron_fly", credit=5.00):
    return {
        "order_id": order_id,
        "symbol": "AAPL",
        "strategy": strategy,
        "expiration": "2026-08-28",
        "entry_credit": credit,
        "quantity": 1,
        "legs_json": json.dumps(legs),
    }


# --------------------------------------------------------------------------- an expired leg has no price
@pytest.fixture()
def cache(tmp_path):
    path = tmp_path / "stream_cache.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    for one in EXPIRED_FLY + CALENDAR:
        # The residue an expired contract really quotes: no bid, a stale ask left standing.
        bid, ask = (0.0, 0.40) if one["symbol"][6:12] == FRONT else (1.00, 1.10)
        conn.execute(
            "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) VALUES (?,?,?,?,?)",
            (one["streamer_symbol"], bid, ask, (bid + ask) / 2, at("13:00").timestamp()),
        )
    conn.execute(
        "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?,?,?)",
        ("AAPL", 190.0, at("13:00").timestamp()),
    )
    conn.commit()
    conn.close()
    return path


def test_an_expired_leg_is_refused_rather_than_priced(cache):
    """The defect in one assertion: this used to come back ok, with a mid nobody ever offered."""
    snap = provider.snapshot(trade(EXPIRED_FLY), db_path=cache, now_ts=at("13:00").timestamp())
    assert snap["ok"] is False
    assert snap["reason"] == "legs_expired"
    assert set(snap["expired"]) == {one["symbol"] for one in EXPIRED_FLY}


def test_a_contract_is_tradeable_on_its_own_expiration_day(cache):
    """Strictly-before, not on-or-before: expiration day is a trading day, and the pin guard does
    its work inside it. Refusing a day early would blind the module to its last chance to act."""
    snap = provider.snapshot(
        trade(EXPIRED_FLY), db_path=cache, now_ts=at("13:00", dt.date(2026, 8, 28)).timestamp()
    )
    assert snap["ok"] is True


def test_only_the_expired_half_of_a_calendar_refuses_it(cache):
    snap = provider.snapshot(trade(CALENDAR), db_path=cache, now_ts=at("13:00").timestamp())
    assert snap["reason"] == "legs_expired"
    assert set(snap["expired"]) == {occ(FRONT, "C", 195), occ(FRONT, "P", 185)}


# --------------------------------------------------------------------------- what it is worth instead
@pytest.mark.parametrize(
    "symbol, close, expected",
    [
        (occ(FRONT, "C", 190), 217.55, 27.55),  # call, in the money
        (occ(FRONT, "C", 190), 180.00, 0.0),  # call, out of it -- worthless, never negative
        (occ(FRONT, "P", 190), 180.00, 10.0),  # put, in the money
        (occ(FRONT, "P", 190), 217.55, 0.0),  # put, out of it
    ],
)
def test_intrinsic_is_the_settlement_value(symbol, close, expected):
    assert settlement.option_intrinsic(symbol, close) == pytest.approx(expected)


def test_an_unparseable_symbol_yields_nothing_rather_than_a_number():
    assert settlement.option_intrinsic("nonsense", 100.0) is None
    assert settlement.option_intrinsic("", 100.0) is None


def test_due_names_the_shape_because_they_are_different_exits():
    assert settlement.due(trade(EXPIRED_FLY), at("13:00")) == "expired"
    assert settlement.due(trade(CALENDAR), at("13:00")) == "front_expiry"
    # Nothing has expired on the day itself.
    assert settlement.due(trade(EXPIRED_FLY), at("13:00", dt.date(2026, 8, 28))) is None


def test_a_settlement_close_must_be_the_expiration_days_own(monkeypatch):
    """`_nearest_close` walks back up to ten days, which is right for the winrate sampling it was
    written for and a fabrication here. A close from three days earlier is not a settlement print."""
    monkeypatch.setattr(
        scanner, "_nearest_close", lambda *a, **k: {"date": dt.date(2026, 8, 25), "close": 190.0}
    )
    assert settlement.settlement_close("AAPL", dt.date(2026, 8, 28), {}) is None

    monkeypatch.setattr(
        scanner, "_nearest_close", lambda *a, **k: {"date": dt.date(2026, 8, 28), "close": 190.0}
    )
    assert settlement.settlement_close("AAPL", dt.date(2026, 8, 28), {}) == 190.0


def test_a_settled_structure_prices_through_the_ordinary_exit_arithmetic(monkeypatch):
    """Interchangeability again: settlement must reach the ledger by the same function every other
    close uses, or it is a second measurement wearing the same column name."""
    monkeypatch.setattr(settlement, "settlement_close", lambda *a, **k: 195.0)
    snap = settlement.resolve(trade(EXPIRED_FLY), {}, at("13:00"))

    assert snap["ok"] and snap["source"] == "settlement"
    # Spot 195: the short 190 call is worth 5, the long 200 call worthless, both puts worthless.
    debit = scanner.compute_generic_exit_debit(EXPIRED_FLY, snap["quotes"])
    assert debit == pytest.approx(5.0)
    # No width anywhere, so the cost model's slippage haircut correctly comes out at nothing.
    assert snap["max_spread_pct"] == 0.0


def test_settlement_refuses_when_the_print_is_unavailable(monkeypatch):
    monkeypatch.setattr(settlement, "settlement_close", lambda *a, **k: None)
    snap = settlement.resolve(trade(EXPIRED_FLY), {}, at("13:00"))
    assert snap["ok"] is False and snap["reason"] == "no_settlement_close"


def test_a_calendars_back_month_settles_at_its_real_market(cache, monkeypatch):
    """The front is a settlement print; the back is still listed and must be closed at a real
    quote rather than an invented one."""
    monkeypatch.setattr(settlement, "settlement_close", lambda *a, **k: 190.0)
    monkeypatch.setattr(provider, "cache_path", lambda: cache)
    snap = settlement.resolve(trade(CALENDAR, strategy="double_calendar"), {}, at("13:00"))

    assert snap["ok"]
    # Front legs at intrinsic: spot 190 sits between the 195 call and the 185 put, so both are worthless.
    assert snap["quotes"][occ(FRONT, "C", 195)]["bid"] == 0.0
    assert snap["quotes"][occ(FRONT, "P", 185)]["bid"] == 0.0
    # Back legs at the cache's real quotes, untouched.
    assert snap["quotes"][occ(BACK, "C", 195)]["bid"] == 1.00
    assert set(snap["settled_legs"]) == {occ(FRONT, "C", 195), occ(FRONT, "P", 185)}


def test_a_back_month_the_cache_cannot_serve_falls_back_to_the_broker(monkeypatch):
    """The front is settled and unpriceable forever, so a back month the cache happens not to be
    serving must not hold the whole position open until it expires too. Without this the calendars
    wait three more weeks in exactly the stall this module exists to end."""
    monkeypatch.setattr(settlement, "settlement_close", lambda *a, **k: 190.0)
    monkeypatch.setattr(provider, "snapshot", lambda *a, **k: {"ok": False, "reason": "missing_leg_quotes"})

    row = trade(CALENDAR, strategy="double_calendar")
    assert settlement.resolve(row, {}, at("13:00"))["ok"] is False

    def broker(remainder):
        legs = json.loads(remainder["legs_json"])
        assert {one["symbol"] for one in legs} == {occ(BACK, "C", 195), occ(BACK, "P", 185)}
        return {
            "ok": True,
            "quotes": {one["symbol"]: {"bid": 2.0, "ask": 2.2} for one in legs},
            "spot": 190.0,
        }

    snap = settlement.resolve(row, {}, at("13:00"), rest_snapshot=broker)
    assert snap["ok"]
    assert snap["quotes"][occ(BACK, "C", 195)]["bid"] == 2.0
    assert snap["quotes"][occ(FRONT, "C", 195)]["bid"] == 0.0


# --------------------------------------------------------------------------- the loop must resolve it
@pytest.fixture()
def book(tmp_path, monkeypatch):
    path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(db_paper, "DB_PATH", path)
    monkeypatch.setattr(metrics, "DB_PATH", path)
    monkeypatch.setattr(metrics, "PAPER_DB_PATH", path)
    db_paper.cmd_init_db(argparse.Namespace())
    monkeypatch.setattr(paper_loop.stream_request, "register", lambda *a, **k: None)
    monkeypatch.setattr(
        paper_loop.costs, "apply_exit_costs", lambda *a, **k: {"total_cost": 1.0, "slippage": 0.0}
    )
    monkeypatch.setattr(settlement, "settlement_close", lambda *a, **k: 195.0)
    row = trade(EXPIRED_FLY)
    row["profile"] = "strat_test:iron_fly"
    row["capital_at_risk"] = 500.0
    row["opened_at"] = at("15:45", dt.date(2026, 8, 25)).timestamp()
    db_paper.cmd_save_trade(argparse.Namespace(data=json.dumps(row)))
    monkeypatch.setattr(harness, "_is_strat_test_book", lambda p: True)
    return path


def test_the_loop_settles_an_expired_position_instead_of_holding_it_forever(book):
    """The regression that matters. Broken, this records `hold` and the position never closes.

    Verified by reverting the settlement branch in `manage()` and re-running: the trade stays open,
    the event is a hold, and it repeats on every subsequent tick -- which is exactly the three-day
    stall this was written for.
    """
    config = {"strategies": {}, "management": {}, "tastytrade_costs": {}}
    out = paper_loop.manage(config, at("13:00"), phase="manage", execute=True)

    assert out["actions_taken"] == 1
    assert [c["reason"] for c in out["closed"]] == ["expired"]

    conn = sqlite3.connect(book)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, exit_reason, exit_debit, pnl FROM trades WHERE order_id='T1'"
    ).fetchone()
    assert row["status"] != "open"
    assert row["exit_reason"] == "expired"
    # Settled at 195: the short 190 call is worth 5.00, everything else expires worthless.
    assert row["exit_debit"] == pytest.approx(5.0)
    assert row["pnl"] == pytest.approx((5.00 - 5.0) * 100)

    event = conn.execute(
        "SELECT action, reason, executed, gate FROM management_events "
        "WHERE order_id='T1' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert (event["action"], event["reason"], event["executed"]) == ("settle", "expired", 1)
    assert event["gate"] is None
    conn.close()


def test_settlement_is_not_blocked_by_the_spread_gate(book):
    """The deadlock itself. An expired contract quotes a 200% spread against a 0.35 policy, so any
    path running settlement through `execution_gate` refuses it every tick and never resolves."""
    config = {"strategies": {}, "management": {"max_leg_spread_pct": 0.35}, "tastytrade_costs": {}}
    out = paper_loop.manage(config, at("13:00"), phase="manage", execute=True)
    assert out["actions_taken"] == 1

    conn = sqlite3.connect(book)
    gates = [r[0] for r in conn.execute("SELECT gate FROM management_events WHERE order_id='T1'")]
    conn.close()
    assert "spread_too_wide" not in gates
