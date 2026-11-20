"""The driver over the full set's intraday chain stores: a later expiry priced through the session, an
entry inside the session taken at its own minute (where the end-of-day chains could only count it
`needs_intraday`), the spot from the same-day store or, where that store holds nothing, the nearest
expiry's parity forward, and a bar never read past its store's interval."""

from __future__ import annotations

import sqlite3
from datetime import date

from market import CALENDAR
from market import rows as market_rows
from test_driver_eod import EXPIRY, gld, sessions

from cherrypick.automations import driver, history

#: The intraday chain stores' table, as the backtest's `intradayfetch.SCHEMA` writes it.
DDL = """
CREATE TABLE intraday_chain_quotes (
    symbol TEXT NOT NULL, session TEXT NOT NULL, expiration TEXT NOT NULL, bar TEXT NOT NULL,
    strike REAL NOT NULL, right TEXT NOT NULL,
    bid REAL, ask REAL, delta REAL, iv REAL, underlying REAL,
    PRIMARY KEY (symbol, session, expiration, bar, strike, right));
"""


def store(path, days, expiry, *, step, spot=300.0, moves=None, bars=None):
    """A store holding `expiry` each session at `step`-minute bars 09:30 to 16:00, rights spelt as the
    vendor's (CALL, PUT), no underlying (the Value tier's)."""
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    for s in days:
        if s > expiry:
            continue
        px = (moves or {}).get(s, spot)
        years = max((date.fromisoformat(expiry) - date.fromisoformat(s)).days, 0.25) / 365
        for m in bars or range(570, 961, step):
            for r in market_rows(px, years, 0.18, step=1.0, width=40.0):
                conn.execute(
                    "INSERT INTO intraday_chain_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("GLD", s, expiry, f"{m // 60:02d}:{m % 60:02d}", r["strike"],
                     "CALL" if r["right"] == "C" else "PUT", r["bid"], r["ask"], r["delta"], r["iv"], None),
                )  # fmt: skip
    conn.commit()
    conn.close()
    return path


BEFORE = ["2026-02-20", "2026-02-23", "2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27"]


def daily(days, spot=300.0, moves=None):
    move = moves or {}
    return [
        {"session": s, "open": spot, "high": spot, "low": spot, "close": move.get(s, spot)}
        for s in [*BEFORE, *days]
    ]


def test_the_spot_is_the_nearest_expirys_parity_forward_where_no_same_day_store_holds_the_session(tmp_path):
    days = sessions(3)
    h = history.IntradayChainHistory("GLD", [(store(tmp_path / "mid.db", days, EXPIRY, step=5), 5)],
                                     daily=daily(days))  # fmt: skip
    assert h.sessions == days
    assert h.minutes(days[0])[:3] == [570, 575, 580]
    assert abs(h.spot_at(days[0], 600) - 300.0) < 0.05
    assert h.listed(days[0], 600) == [EXPIRY]


def test_a_bar_is_read_until_its_interval_ends_and_never_after(tmp_path):
    days = sessions(1)
    h = history.IntradayChainHistory(
        "GLD", [(store(tmp_path / "mid.db", days, EXPIRY, step=5, bars=[600, 620]), 5)], daily=daily(days)
    )
    assert h.rows(days[0], EXPIRY, 600) and h.rows(days[0], EXPIRY, 604) == h.rows(days[0], EXPIRY, 600)
    assert h.rows(days[0], EXPIRY, 605) == [], "a five-minute bar is not read five minutes on"
    assert h.rows(days[0], EXPIRY, 599) == [], "never a bar from after the minute"
    row = h.rows(days[0], EXPIRY, 620)[0]
    assert row["right"] in ("C", "P") and row["gamma"] is None and row["oi"] is None


def test_an_expiry_two_stores_hold_is_read_from_the_finer(tmp_path):
    days = sessions(1)
    near = store(tmp_path / "near.db", days, EXPIRY, step=1, bars=[601])
    mid = store(tmp_path / "mid.db", days, EXPIRY, step=5, bars=[600])
    h = history.IntradayChainHistory("GLD", [(near, 1), (mid, 5)], daily=daily(days))
    assert h.rows(days[0], EXPIRY, 601) and h.rows(days[0], EXPIRY, 600) == []


def test_an_entry_inside_the_session_on_a_later_expiry_is_taken_at_its_minute(tmp_path):
    days = sessions()
    h = history.IntradayChainHistory(
        "GLD", [(store(tmp_path / "mid.db", days, EXPIRY, step=5, moves={EXPIRY: 320.0}), 5)],
        daily=daily(days, moves={EXPIRY: 320.0}),
    )  # fmt: skip
    book = driver.run({"gld": gld("12:30", 5)}, h, days, events=CALENDAR, vix_daily={})
    why = book.why["gld-call-spread-20d"]
    assert "needs_intraday" not in why and why.get("entered") == 1
    (trade,) = book.trades
    assert trade["session"] == days[0] and trade["entry_minute"] == 750 and trade["expiry"] == EXPIRY
