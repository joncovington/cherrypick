"""facts.build against fixture stores shaped like the REAL cache: provenance labeling, the two
prior-close routes (today's summary row, last recorded trade), fallbacks, and graceful absence.

The fixtures deliberately leave ``day_close`` NULL everywhere, and this package reads the settled
close as ``prev_day_close`` on the NEXT session's row. That was originally justified by "the
producer stops at the bell"; it no longer does (it ran to 20:07 ET through August), and until
2026-08-27 a late-evening Summary event with a cleared ``day_close`` was erasing the real one for
SPX and XSP anyway. The fixture shape is still right for a different reason: pre-open — the only
window this package runs in — today's row does not exist yet, so ``prev_day_close`` is genuinely
the only route to the prior settle.
"""

import sqlite3
from datetime import UTC, datetime

from cherrypick.overview import facts, paths

SESSION = "2026-08-17"
PRIOR = "2026-08-14"
NOW = datetime(2026, 8, 17, 12, 30, tzinfo=UTC)  # 08:30 ET on the Monday
NOW_TS = NOW.timestamp()

# 20:00 UTC on Friday 2026-08-14 -- a last trade stamped at that session's close.
FRIDAY_CLOSE_TS = datetime(2026, 8, 14, 20, 0, tzinfo=UTC).timestamp()


def _make_cache(rows_summary=(), rows_trades=()):
    path = paths.stream_cache_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE stream_trades (symbol TEXT PRIMARY KEY, last REAL, change REAL, "
        "volume REAL, updated_at REAL NOT NULL);"
        "CREATE TABLE stream_summary (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, "
        "day_open REAL, day_high REAL, day_low REAL, day_close REAL, prev_day_close REAL, "
        "updated_at REAL NOT NULL, PRIMARY KEY (symbol, trade_date));"
    )
    conn.executemany(
        "INSERT INTO stream_summary (symbol, trade_date, prev_day_close, updated_at) VALUES (?, ?, ?, ?)",
        rows_summary,
    )
    conn.executemany("INSERT INTO stream_trades (symbol, last, updated_at) VALUES (?, ?, ?)", rows_trades)
    conn.commit()
    conn.close()


def _make_gex(rows=()):
    path = paths.gex_history_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE gex_regime_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL, "
        "net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, "
        "expiration TEXT)"
    )
    conn.executemany(
        "INSERT INTO gex_regime_history (symbol, trade_date, ts, zero_gamma, call_wall, put_wall, "
        "net_gex) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_empty_home_builds_unmeasured_pack_not_a_crash():
    pack = facts.build(SESSION, now=NOW)
    assert pack["session"] == SESSION
    assert pack["readings"]["spx"]["value"] is None
    assert pack["phase"]["phase"] == "yellow"
    assert pack["phase"]["gates_measured"] == 0


def test_todays_summary_row_yields_the_prior_settle():
    # Monday's row exists (producer started pre-market): its prev_day_close IS Friday's settle.
    # Friday's own row provides Thursday's settle, which is what the daily change is against.
    _make_cache(
        rows_summary=[("SPX", SESSION, 7798.99, NOW_TS - 600), ("SPX", PRIOR, 7744.90, NOW_TS - 60000)]
    )
    pack = facts.build(SESSION, now=NOW)
    spx = pack["readings"]["spx"]
    assert spx["value"] == 7798.99
    assert spx["basis"] == "prior"
    assert spx["session"] == PRIOR
    change = pack["readings"]["spx_prior_change_pct"]
    assert round(change["value"], 2) == 0.70
    assert change["session"] == PRIOR


def test_without_todays_row_the_last_trade_is_the_prior_value():
    # No Monday row yet (midnight build): the Friday-stamped last trade is the confirmed prior,
    # dated to ITS OWN session, with the change computed against Friday's row's prev_day_close.
    _make_cache(
        rows_summary=[("SPX", PRIOR, 7744.90, NOW_TS - 60000)],
        rows_trades=[("SPX", 7798.50, FRIDAY_CLOSE_TS)],
    )
    pack = facts.build(SESSION, now=NOW)
    spx = pack["readings"]["spx"]
    assert spx["value"] == 7798.50
    assert spx["basis"] == "prior"
    assert spx["session"] == PRIOR
    assert "last trade" in spx["source"]
    assert round(spx["prior_change_pct"], 2) == 0.69


# 05:20 UTC on Monday 2026-08-17 = 01:20 ET the SAME morning: an overnight print carrying Friday's
# close. This is what production actually holds when overview runs, and nothing covered it — the
# test above stamps its trade inside the PRIOR session's ET date, where the lookup happened to work.
OVERNIGHT_TS = datetime(2026, 8, 17, 5, 20, tzinfo=UTC).timestamp()


def test_an_overnight_print_is_dated_to_the_session_it_belongs_to():
    """The gate this feeds had NEVER been measured in production (checked 2026-08-27: null on every
    stored pack). An overnight print carries the prior close, but `_et_date` calls it today, so the
    base was looked up on today's summary row — which does not exist pre-open, the only window this
    package runs in. The change came back None every morning and the prior session was labelled as
    the current one."""
    _make_cache(
        rows_summary=[("SPX", PRIOR, 7744.90, NOW_TS - 60000)], rows_trades=[("SPX", 7798.50, OVERNIGHT_TS)]
    )
    pack = facts.build(SESSION, now=NOW)
    spx = pack["readings"]["spx"]
    assert spx["prior_session"] == PRIOR, "an overnight print belongs to the prior session"
    assert round(spx["prior_change_pct"], 2) == 0.69
    change = pack["readings"]["spx_prior_change_pct"]
    assert round(change["value"], 2) == 0.69


def test_the_calm_tape_gate_is_measurable_pre_open():
    """The whole point: five declared gates, and one of them could never participate."""
    _make_cache(
        rows_summary=[("SPX", PRIOR, 7744.90, NOW_TS - 60000)], rows_trades=[("SPX", 7798.50, OVERNIGHT_TS)]
    )
    pack = facts.build(SESSION, now=NOW)
    calm = [g for g in pack["gates"] if g["id"] == "calm_tape"][0]
    assert calm["status"] == "met"
    assert "not measured" not in calm["detail"]


def test_fresh_trade_is_live_and_still_carries_the_prior_change():
    _make_cache(
        rows_summary=[("SPX", SESSION, 7798.99, NOW_TS - 600), ("SPX", PRIOR, 7744.90, NOW_TS - 60000)],
        rows_trades=[("SPX", 7801.25, NOW_TS - 300)],
    )
    pack = facts.build(SESSION, now=NOW)
    spx = pack["readings"]["spx"]
    assert spx["basis"] == "live"
    assert spx["value"] == 7801.25
    assert spx["session"] == SESSION
    assert spx["prior_close"] == 7798.99
    assert round(spx["prior_change_pct"], 2) == 0.70


def test_a_stale_trade_is_never_live():
    _make_cache(rows_trades=[("SPX", 7785.76, NOW_TS - 3 * 3600)])
    pack = facts.build(SESSION, now=NOW)
    assert pack["readings"]["spx"]["basis"] == "prior"


def test_vix_falls_back_to_meic_market_context():
    db = paths.meic_paper_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE market_context (context_date TEXT, vix REAL, vix1d REAL, "
        "vix1d_ratio REAL, symbols_json TEXT, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO market_context (context_date, vix, updated_at) VALUES (?, ?, ?)",
        (PRIOR, 14.85, NOW_TS - 60000),
    )
    conn.commit()
    conn.close()
    pack = facts.build(SESSION, now=NOW)
    vix = pack["readings"]["vix"]
    assert vix["value"] == 14.85
    assert vix["source"] == "meic.market_context"


def test_gex_levels_and_sector_board_flow_into_gates():
    summaries = [
        ("SPX", SESSION, 7798.99, NOW_TS - 600),
        ("SPX", PRIOR, 7744.90, NOW_TS - 60000),
        ("VIX", SESSION, 14.60, NOW_TS - 600),
        ("VIX3M", SESSION, 18.91, NOW_TS - 600),
        ("VVIX", SESSION, 90.90, NOW_TS - 600),
        ("XLC", SESSION, 100.0, NOW_TS - 600),
        ("XLC", PRIOR, 98.42, NOW_TS - 60000),
        ("XLE", SESSION, 90.0, NOW_TS - 600),
        ("XLE", PRIOR, 91.50, NOW_TS - 60000),
    ]
    _make_cache(rows_summary=summaries)
    _make_gex(rows=[("SPX", PRIOR, NOW_TS - 70000, 7560.58, 7800.0, 7500.0, 1.2e9)])
    pack = facts.build(SESSION, now=NOW)

    assert pack["levels"]["zero_gamma"] == 7560.58
    assert pack["levels"]["session"] == PRIOR
    assert pack["levels"]["reference_price"] == 7798.99

    sectors = pack["sectors"]
    assert sectors["strongest"]["symbol"] == "XLC"
    assert sectors["weakest"]["symbol"] == "XLE"
    assert sectors["measured"] == 2

    assert pack["phase"]["phase"] == "green"


def test_close_history_dates_each_column_to_its_own_session():
    # The two columns are dated differently and mixing them up shifts the whole series:
    # day_close belongs to its OWN row's session (what the candle backfill writes), while
    # prev_day_close belongs to the session BEFORE its row (what the live producer leaves behind).
    _make_cache(
        rows_summary=[
            ("VIX", "2026-08-10", None, NOW_TS),  # backfilled: day_close set below
            ("VIX", "2026-08-11", 14.10, NOW_TS),  # live row -> 08-10 settled at 14.10
            ("VIX", "2026-08-12", 14.20, NOW_TS),  # live row -> 08-11 settled at 14.20
        ]
    )
    conn = sqlite3.connect(paths.stream_cache_db())
    conn.execute(
        "UPDATE stream_summary SET day_close = 13.90 WHERE symbol = 'VIX' AND trade_date = '2026-08-10'"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(paths.stream_cache_db())
    conn.row_factory = sqlite3.Row
    series = facts._close_history(conn, ["VIX"], SESSION, 270)["VIX"]
    conn.close()

    # day_close outranks the chained value on 08-10; 08-11 comes from 08-12's prev_day_close.
    assert series == [{"session": "2026-08-10", "close": 13.90}, {"session": "2026-08-11", "close": 14.20}]


def test_close_history_takes_the_freshest_close_off_todays_row():
    # The prior session's settle lives on TODAY's row. Skipping today's row to avoid its partial
    # bar would leave the series a session stale -- it is read for prev_day_close, then dropped.
    _make_cache(rows_summary=[("VIX", PRIOR, 14.10, NOW_TS), ("VIX", SESSION, 14.50, NOW_TS)])
    conn = sqlite3.connect(paths.stream_cache_db())
    conn.row_factory = sqlite3.Row
    series = facts._close_history(conn, ["VIX"], SESSION, 270)["VIX"]
    conn.close()
    assert [row["session"] for row in series] == [PRIOR]
    assert series[0]["close"] == 14.50


def test_deployment_block_is_present_and_records_that_it_governs_nothing():
    pack = facts.build(SESSION, now=NOW)
    deployment = pack["deployment"]
    assert deployment["record_only"] is True
    # An empty home measures nothing, so the block refuses a score rather than inventing one --
    # and the phase beside it is still computed by the gates alone.
    assert deployment["score"] is None
    assert pack["phase"]["phase"] == "yellow"


def test_write_then_read_roundtrip():
    pack = facts.build(SESSION, now=NOW)
    facts.write(pack)
    again = facts.read(SESSION)
    assert again is not None
    assert again["session"] == SESSION
    assert again["fact_version"] == facts.FACT_VERSION
