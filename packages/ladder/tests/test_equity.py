"""analytics.equity: each arm's book by session, realized and marked at the session's last bid."""

from cherrypick.ladder import analytics, rules

THU, FRI, MON, TUE = "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"


def _lot(conn, pid, arm, *, qty, buy, bought, sold=None, sell=None, symbol="TQQQ"):
    m = rules.money(qty, buy, sell) if sold else {"entry_value": round(-qty * buy, 2)}
    conn.execute(
        "INSERT INTO ladder_positions (position_id, arm, symbol, entry_rule, target_rule, quantity, "
        "buy_level, buy_price, buy_session, buy_time, status, sell_price, sell_session, entry_value, "
        "exit_value, gross_pnl, fees, settlement, slippage, net_pnl) "
        "VALUES (?, ?, ?, 'once', 'fixed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid,
            arm,
            symbol,
            qty,
            buy,
            buy,
            bought,
            bought + "T10:00:00-05:00",
            "closed" if sold else "open",
            sell,
            sold,
            m["entry_value"],
            m.get("exit_value"),
            m.get("gross_pnl"),
            m.get("fees"),
            m.get("settlement"),
            m.get("slippage"),
            m.get("net_pnl"),
        ),
    )
    conn.commit()
    return m


def _tick(conn, day, bid, *, symbol="TQQQ", hhmm="19:59", status="ok"):
    conn.execute(
        "INSERT INTO ladder_snapshots (ts, trade_date, symbol, kind, status, bid, ask) "
        "VALUES (?, ?, ?, 'tick', ?, ?, ?)",
        (f"{day}T{hhmm}:00-05:00", day, symbol, status, bid, None if bid is None else bid + 0.02),
    )
    conn.commit()


def test_realized_climbs_while_the_marked_line_shows_the_held_loss(ledger):
    won = _lot(ledger, "a:1", "a", qty=10, buy=93.0, bought=THU, sold=FRI, sell=96.72)
    _lot(ledger, "a:2", "a", qty=10, buy=90.0, bought=FRI)
    _tick(ledger, THU, 92.0)
    _tick(ledger, FRI, 80.0, hhmm="12:00")
    _tick(ledger, FRI, 81.0)  # the session's last tick is the mark
    points = analytics.equity(ledger)["arms"]["a"]
    thu, fri = points
    assert thu["realized"] == 0.0 and thu["open_lots"] == 1
    assert thu["unrealized"] == rules.money(10, 93.0, 92.0)["net_pnl"]
    held = rules.money(10, 90.0, 81.0)["net_pnl"]
    assert fri["realized"] == won["net_pnl"] > 0
    assert fri["unrealized"] == held < 0
    assert fri["marked"] == round(won["net_pnl"] + held, 2) < fri["realized"]
    assert fri["open_lots"] == 1 and fri["open_cost"] == 900.0


def test_a_lot_bought_and_sold_in_one_session_is_realized_only(ledger):
    m = _lot(ledger, "a:1", "a", qty=10, buy=93.0, bought=THU, sold=THU, sell=96.72)
    _tick(ledger, THU, 70.0)
    (thu,) = analytics.equity(ledger)["arms"]["a"]
    assert thu == {
        "session": THU,
        "realized": m["net_pnl"],
        "unrealized": 0.0,
        "marked": m["net_pnl"],
        "open_lots": 0,
        "open_cost": 0.0,
        "unmarked": 0,
    }


def test_a_session_with_no_bid_is_a_gap_never_a_carried_price(ledger):
    _lot(ledger, "a:1", "a", qty=10, buy=93.0, bought=THU)
    _tick(ledger, THU, 92.0)
    _tick(ledger, FRI, None, status="no_fresh_quote")  # the loop ran and read no quote
    _tick(ledger, TUE, 95.0)  # Monday: the loop never ran
    points = analytics.equity(ledger)["arms"]["a"]
    assert [p["session"] for p in points] == [THU, FRI, MON, TUE]  # every trading day, no weekend
    for p in points[1:3]:
        assert p["unrealized"] is None and p["marked"] is None and p["unmarked"] == 1
        assert p["realized"] == 0.0 and p["open_lots"] == 1
    assert points[3]["marked"] == rules.money(10, 93.0, 95.0)["net_pnl"]


def test_each_arm_starts_at_its_first_buy_and_can_be_read_alone(ledger):
    _lot(ledger, "a:1", "a", qty=10, buy=93.0, bought=THU)
    _lot(ledger, "b:1", "b", qty=5, buy=90.0, bought=FRI)
    _tick(ledger, THU, 92.0)
    _tick(ledger, FRI, 91.0)
    arms = analytics.equity(ledger)["arms"]
    assert [p["session"] for p in arms["a"]] == [THU, FRI]
    assert [p["session"] for p in arms["b"]] == [FRI]
    assert list(analytics.equity(ledger, "b")["arms"]) == ["b"]


def test_an_empty_ledger_has_no_arms(ledger):
    assert analytics.equity(ledger) == {"arms": {}}
