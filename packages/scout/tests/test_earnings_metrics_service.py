"""``entry_reviews`` DDL below is copied verbatim from ``cherrypick.earnings.db_paper`` (not
imported -- scout must never import ``cherrypick.earnings``, see CLAUDE.md's invariants) so a
schema drift between the two would show up here as a test failure the next time someone updates
this file by hand.
"""

import json
import sqlite3

import pytest

from cherrypick.scout.services import calendar_service, earnings_metrics_service

_ENTRY_REVIEWS_DDL = """
CREATE TABLE IF NOT EXISTS entry_reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date      TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    timing         TEXT,
    strategy       TEXT,
    price          REAL,
    volume         REAL,
    winrate        REAL,
    winrate_sample INTEGER,
    iv_rv_ratio    REAL,
    iv_rv_source   TEXT,
    term_structure REAL,
    market_cap     REAL,
    expected_move  REAL,
    expected_move_pct       REAL,
    combined_open_interest  REAL,
    combined_option_volume  REAL,
    bid_ask_spread_pct      REAL,
    net_combo_spread_pct    REAL,
    avg_actual_move_pct     REAL,
    move_dispersion_pct     REAL,
    max_actual_move_pct     REAL,
    implied_vs_avg_actual   REAL,
    move_tail_veto INTEGER,
    iv_rank        REAL,
    iv_percentile  REAL,
    composite_score REAL,
    best_tier      TEXT,
    selected       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    criteria_json  TEXT,
    logged_at      REAL,
    profile        TEXT NOT NULL DEFAULT 'default',
    UNIQUE(scan_date, symbol, profile)
);
"""


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(_ENTRY_REVIEWS_DDL)
    for row in rows:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO entry_reviews ({cols}) VALUES ({placeholders})", tuple(row.values()))
    conn.commit()
    conn.close()


def _row(scan_date, symbol, **overrides):
    base = {
        "scan_date": scan_date,
        "symbol": symbol,
        "timing": "AMC",
        "strategy": "iron_fly",
        "composite_score": None,
        "selected": 0,
        "reason": None,
        "best_tier": None,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def paper_db(tmp_path, monkeypatch):
    path = tmp_path / "paper_trades.db"
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: path)
    return path


def test_get_screen_dates_missing_db_is_empty_not_an_exception(paper_db):
    assert earnings_metrics_service.get_screen_dates("paper") == []


def test_get_screens_missing_db_is_graceful(paper_db):
    result = earnings_metrics_service.get_screens(mode="paper")
    assert result == {
        "ok": True,
        "mode": "paper",
        "scan_date": None,
        "rows": [],
        "note": "no earnings database found yet",
    }


def test_get_screen_dates_orders_most_recent_first(paper_db):
    _make_db(
        paper_db,
        [
            _row("2026-08-01", "AAPL"),
            _row("2026-08-05", "MSFT"),
            _row("2026-08-03", "NVDA"),
        ],
    )
    assert earnings_metrics_service.get_screen_dates("paper") == ["2026-08-05", "2026-08-03", "2026-08-01"]


def test_get_screens_defaults_to_most_recent_scan_date(paper_db):
    _make_db(
        paper_db,
        [
            _row("2026-08-01", "AAPL"),
            _row("2026-08-05", "MSFT"),
        ],
    )
    result = earnings_metrics_service.get_screens(mode="paper")
    assert result["ok"] is True
    assert result["scan_date"] == "2026-08-05"
    assert [r["symbol"] for r in result["rows"]] == ["MSFT"]


def test_get_screens_orders_by_composite_score_desc_with_nulls_last(paper_db):
    _make_db(
        paper_db,
        [
            _row("2026-08-05", "LOWSCORE", composite_score=1.0),
            _row("2026-08-05", "NOSCORE", composite_score=None),
            _row("2026-08-05", "HISCORE", composite_score=9.0),
        ],
    )
    result = earnings_metrics_service.get_screens("2026-08-05", mode="paper")
    assert [r["symbol"] for r in result["rows"]] == ["HISCORE", "LOWSCORE", "NOSCORE"]


def test_get_screens_returns_plain_json_serializable_dicts(paper_db):
    _make_db(paper_db, [_row("2026-08-05", "AAPL", selected=1, best_tier="A")])
    result = earnings_metrics_service.get_screens("2026-08-05", mode="paper")
    row = result["rows"][0]
    assert isinstance(row, dict)
    assert row["symbol"] == "AAPL"
    assert row["selected"] == 1
    assert row["best_tier"] == "A"


def test_get_screens_unknown_scan_date_is_graceful(paper_db):
    _make_db(paper_db, [_row("2026-08-05", "AAPL")])
    result = earnings_metrics_service.get_screens("2099-01-01", mode="paper")
    assert result["ok"] is True
    assert result["rows"] == []


def test_get_screens_predates_entry_reviews_table_is_graceful(tmp_path, monkeypatch):
    path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trades (order_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: path)

    result = earnings_metrics_service.get_screens(mode="paper")
    assert result["ok"] is True
    assert result["rows"] == []
    assert "entry_reviews" in result["note"]

    assert earnings_metrics_service.get_screen_dates("paper") == []


def test_live_and_paper_modes_read_different_files(tmp_path, monkeypatch):
    paper_path = tmp_path / "paper_trades.db"
    live_path = tmp_path / "earnings_trades.db"
    monkeypatch.setattr(earnings_metrics_service, "paper_db_path", lambda: paper_path)
    monkeypatch.setattr(earnings_metrics_service, "live_db_path", lambda: live_path)
    _make_db(paper_path, [_row("2026-08-05", "PAPERSYM")])
    _make_db(live_path, [_row("2026-08-05", "LIVESYM")])

    paper_result = earnings_metrics_service.get_screens(mode="paper")
    live_result = earnings_metrics_service.get_screens(mode="live")
    assert [r["symbol"] for r in paper_result["rows"]] == ["PAPERSYM"]
    assert [r["symbol"] for r in live_result["rows"]] == ["LIVESYM"]


def test_open_ro_connection_genuinely_cannot_write(paper_db):
    _make_db(paper_db, [_row("2026-08-05", "AAPL")])
    conn = earnings_metrics_service.open_ro(paper_db)
    assert conn is not None
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO entry_reviews (scan_date, symbol) VALUES ('2026-08-06', 'BAD')")
    finally:
        conn.close()


def test_open_ro_returns_none_for_a_missing_file(tmp_path):
    assert earnings_metrics_service.open_ro(tmp_path / "does-not-exist.db") is None


@pytest.mark.asyncio
async def test_get_upcoming_passes_through_a_failed_calendar_result(monkeypatch):
    async def fake_get_calendar(*_a, **_kw):
        return {"ok": False, "error": "broker unavailable"}

    monkeypatch.setattr(calendar_service, "get_calendar", fake_get_calendar)

    result = await earnings_metrics_service.get_upcoming(object(), object(), {}, days=14)
    assert result == {"ok": False, "error": "broker unavailable"}


@pytest.fixture()
def symbol_watch_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings_metrics_service, "_earnings_data_dir", lambda: tmp_path)
    return tmp_path


def _write_watch_snapshot(dir_path, symbols, **meta):
    payload = {
        "pass_started_at": meta.get("pass_started_at", 100.0),
        "pass_completed_at": meta.get("pass_completed_at", 200.0),
        "total": meta.get("total", len(symbols)),
        "done": meta.get("done", len(symbols)),
        "symbols": symbols,
    }
    (dir_path / "symbol_watch.json").write_text(json.dumps(payload))


def test_get_watch_status_missing_file_is_never_run(symbol_watch_dir):
    status = earnings_metrics_service.get_watch_status()
    assert status == {
        "ok": True,
        "never_run": True,
        "pass_started_at": None,
        "pass_completed_at": None,
        "total": 0,
        "done": 0,
    }


def test_get_watch_status_reflects_snapshot_progress(symbol_watch_dir):
    _write_watch_snapshot(
        symbol_watch_dir,
        {"AAPL": {"symbol": "AAPL"}},
        pass_started_at=100.0,
        pass_completed_at=None,
        total=5,
        done=1,
    )
    status = earnings_metrics_service.get_watch_status()
    assert status == {
        "ok": True,
        "never_run": False,
        "pass_started_at": 100.0,
        "pass_completed_at": None,
        "total": 5,
        "done": 1,
    }


def test_get_watch_status_corrupt_file_degrades_to_never_run(symbol_watch_dir):
    (symbol_watch_dir / "symbol_watch.json").write_text("{not json")
    status = earnings_metrics_service.get_watch_status()
    assert status["never_run"] is True


def test_merge_symbol_watch_attaches_matching_symbol_and_date(symbol_watch_dir):
    _write_watch_snapshot(
        symbol_watch_dir,
        {
            "AAPL": {
                "symbol": "AAPL",
                "earnings_date": "2026-08-05",
                "expected_move_pct": 0.045,
                "term_structure": -0.05,
                "winrate": 0.6,
                "winrate_sample": 8,
                "error": None,
                "refreshed_at": 123.0,
            }
        },
    )
    entries = [{"symbol": "AAPL", "date": "2026-08-05", "expected_move_pct": None}]
    merged = earnings_metrics_service._merge_symbol_watch(entries)
    assert merged[0]["expected_move_pct"] == 0.045
    assert merged[0]["term_structure"] == -0.05
    assert merged[0]["winrate"] == 0.6
    assert merged[0]["watch_refreshed_at"] == 123.0


def test_merge_symbol_watch_fills_market_cap_and_iv_rank_when_calendar_row_has_none(symbol_watch_dir):
    """The bug this guards: most Upcoming rows come from the broad Dolt fallback, which carries
    no market_cap/iv_rank/iv_percentile of its own (Dolt's earnings_calendar table has no such
    columns) -- those rows must not stay permanently blank when the scan already has the data."""
    _write_watch_snapshot(
        symbol_watch_dir,
        {
            "GME": {
                "symbol": "GME",
                "earnings_date": "2026-08-05",
                "market_cap": 12_000_000_000,
                "iv_rank": 0.42,
                "iv_percentile": 0.55,
            }
        },
    )
    entries = [
        {"symbol": "GME", "date": "2026-08-05", "market_cap": None, "iv_rank": None, "iv_percentile": None}
    ]
    merged = earnings_metrics_service._merge_symbol_watch(entries)
    assert merged[0]["market_cap"] == 12_000_000_000
    assert merged[0]["iv_rank"] == 0.42
    assert merged[0]["iv_percentile"] == 0.55


def test_merge_symbol_watch_never_overwrites_a_live_metrics_value(symbol_watch_dir):
    """A metrics-sourced row (the ~85-symbol "All Earnings" watchlist union) is fresher than the
    scan's own reading -- the scan must never clobber it, even when it has a different number."""
    _write_watch_snapshot(
        symbol_watch_dir,
        {"SMCI": {"symbol": "SMCI", "earnings_date": "2026-08-05", "market_cap": 999, "iv_rank": 0.99}},
    )
    entries = [{"symbol": "SMCI", "date": "2026-08-05", "market_cap": 20_137_157_330.0, "iv_rank": 0.755}]
    merged = earnings_metrics_service._merge_symbol_watch(entries)
    assert merged[0]["market_cap"] == 20_137_157_330.0
    assert merged[0]["iv_rank"] == 0.755


def test_merge_symbol_watch_drops_rows_when_earnings_date_disagrees(symbol_watch_dir):
    """A rescheduled earnings date should not silently attach a reading computed for the old
    date -- and since the display universe IS the scan universe now, a date mismatch drops the
    row entirely rather than showing it half-filled."""
    _write_watch_snapshot(
        symbol_watch_dir,
        {"AAPL": {"symbol": "AAPL", "earnings_date": "2026-08-04", "expected_move_pct": 0.045}},
    )
    entries = [{"symbol": "AAPL", "date": "2026-08-05", "expected_move_pct": None}]
    merged = earnings_metrics_service._merge_symbol_watch(entries)
    assert merged == []


def test_merge_symbol_watch_drops_symbols_outside_the_scan_universe(symbol_watch_dir):
    """A calendar row for a symbol the scan never touched (outside the liquid-enough universe,
    or not yet reached mid-pass) must not appear in Upcoming at all."""
    entries = [{"symbol": "TSLA", "date": "2026-08-05", "expected_move_pct": None}]
    merged = earnings_metrics_service._merge_symbol_watch(entries)
    assert merged == []


def test_merge_symbol_watch_includes_price_and_tier_fields(symbol_watch_dir):
    _write_watch_snapshot(
        symbol_watch_dir,
        {
            "AAPL": {
                "symbol": "AAPL",
                "earnings_date": "2026-08-05",
                "price": 220.5,
                "tier": "recommended",
                "tier_reasons": [],
            }
        },
    )
    entries = [{"symbol": "AAPL", "date": "2026-08-05"}]
    merged = earnings_metrics_service._merge_symbol_watch(entries)
    assert merged[0]["price"] == 220.5
    assert merged[0]["tier"] == "recommended"
    assert merged[0]["tier_reasons"] == []


@pytest.mark.asyncio
async def test_get_upcoming_merges_symbol_watch_and_reports_status(monkeypatch, symbol_watch_dir):
    _write_watch_snapshot(
        symbol_watch_dir,
        {"AAPL": {"symbol": "AAPL", "earnings_date": "2026-08-05", "winrate": 0.7}},
        total=1,
        done=1,
        pass_completed_at=200.0,
    )

    async def fake_get_calendar(*_a, **_kw):
        return {"ok": True, "entries": [{"symbol": "AAPL", "date": "2026-08-05", "winrate": None}]}

    monkeypatch.setattr(calendar_service, "get_calendar", fake_get_calendar)

    result = await earnings_metrics_service.get_upcoming(object(), object(), {}, days=14)
    assert result["entries"][0]["winrate"] == 0.7
    assert result["watch"] == {
        "ok": True,
        "never_run": False,
        "pass_started_at": 100.0,
        "pass_completed_at": 200.0,
        "total": 1,
        "done": 1,
    }


@pytest.mark.asyncio
async def test_get_upcoming_drops_rows_outside_the_scan_snapshot(monkeypatch, symbol_watch_dir):
    """The redesign's whole point: Upcoming only shows what the scan actually scanned, not
    calendar_service's broader Dolt-inclusive universe."""
    _write_watch_snapshot(
        symbol_watch_dir, {"AAPL": {"symbol": "AAPL", "earnings_date": "2026-08-05"}}, total=1, done=1
    )

    async def fake_get_calendar(*_a, **_kw):
        return {
            "ok": True,
            "entries": [
                {"symbol": "AAPL", "date": "2026-08-05"},
                {"symbol": "ILLIQUIDCO", "date": "2026-08-06"},  # never scanned -- must be dropped
            ],
        }

    monkeypatch.setattr(calendar_service, "get_calendar", fake_get_calendar)

    result = await earnings_metrics_service.get_upcoming(object(), object(), {}, days=10)
    assert [e["symbol"] for e in result["entries"]] == ["AAPL"]


@pytest.mark.asyncio
async def test_get_upcoming_sorts_tier_first_then_date_then_symbol(monkeypatch, symbol_watch_dir):
    _write_watch_snapshot(
        symbol_watch_dir,
        {
            "FAILCO": {"symbol": "FAILCO", "earnings_date": "2026-08-05", "tier": "fail"},
            "BBB": {"symbol": "BBB", "earnings_date": "2026-08-06", "tier": "recommended"},
            "AAA": {"symbol": "AAA", "earnings_date": "2026-08-06", "tier": "recommended"},
            "NEARCO": {"symbol": "NEARCO", "earnings_date": "2026-08-05", "tier": "near_miss"},
            "UNSCORED": {"symbol": "UNSCORED", "earnings_date": "2026-08-05", "tier": None},
        },
        total=5,
        done=5,
    )

    async def fake_get_calendar(*_a, **_kw):
        return {
            "ok": True,
            "entries": [
                {"symbol": "FAILCO", "date": "2026-08-05"},
                {"symbol": "BBB", "date": "2026-08-06"},
                {"symbol": "AAA", "date": "2026-08-06"},
                {"symbol": "NEARCO", "date": "2026-08-05"},
                {"symbol": "UNSCORED", "date": "2026-08-05"},
            ],
        }

    monkeypatch.setattr(calendar_service, "get_calendar", fake_get_calendar)

    result = await earnings_metrics_service.get_upcoming(object(), object(), {}, days=10)
    assert [e["symbol"] for e in result["entries"]] == ["AAA", "BBB", "NEARCO", "FAILCO", "UNSCORED"]


@pytest.mark.asyncio
async def test_get_upcoming_converts_trading_days_to_calendar_days(monkeypatch):
    from datetime import date

    from cherrypick.core import calendar as _calendar

    captured = {}

    async def fake_get_calendar(conn, session, cfg, watchlist_symbols, *, days=14, now=None):
        captured["days"] = days
        return {"ok": True, "entries": []}

    monkeypatch.setattr(calendar_service, "get_calendar", fake_get_calendar)

    await earnings_metrics_service.get_upcoming(object(), object(), {}, days=10)

    today = date.today()
    expected = (_calendar.nth_trading_day(today, 10) - today).days
    assert captured["days"] == expected
