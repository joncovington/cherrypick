"""Today's market conditions in an automation's record (`conditions`): its trades cut by each entry
session's daily-bar label, assembled and stamped by `core.regimecuts`. Synthetic trades and bars only."""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core.metrics import MIN_EFFECTIVE_N

from cherrypick.automations import conditions

NAME = "mine"


def _days(n: int, start: str = "2026-06-01") -> list[str]:
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _trade(session: str, net: float, symbol: str = "SPX") -> dict:
    return {"entry_session": session, "symbol": symbol, "net_pnl": net, "gross_pnl": net + 4, "cost": 4}


def _cut(trades, by_symbol, today, era_start=None, symbol="SPX"):
    return conditions.cut(
        NAME, symbol, trades, by_symbol, today, "2026-11-23", era_start, "2026-11-23T09:00:00"
    )


def _measured(trend: str, vol: str) -> dict:
    return {
        "status": "measured",
        "trend": trend,
        "vol": vol,
        "atr_percentile": 0.8,
        "sma_slope": -2.0,
        "session": "2026-11-20",
    }


def test_each_trade_takes_its_entry_sessions_label_and_today_reads_its_cell():
    days = _days(20)
    labels = {s: {"trend": "range" if i % 2 else "downtrend", "vol": "high"} for i, s in enumerate(days)}
    trades = [_trade(s, 10.0 if i % 2 else -5.0) for i, s in enumerate(days)]
    doc = _cut(trades, {"SPX": labels}, {"SPX": _measured("downtrend", "high")})
    arm = doc["arms"][0]
    trend = {b["bucket"]: b for b in arm["dimensions"]["trend"]["buckets"]}
    assert trend["downtrend"]["trades"] == 10 and trend["downtrend"]["net_pnl"] == -50.0
    assert trend["range"]["net_pnl"] == 100.0
    # Gross less cost is net, cell by cell.
    assert trend["range"]["gross_pnl"] - trend["range"]["fees"] == trend["range"]["net_pnl"]
    assert doc["today"]["buckets"] == {"trend": "downtrend", "vol": "high"}
    cell = arm["today"]["trend"]
    assert cell["bucket"] == "downtrend" and cell["trades"] == 10 and cell["net_pnl"] == -50.0
    # Twenty sessions clear the minimum, so the trend cut reads; every trade in one vol bucket does not.
    assert cell["readable"] is True
    assert arm["today"]["vol"] == {"bucket": "high", "readable": False, "why": "degenerate"}
    assert doc["today"]["labels"]["SPX"]["through"] == "2026-11-20"


def test_under_the_minimum_of_sessions_a_cut_is_underpowered_not_read():
    days = _days(MIN_EFFECTIVE_N - 1)
    labels = {
        s: {"trend": "range" if i % 2 else "uptrend", "vol": "low" if i % 3 else "high"}
        for i, s in enumerate(days)
    }
    doc = _cut([_trade(s, 1.0) for s in days], {"SPX": labels}, {"SPX": _measured("range", "low")})
    today = doc["arms"][0]["today"]
    assert today["trend"]["why"] == "underpowered" and today["vol"]["why"] == "underpowered"
    assert doc["arms"][0]["dimensions"]["trend"]["sessions_needed"] == 1


def test_a_trade_with_no_label_is_untagged_and_an_unmeasured_today_is_left_out():
    days = _days(3)
    doc = _cut(
        [_trade(d, 1.0) for d in days],
        {"SPX": {}},
        {"SPX": {"status": "unmeasured", "reason": "needs 60 bars, has 39"}},
    )
    buckets = doc["arms"][0]["dimensions"]["trend"]["buckets"]
    assert [b["bucket"] for b in buckets] == ["untagged"]
    assert doc["today"]["buckets"] == {} and doc["arms"][0]["today"] == {}
    assert doc["today"]["labels"]["SPX"] == {"status": "unmeasured", "reason": "needs 60 bars, has 39"}


def test_trades_before_the_era_are_left_out():
    days = _days(4)
    labels = {s: {"trend": "range", "vol": "low"} for s in days}
    doc = _cut(
        [_trade(s, 1.0) for s in days], {"SPX": labels}, {"SPX": _measured("range", "low")}, era_start=days[2]
    )
    assert doc["arms"][0]["trades"] == 2 and doc["era"]["start"] == days[2]


def test_labels_today_from_every_bar_given_and_each_past_session_from_the_bars_before_it():
    days = _days(400, "2025-01-02")
    bars = [
        {"session": s, "open": 100 + i, "high": 101 + i + (i % 7), "low": 99 + i, "close": 100.5 + i}
        for i, s in enumerate(days)
    ]
    past, now = conditions.labels(bars, "2026-11-23")
    assert now["status"] == "measured" and past["2026-11-23"] == {"trend": now["trend"], "vol": now["vol"]}
    # A steady climb: every labelled session is an uptrend, and the first bars, too few to label, are not.
    assert {v["trend"] for s, v in past.items() if s != "2026-11-23"} == {"uptrend"}
    assert days[0] not in past
    none, nothing = conditions.labels([], "2026-11-23")
    assert none == {} and nothing == {"status": "unmeasured", "reason": "no daily bars"}
