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
