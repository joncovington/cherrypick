"""Landing, against a fake Dolt that answers the queries the way the real one does.

The shape of the queries is the cost (month windows, no symbol filter), so the fake records what it
was asked and the tests check both the rows landed and the windows read.
"""

from __future__ import annotations

from datetime import date

import pytest

from cherrypick.technicals import land, store, vendor_check

TODAY = date(2026, 9, 28)


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows, self.calls = db, [], db.calls

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        s = sql.lower()
        if "from symbol" in s:
            self.rows = [(sym, 1 if sym == "ZZZ" else 0) for sym in self.db.listed]
        elif "from ohlcv" in s and "date = %s" in s:  # one session, the whole market: the rank's read
            (d,) = params
            self.rows = [(r[0], r[5], r[6]) for r in self.db.ohlcv if r[1].isoformat() == d]
        elif "from ohlcv" in s:
            lo, hi = (date.fromisoformat(p) for p in params)
            self.rows = [r for r in self.db.ohlcv if lo <= r[1] < hi]
        elif "from split" in s:
            self.rows = self.db.splits
        elif "from dividend" in s:
            self.rows = self.db.dividends
        elif "from volatility_history" in s:
            lo, hi = (date.fromisoformat(p) for p in params)
            self.rows = [r for r in self.db.iv if lo <= r[1] < hi]

    def fetchall(self):
        return list(self.rows)


class FakeDolt:
    def __init__(self):
        self.calls = []
        self.listed = ["AAA", "BBB", "ZZZ"]
        self.ohlcv = [
            ("AAA", date(2026, 9, 24), 10, 11, 9, 10, 100),
            ("AAA", date(2026, 9, 25), 10, 12, 10, 11, 100),
            ("ZZZ", date(2026, 9, 25), 1, 1, 1, 1, 1),  # not wanted
        ]
        self.splits = [("AAA", date(2026, 9, 25), 2, 1), ("ZZZ", date(2026, 1, 2), 2, 1)]
        self.dividends = [("AAA", date(2026, 8, 1), 0.1)]
        self.iv = [("AAA", date(2026, 9, 25), 0.30, 0.50, 0.20, 0.25)]

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


@pytest.fixture
def dolt(monkeypatch):
    fake = FakeDolt()
    monkeypatch.setattr(land, "_connect", lambda cfg, database: fake)
    return fake


def test_a_landing_keeps_only_wanted_symbols_and_reports_what_dolt_lacks(dolt):
    report = land.land(wanted=["AAA", "BBB", "SPX"], today=TODAY)
    assert report["ok"] and report["bars"] == 2 and report["splits"] == 1 and report["dividends"] == 1
    assert report["not_in_dolt"] == ["SPX"] and report["missing"] == ["BBB"]
    conn = store.connect()
    assert [b.close for b in store.adjusted_bars(conn, "AAA")] == [5.0, 11.0]
    assert store.iv_rank(conn, "AAA")["iv_rank"] == pytest.approx(33.3, abs=0.1)


def test_iv_rank_is_ours_where_dolt_has_it_and_tastytrades_where_it_does_not(dolt):
    """The fallback the user allowed: tastytrade's rank only where we cannot calculate one."""
    import json

    from cherrypick.technicals import paths

    land.land(wanted=["AAA"], today=TODAY)
    f = paths.tastytrade_iv_rank()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps(
            {
                "days": {
                    "2026-09-24": {"AAA": {"iv_rank": 90.0}, "ZZZ": {"iv_rank": 41.26, "iv_index": 0.31}},
                    "2026-09-25": {"ZZZ": {"iv_rank": 55.0}},
                }
            }
        ),
        encoding="utf-8",
    )
    conn = store.connect()
    assert store.iv_rank(conn, "AAA")["source"] == "dolt"  # ours wins where we have it
    assert store.iv_rank(conn, "ZZZ") == {
        "date": "2026-09-25",
        "iv": None,
        "iv_rank": 55.0,
        "source": "tastytrade",
    }
    assert store.iv_rank(conn, "ZZZ", "2026-09-24")["iv_rank"] == 41.3  # never a reading after `on`
    assert store.iv_rank(conn, "NONE") is None


def test_a_symbol_dolt_does_not_list_never_drags_the_read_back_to_a_full_backfill(dolt):
    """SPX, NDX and VIX made every morning a three-year read (3m 24s) until they were dropped."""
    land.land(wanted=["AAA"], today=TODAY)  # first landing backfills AAA
    dolt.calls.clear()
    land.land(wanted=["AAA", "SPX"], today=TODAY)
    ohlcv = [p for sql, p in dolt.calls if "from ohlcv" in sql.lower()]
    assert ohlcv and min(p[0] for p in ohlcv) >= "2026-09-01", "a restatement window, not a backfill"


def test_the_bar_query_never_filters_by_symbol(dolt):
    """A symbol filter makes Dolt walk the date-led key: 27.5 s a month against 4.4 s unfiltered."""
    land.land(wanted=["AAA"], today=TODAY)
    for sql, _ in dolt.calls:
        if "from ohlcv" in sql.lower() or "from volatility_history" in sql.lower():
            assert "act_symbol in" not in sql.lower()


def test_landing_twice_is_idempotent(dolt):
    land.land(wanted=["AAA"], today=TODAY)
    land.land(wanted=["AAA"], today=TODAY)
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 2


def test_no_dolt_server_is_a_reported_failure(monkeypatch):
    def refuse(cfg, database):
        raise ConnectionRefusedError("no server")

    monkeypatch.setattr(land, "_connect", refuse)
    report = land.land(wanted=["AAA"], today=TODAY)
    assert report["ok"] is False and "dolt unreachable" in report["reason"]


def test_plan_starts_backfills_new_symbols_and_restates_known_ones():
    starts = land.plan_starts(["NEW", "OLD"], {"OLD": "2026-09-25"}, TODAY)
    assert starts["OLD"] == "2026-09-15"
    assert starts["NEW"] < "2023-10-01"


def test_windows_cover_every_day_once():
    ws = land.windows("2026-07-01", TODAY)
    assert ws[0][0] == "2026-07-01" and ws[-1][1] == "2026-09-29"
    assert all(a[1] == b[0] for a, b in zip(ws, ws[1:], strict=False))


# --------------------------------------------------------------------------- the vendor check


def test_the_vendor_check_counts_a_cent_as_agreement_and_more_as_a_miss():
    ours = {"2026-09-25": {"open": 10.00, "high": 11.0, "low": 9.0, "close": 10.505}}
    vendor = [{"date": "2026-09-25T00:00:00", "open": 10.01, "high": 11.0, "low": 9.0, "close": 10.49}]
    result = vendor_check.compare(ours, vendor)
    assert (result["prices"], result["agree"]) == (4, 3)
    assert result["worst"][0][1] == "close"


def test_the_landing_records_which_symbols_are_funds_so_breadth_counts_stocks_only(dolt):
    dolt.listed.append("ETF1")
    dolt.ohlcv.append(("ETF1", date(2026, 9, 25), 1, 1, 1, 1, 1))
    land.land(wanted=["AAA", "ETF1"], today=TODAY)
    conn = store.connect()
    conn.execute("UPDATE listings SET is_etf = 1 WHERE symbol = 'ETF1'")
    assert store.stocks(conn, ["AAA", "ETF1", "UNLANDED"]) == ["AAA", "UNLANDED"]


def test_every_captured_name_is_landed_so_its_capture_can_be_scored():
    from cherrypick.technicals import paths, symbols

    folder = paths.market_report_dir() / "vendor-charts" / "2026-09-25"
    folder.mkdir(parents=True)
    for name in ("SGOV.json", "LOGC.rejected.json", "trade-ideas.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    assert symbols.captured() == ["LOGC", "SGOV"]
    assert {"LOGC", "SGOV"} <= set(symbols.all_symbols())


def test_the_landing_stores_the_whole_markets_rank_cutoffs_for_recent_sessions(dolt):
    """The rank is a decile across every name Dolt carries, not just the wanted ones: 200 unwanted
    names with rising scores set the cut-offs; a split inside the window and a name trading under
    $100k a day are left out; a session that already has cut-offs is not re-read."""
    from datetime import timedelta

    from cherrypick.technicals import levels

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(140)]
    dolt.listed = ["SPY"]
    dolt.ohlcv = [("SPY", d, 1, 1, 1, 1, 1) for d in days]
    for k in range(200):  # name k gains k% over the long window, flat over the short one
        dolt.ohlcv += [
            (f"M{k}", d, 0, 0, 0, 100.0 if i <= 139 - 126 else 100.0 + k, 10_000) for i, d in enumerate(days)
        ]
    dolt.ohlcv += [("SPLIT", d, 0, 0, 0, 1.0 if i < 130 else 1000.0, 1e9) for i, d in enumerate(days)]
    dolt.ohlcv += [("THIN", d, 0, 0, 0, 1.0 if i < 130 else 1000.0, 1) for i, d in enumerate(days)]
    dolt.splits = [("SPLIT", days[130], 1, 10)]
    land.land(wanted=["SPY"], today=days[-1] + timedelta(days=1))
    conn = store.connect()
    cut = store.rank_cutoffs(conn, days[-1].isoformat())
    assert cut is not None and len(cut) == 9
    assert levels.rank_from_cutoffs(1.99, cut) == 10  # M199: +199%, the top decile of 200 names
    assert levels.rank_from_cutoffs(0.0, cut) == 1
    universe = conn.execute(
        "SELECT universe FROM rank_cutoffs WHERE session = ?", (days[-1].isoformat(),)
    ).fetchone()[0]
    assert universe == 200, (
        "SPLIT (a split inside the window) and THIN (under $100k a day) are not the market"
    )
    reads = sum("date = %s" in sql for sql, _ in dolt.calls)
    land.land(wanted=["SPY"], today=days[-1] + timedelta(days=1))
    assert sum("date = %s" in sql for sql, _ in dolt.calls) == reads, "sessions with cut-offs are not re-read"


def test_a_thin_market_day_stores_no_rank_cutoffs(dolt):
    from datetime import timedelta

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(140)]
    dolt.listed = ["SPY"]
    dolt.ohlcv = [("SPY", d, 1, 1, 1, 1, 1e9) for d in days]  # one name is not a market
    land.land(wanted=["SPY"], today=days[-1] + timedelta(days=1))
    assert store.rank_cutoffs(store.connect(), days[-1].isoformat()) is None
