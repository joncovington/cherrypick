"""close_or_hold: the close-now price, the move window, the hold distribution and the verdict."""

import sqlite3
from datetime import datetime

import pytest
from cherrypick.core import fees as core_fees
from cherrypick.core.clock import ET

from cherrypick.flies import close_or_hold as coh
from cherrypick.flies import engine, fly


def _q(bid, ask):
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}


def _quote_at(snapshot):
    return lambda side, strike: engine.quote(snapshot, side, strike)


def _vertical(**kw):
    row = {
        "position_id": "v1",
        "arm": "control",
        "symbol": "SPX",
        "kind": "short_vertical",
        "side": "call",
        "center": 100.0,
        "wing_width": 5.0,
        "net": 2.40,
        "fees": 3.44,
        "quantity": 1,
        "status": "open",
    }
    row.update(kw)
    return row


def _fly(**kw):
    row = {
        "position_id": "f1",
        "arm": "control",
        "symbol": "SPX",
        "kind": "fly",
        "side": "call",
        "center": 100.0,
        "wing_width": 5.0,
        "net": 0.25,
        "fees": 6.89,
        "quantity": 1,
        "status": "open",
    }
    row.update(kw)
    return row


def _ts(day, hh, mm, ss=0):
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET).timestamp()


# --------------------------------------------------------------------------- close now
def test_short_vertical_closes_at_natural_with_close_fees():
    snap = {"calls": {100.0: _q(4.30, 4.50), 105.0: _q(0.10, 0.20)}}
    out = coh.close_now(_vertical(), _quote_at(snap))
    assert out["ok"]
    # buy the short 100 call back at its ask, sell the long 105 call at its bid
    assert out["exit_cash"] == pytest.approx((0.10 - 4.50) * 100)
    assert out["close_fees"] == core_fees.ic_close_fee("SPX", 1, legs=2, sell_legs=1)
    assert out["value"] == pytest.approx(out["exit_cash"] - out["close_fees"])
    assert out["pnl"] == pytest.approx(240 - 3.44 + out["value"])


def test_fly_buys_its_doubled_centre_back_twice_at_the_ask():
    snap = {"calls": {95.0: _q(5.0, 5.2), 100.0: _q(2.0, 2.2), 105.0: _q(0.4, 0.5)}}
    out = coh.close_now(_fly(), _quote_at(snap))
    assert out["exit_cash"] == pytest.approx((5.0 + 0.4 - 2 * 2.2) * 100)
    assert out["close_fees"] == core_fees.ic_close_fee("SPX", 1, legs=4, sell_legs=2)


@pytest.mark.parametrize(
    "calls",
    [
        {100.0: _q(4.30, 4.50)},  # the long leg is missing (absent or dropped as stale)
        {100.0: _q(4.60, 4.50), 105.0: _q(0.10, 0.20)},  # crossed
        {100.0: _q(0.0, 0.0), 105.0: _q(0.10, 0.20)},  # no ask
        {100.0: {"bid": None, "ask": 4.5}, 105.0: _q(0.10, 0.20)},  # one-sided
    ],
)
def test_an_unusable_leg_gives_no_close_number(calls):
    row = coh.evaluate(_vertical(), _quote_at({"calls": calls}), None, [])
    assert row["close_now"] is None
    assert row["verdict"].startswith("no close price:")
    assert row["hold_minus_close"] is None


def test_the_refusal_names_the_leg():
    row = coh.evaluate(_vertical(), _quote_at({"calls": {100.0: _q(4.3, 4.5)}}), None, [])
    assert row["close_now_reason"] == "no usable quote for the 105 call"


def test_a_refused_snapshot_gives_no_close_number():
    row = coh.evaluate(_vertical(), None, 100.0, [], snapshot_reason="no_fresh_quotes")
    assert row["verdict"] == "no close price: snapshot refused: no_fresh_quotes"


# --------------------------------------------------------------------------- hold and the verdict
def _moves(*rs):
    return [{"trade_date": f"2026-08-{i + 1:02d}", "r": r} for i, r in enumerate(rs)]


def test_hold_is_the_mean_settled_pnl_with_settlement_fees():
    pos = _fly(fees=0.0)
    hold = coh.hold_distribution(pos, 100.0, _moves(0.0, 0.10))
    # at 100: payoff 5 -> 525, the 95 call ITM ($5); at 110: payoff 0 -> 25, all three ITM ($15)
    assert hold["mean_pnl"] == pytest.approx(((525 - 5) + (25 - 15)) / 2)
    assert hold["mean_value"] == pytest.approx(hold["mean_pnl"] - 25)
    assert hold["p_loss"] == 0
    assert hold["n"] == 2


def test_break_even_is_not_a_loss():
    # a call spread for 0.05 against $5.00 of fees, settling out of the money: exactly $0.00
    hold = coh.hold_distribution(_vertical(net=0.05, fees=5.0), 90.0, _moves(0.0))
    assert hold["mean_pnl"] == pytest.approx(0.0)
    assert hold["p_loss"] == 0


def test_verdict_hold_and_close_on_a_synthetic_distribution():
    pos = _fly(fees=0.0)
    moves = _moves(*([0.0, 0.10] * 10))  # 20 sessions, mean pnl 265
    # close: sell 95 at 4.00, sell 105 at 0, buy 100 twice at 1.00 -> +2.00; pnl 25 + 200 - fees
    snap = {"calls": {95.0: _q(4.0, 4.2), 100.0: _q(0.9, 1.0), 105.0: _q(0.0, 0.05)}}
    row = coh.evaluate(pos, _quote_at(snap), 100.0, moves)
    close_pnl = 25 + 200 - core_fees.ic_close_fee("SPX", 1, legs=4, sell_legs=2)
    assert row["close_now"]["pnl"] == pytest.approx(close_pnl, abs=0.005)
    assert row["hold"]["expected_pnl"] == 265
    assert row["hold_minus_close"] == pytest.approx(265 - close_pnl, abs=0.01)
    assert row["verdict"] == f"hold: expected value higher by ${265 - close_pnl:,.2f}"

    rich = {"calls": {95.0: _q(7.0, 7.2), 100.0: _q(0.9, 1.0), 105.0: _q(0.0, 0.05)}}
    row = coh.evaluate(pos, _quote_at(rich), 100.0, moves)
    assert row["verdict"].startswith("close: closing now is higher than the expected hold by $")


def test_too_few_sessions_gives_no_hold_estimate():
    snap = {"calls": {100.0: _q(4.3, 4.5), 105.0: _q(0.1, 0.2)}}
    row = coh.evaluate(_vertical(), _quote_at(snap), 100.0, _moves(*[0.0] * 19))
    assert row["hold"] is None
    assert row["verdict"] == "no hold estimate: only 19 sessions in the window (need 20)"


def test_the_printed_close_row_adds_up_to_the_cent():
    snap = {"calls": {100.0: _q(4.3, 4.4), 105.0: _q(0.05, 0.1)}}
    row = coh.evaluate(_vertical(fees=3.4433), _quote_at(snap), None, [])
    c = row["close_now"]
    assert round(c["exit_cash"] - c["close_fees"], 2) == c["value"]
    assert round(row["entry_net"] - row["fees_paid"] + c["value"], 2) == c["pnl"]


@pytest.mark.parametrize("side", [fly.PUT, fly.CALL])
def test_a_completed_fly_for_a_credit_never_reads_below_its_worst_case(side):
    pos = _fly(side=side)
    floor = fly.position_floor(pos)
    assert floor > 0  # net 0.25 against $6.89 of fees and three $5 strikes
    moves = _moves(*[k / 1000 for k in range(-200, 201)])  # +-20% in 0.1% steps, past both wings
    hold = coh.hold_distribution(pos, 100.0, moves)
    assert hold["min"] == pytest.approx(floor)
    assert hold["mean_pnl"] >= floor
    assert hold["p10"] >= floor
    assert hold["p_loss"] == 0


# --------------------------------------------------------------------------- the window
def test_the_window_takes_the_nearest_reading_within_five_minutes_and_excludes_today():
    trail = [
        ("2026-09-01", _ts("2026-09-01", 12, 1), 100.0),  # nearer: this one
        ("2026-09-01", _ts("2026-09-01", 12, 4), 101.0),  # read after the nearer one, and must not replace it
        ("2026-09-02", _ts("2026-09-02", 11, 55), 200.0),  # exactly five minutes: in
        ("2026-09-03", _ts("2026-09-03", 12, 5, 1), 300.0),  # just over five: out
        ("2026-09-04", _ts("2026-09-04", 12, 0), 400.0),  # no close on file
        ("2025-11-28", _ts("2025-11-28", 12, 0), 500.0),  # day after Thanksgiving: early close
        ("2026-10-09", _ts("2026-10-09", 12, 0), 600.0),  # today
        ("2026-09-08", _ts("2026-09-08", 12, 0), 0.0),  # a zero spot is no reading
    ]
    closes = {"2026-09-01": 102.0, "2026-09-02": 198.0, "2026-09-03": 303.0, "2025-11-28": 505.0}
    closes.update({"2026-10-09": 606.0, "2026-09-08": 100.0})
    out = coh.session_moves(trail, closes, today="2026-10-09", clock_min=12 * 60)
    assert [m["trade_date"] for m in out["moves"]] == ["2026-09-01", "2026-09-02"]
    assert out["moves"][0]["r"] == pytest.approx(0.02)
    assert out["moves"][1]["r"] == pytest.approx(-0.01)
    assert out["excluded"] == {
        "today_or_later": 1,
        "no_close": 1,
        "early_close": 1,
        "no_reading_in_window": 2,
    }


# --------------------------------------------------------------------------- the CLI
def test_cli_refuses_a_junk_clock_rather_than_defaulting(tmp_path, capsys):
    from cherrypick.flies import cli

    ledger, gex = tmp_path / "ledger.db", tmp_path / "gex.db"
    ledger.touch()
    gex.touch()
    argv = ["close-or-hold", "--ledger", str(ledger), "--gex-db", str(gex), "--at", "junk"]
    assert cli.main(argv) == 2
    assert "--at wants HH:MM" in capsys.readouterr().out


# --------------------------------------------------------------------------- the reader
def _ledger(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE fly_positions (id INTEGER PRIMARY KEY, position_id TEXT, trade_date TEXT, arm TEXT, "
        "symbol TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, net REAL, fees REAL, quantity INT, "
        "status TEXT, void_reason TEXT, entry_fill_status TEXT, completion_fill_status TEXT)"
    )
    for r in rows:
        cols = ", ".join(r)
        conn.execute(
            f"INSERT INTO fly_positions ({cols}) VALUES ({', '.join('?' * len(r))})", list(r.values())
        )
    return conn


def _gex(days):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL)")
    conn.execute("CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL)")
    for day, r in days:
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, 100.0)", (day, _ts(day, 12, 0)))
        conn.execute("INSERT INTO daily_closes VALUES ('SPX', ?, ?)", (day, 100.0 * (1 + r)))
    return conn


def _base(**kw):
    row = {k: v for k, v in _vertical().items()}
    row.update({"trade_date": "2026-10-09", "entry_fill_status": "filled"}, **kw)
    return row


def test_report_reads_only_todays_filled_unvoided_open_rows():
    ledger = _ledger(
        [
            _base(position_id="ok"),
            _base(position_id="old", trade_date="2026-10-08"),
            _base(position_id="void", void_reason="bad"),
            _base(position_id="cancelled", entry_fill_status="cancelled"),
            _base(position_id="working", entry_fill_status="pending"),
            _base(position_id="settled", status="settled"),
        ]
    )
    days = [(f"2026-09-{d:02d}", 0.0) for d in range(1, 26)]
    snap = {"ok": True, "underlying_price": 100.0, "calls": {100.0: _q(1.0, 1.1), 105.0: _q(0.1, 0.2)}}
    out = coh.report(
        ledger, snapshot_for=lambda s: snap, gex_conn=_gex(days), now=datetime(2026, 10, 9, 12, 0, tzinfo=ET)
    )
    assert [p["position_id"] for p in out["positions"]] == ["ok"]
    assert out["earlier_open_rows_not_evaluated"] == 1
    assert out["symbols"]["SPX"]["n_sessions"] == 25
    assert out["positions"][0]["hold"]["n_sessions"] == 25
    assert out["scaled"] is False


def test_report_gives_no_hold_estimate_outside_regular_hours():
    ledger = _ledger([_base()])
    days = [(f"2026-09-{d:02d}", 0.0) for d in range(1, 26)]
    snap = {"ok": True, "underlying_price": 100.0, "calls": {100.0: _q(1.0, 1.1), 105.0: _q(0.1, 0.2)}}
    out = coh.report(
        ledger,
        snapshot_for=lambda s: snap,
        gex_conn=_gex(days),
        now=datetime(2026, 10, 9, 12, 0, tzinfo=ET),
        clock_min=16 * 60 + 5,
    )
    row = out["positions"][0]
    assert row["hold"] is None
    assert row["verdict"] == "no hold estimate: outside regular hours"
