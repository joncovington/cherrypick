import pytest

from cherrypick.earnings import symbol_watch


@pytest.fixture()
def snapshot_path(tmp_path, monkeypatch):
    path = tmp_path / "symbol_watch.json"
    monkeypatch.setattr(symbol_watch, "_snapshot_path", lambda: path)
    return path


def _clean_entry(**overrides):
    base = {
        "price": 50.0,
        "combined_open_interest": 5000,
        "term_structure": -0.05,
        "expected_move_pct": 0.06,  # $3.00 expected move on a $50 stock
        "iv_rv_ratio": 1.5,
        "winrate": 0.6,
        "avg_volume": 2_000_000,
        "error": None,
    }
    base.update(overrides)
    return base


def test_classify_tier_clean_entry_is_recommended():
    tier, reasons = symbol_watch.classify_tier(_clean_entry(), {})
    assert tier == "recommended"
    assert reasons == []


def test_classify_tier_missing_chain_data_returns_none():
    entry = _clean_entry(price=None, term_structure=None, expected_move_pct=None, error="no chain data")
    tier, reasons = symbol_watch.classify_tier(entry, {})
    assert tier is None
    assert reasons == ["no chain data"]


def test_classify_tier_missing_chain_data_without_error_still_has_a_reason():
    entry = _clean_entry(combined_open_interest=None)
    tier, reasons = symbol_watch.classify_tier(entry, {})
    assert tier is None
    assert reasons == ["insufficient data to classify"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": 7.0, "expected_move_pct": 0.5},  # $5-10 near-miss band ($3.50 move, still clears $0.90)
        {"iv_rv_ratio": 1.1},  # 1.00-1.25 near-miss band
        {"winrate": 0.45},  # 0.40-0.50 near-miss band
        {"avg_volume": 1_200_000},  # 1M-1.5M near-miss band
        {"iv_rv_ratio": None},  # unavailable -> near-miss, not fail
        {"winrate": None},
        {"avg_volume": None},
    ],
)
def test_classify_tier_near_miss_bands(overrides):
    tier, reasons = symbol_watch.classify_tier(_clean_entry(**overrides), {})
    assert tier == "near_miss"
    assert len(reasons) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": 3.0, "expected_move_pct": 0.5},  # below near-miss floor ($1.50 move, still clears $0.90)
        {"winrate": 0.2},
        {"avg_volume": 100_000},
    ],
)
def test_classify_tier_fails_only_on_stable_criteria(overrides):
    """Price, winrate and average volume are settled by the time this scan runs — a name under
    those bars at 06:30 is still under them at 15:35, so it can be called out."""
    tier, reasons = symbol_watch.classify_tier(_clean_entry(**overrides), {})
    assert tier == "fail"
    assert len(reasons) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"combined_open_interest": 500},
        {"term_structure": 0.01},  # positive, above the -0.004 gate
        {"expected_move_pct": 0.001},  # $0.05 on a $50 stock, below $0.90
        {"iv_rv_ratio": 0.5},  # below the near-miss floor
    ],
)
def test_classify_tier_will_not_fail_a_name_on_a_perishable_reading(overrides):
    """Implied vol rises into an announcement, and term structure, expected move and open interest
    move with it. A morning reading under those bars is real but provisional — calling it a fail
    would state a verdict this scan cannot support. COHR on 2026-08-12 was marked fail on iv/rv
    0.96 against a 1.00 floor, hours before the print that would move it."""
    tier, reasons = symbol_watch.classify_tier(_clean_entry(**overrides), {})
    assert tier == "near_miss"
    assert len(reasons) == 1
    assert "may still clear by the entry window" in reasons[0]


def test_classify_tier_thresholds_come_from_the_strategies_own_config():
    """The badge must answer against the bars that will really be applied. A parallel ladder can
    agree today and drift silently tomorrow."""
    config = {"strategies": {"iron_fly": {"min_avg_volume": 5_000_000}}}
    tier, reasons = symbol_watch.classify_tier(_clean_entry(avg_volume=2_000_000), config)
    assert tier == "near_miss"
    assert "avg volume" in reasons[0]


def test_classify_tier_takes_the_loosest_bar_across_strategies():
    """The forced-sampling book takes every strategy that clears on a name, so one strategy's
    willingness is enough for the name to be worth showing."""
    config = {
        "strategies": {
            "iron_fly": {"min_avg_volume": 1_000_000},
            "double_calendar": {"min_avg_volume": 5_000_000},
        }
    }
    tier, _ = symbol_watch.classify_tier(_clean_entry(avg_volume=2_000_000), config)
    assert tier == "recommended"


def test_classify_tier_ignores_config_strategies_the_registry_no_longer_has():
    """`reverse_fly` sits in config with enabled: true and has not been in the registry for some
    time. Taking the loosest bar across raw config would let a dead strategy set the badge."""
    config = {"strategies": {"reverse_fly": {"min_avg_volume": 1}}}
    tier, _ = symbol_watch.classify_tier(_clean_entry(avg_volume=100_000), config)
    assert tier == "fail"


def test_classify_tier_multiple_near_misses_still_near_miss_not_fail():
    entry = _clean_entry(price=7.0, expected_move_pct=0.5, winrate=0.45)
    tier, reasons = symbol_watch.classify_tier(entry, {})
    assert tier == "near_miss"
    assert len(reasons) == 2


def test_classify_tier_any_fail_wins_over_near_miss():
    entry = _clean_entry(price=7.0, expected_move_pct=0.5, avg_volume=100_000)  # near-miss + fail together
    tier, reasons = symbol_watch.classify_tier(entry, {})
    assert tier == "fail"
    assert reasons == ["avg volume 100000 below 1000000"]


def test_classify_tier_respects_config_overrides():
    entry = _clean_entry(price=8.0, expected_move_pct=0.5)  # near-miss at defaults (min_price 10)
    config = {"symbol_watch": {"tier_thresholds": {"min_price": 7.0}}}
    tier, reasons = symbol_watch.classify_tier(entry, config)
    assert tier == "recommended"
    assert reasons == []


def test_collapse_to_nearest_date_dedupes_and_normalizes_symbol():
    rows = [
        {"symbol": "aapl", "date": "2026-08-20", "timing": "After market close"},
        {"symbol": "AAPL", "date": "2026-08-15", "timing": "Before market open"},
        {"symbol": "MSFT", "date": "2026-08-18", "timing": None},
    ]
    result = symbol_watch._collapse_to_nearest_date(rows)
    assert set(result) == {"AAPL", "MSFT"}
    assert result["AAPL"]["date"].isoformat() == "2026-08-15"
    assert result["AAPL"]["timing"] == "Before market open"


def test_collapse_to_nearest_date_skips_blank_symbol_or_missing_date():
    rows = [{"symbol": "", "date": "2026-08-20"}, {"symbol": "AAPL", "date": None}]
    assert symbol_watch._collapse_to_nearest_date(rows) == {}


def test_write_and_read_snapshot_round_trip(snapshot_path):
    assert symbol_watch.read_snapshot() == {
        "pass_started_at": None,
        "pass_completed_at": None,
        "total": 0,
        "done": 0,
        "symbols": {},
    }
    symbol_watch._write_snapshot({"AAPL": {"x": 1}}, 100.0, None, 5, 1)
    assert symbol_watch.read_snapshot() == {
        "pass_started_at": 100.0,
        "pass_completed_at": None,
        "total": 5,
        "done": 1,
        "symbols": {"AAPL": {"x": 1}},
    }
    # atomic: no leftover temp files after a successful write
    assert list(snapshot_path.parent.iterdir()) == [snapshot_path]


def test_read_snapshot_corrupt_file_degrades_gracefully(snapshot_path):
    snapshot_path.write_text("{not json")
    assert symbol_watch.read_snapshot()["symbols"] == {}


def _stub_dolt_only(monkeypatch, *, avg_volume=1_000_000, iv_rv_ratio=1.2, winrate=0.6, sample=8):
    monkeypatch.setattr(symbol_watch._scanner, "fetch_avg_volume", lambda symbol, config: avg_volume)
    monkeypatch.setattr(
        symbol_watch._scanner,
        "fetch_iv_rv_ratio",
        lambda symbol, config: {"ok": True, "iv_rv_ratio": iv_rv_ratio},
    )
    monkeypatch.setattr(
        symbol_watch._scanner,
        "compute_winrate",
        lambda symbol, config, lookback: {
            "ok": True,
            "winrate": winrate,
            "sample_size": sample,
            "quarters": [
                {"realized_move": 5.0, "pre_close": 100.0},
                {"realized_move": 8.0, "pre_close": 100.0},
            ],
            "realized_move_quarters": [
                {"realized_move": 5.0, "pre_close": 100.0},
                {"realized_move": 8.0, "pre_close": 100.0},
            ],
        },
    )


def test_compute_symbol_entry_happy_path(monkeypatch):
    _stub_dolt_only(monkeypatch)
    monkeypatch.setattr(
        symbol_watch._scanner,
        "fetch_quote_and_expirations",
        lambda symbol: {"ok": True, "price": 100.0, "expirations": ["2026-08-21", "2026-09-18"]},
    )
    monkeypatch.setattr(
        symbol_watch._scanner, "select_front_expiration", lambda *a, **k: ("2026-08-21", None)
    )
    monkeypatch.setattr(symbol_watch._scanner, "select_back_expiration", lambda *a, **k: "2026-09-18")
    monkeypatch.setattr(
        symbol_watch._scanner,
        "fetch_front_back_atm_entries",
        lambda *a, **k: {
            "ok": True,
            "front_call": {"mid": 3.0, "iv": 0.55, "bid": 2.9, "ask": 3.1},
            "front_put": {"mid": 2.8, "iv": 0.55, "bid": 2.7, "ask": 2.9},
            "back_call": {"mid": 4.0, "iv": 0.40},
        },
    )
    monkeypatch.setattr(
        symbol_watch._scanner,
        "compute_expected_move_and_term_structure",
        lambda *a, **k: {"term_structure": -0.05, "expected_move_dollars": 4.93, "expected_move_pct": 0.0493},
    )
    monkeypatch.setattr(
        symbol_watch._scanner,
        "fetch_liquidity_criteria",
        lambda *a, **k: {
            "bid_ask_spread_pct": 0.03,
            "market_cap": 5e9,
            "combined_open_interest": 3000,
            "combined_option_volume": 1200,
            "iv_rank": 45.0,
            "iv_percentile": 60.0,
            "tastytrade_iv_rv_ratio": 1.4,
            "net_combo_spread_pct": 0.02,
        },
    )

    from datetime import date

    entry = symbol_watch._compute_symbol_entry("AAPL", date(2026, 8, 20), "After market close", {})

    assert entry["symbol"] == "AAPL"
    assert entry["earnings_date"] == "2026-08-20"
    assert entry["earnings_timing"] == "After market close"
    assert entry["term_structure"] == -0.05
    assert entry["expected_move_pct"] == 0.0493
    assert entry["market_cap"] == 5e9
    assert entry["iv_rank"] == 45.0
    # tastytrade's ratio wins over Dolt's when both are present (apply_common_signals' rule)
    assert entry["iv_rv_ratio"] == 1.4
    assert entry["iv_rv_source"] == "tastytrade"
    assert entry["avg_volume"] == 1_000_000
    assert entry["winrate"] == 0.6
    assert entry["winrate_sample"] == 8
    assert entry["avg_actual_move_pct"] is not None
    assert entry["implied_vs_avg_actual"] is not None
    assert entry["net_combo_spread_pct"] == 0.02
    assert entry["error"] is None
    assert isinstance(entry["refreshed_at"], float)
    assert entry["price"] == 100.0
    # avg_volume=1_000_000 sits in the near-miss band (near_miss floor 1M, strict bar 1.5M)
    assert entry["tier"] == "near_miss"
    assert entry["tier_reasons"] == ["avg volume 1000000 in near-miss band (<1500000)"]


def test_compute_symbol_entry_chain_failure_keeps_dolt_only_fields(monkeypatch):
    _stub_dolt_only(monkeypatch)
    monkeypatch.setattr(
        symbol_watch._scanner,
        "fetch_quote_and_expirations",
        lambda symbol: {"ok": False, "error": "get_quote failed"},
    )

    from datetime import date

    entry = symbol_watch._compute_symbol_entry("ZZZZ", date(2026, 8, 20), "After market close", {})

    assert entry["error"] == "get_quote failed"
    assert entry["term_structure"] is None
    assert entry["expected_move_pct"] is None
    assert entry["avg_volume"] == 1_000_000
    assert entry["winrate"] == 0.6
    # no tastytrade ratio available (chain fetch never happened) -- falls back to Dolt's
    assert entry["iv_rv_ratio"] == 1.2
    assert entry["iv_rv_source"] == "dolt"
    assert entry["price"] is None
    # no price/term_structure/expected_move/OI -- can't be classified
    assert entry["tier"] is None
    assert entry["tier_reasons"] == ["get_quote failed"]


def test_compute_symbol_entry_no_back_expiration_records_reason(monkeypatch):
    _stub_dolt_only(monkeypatch)
    monkeypatch.setattr(
        symbol_watch._scanner,
        "fetch_quote_and_expirations",
        lambda symbol: {"ok": True, "price": 100.0, "expirations": ["2026-08-21"]},
    )
    monkeypatch.setattr(
        symbol_watch._scanner, "select_front_expiration", lambda *a, **k: ("2026-08-21", None)
    )
    monkeypatch.setattr(symbol_watch._scanner, "select_back_expiration", lambda *a, **k: None)

    from datetime import date

    entry = symbol_watch._compute_symbol_entry("AAPL", date(2026, 8, 20), "After market close", {})
    assert entry["error"] == "no back-month expiration available"


def test_refresh_symbol_watch_window_end_is_nth_trading_day(monkeypatch, snapshot_path):
    from datetime import date

    from cherrypick.core import calendar as _calendar

    captured = {}

    def fake_range(start, end, config):
        captured["start"] = start
        captured["end"] = end
        return []

    monkeypatch.setattr(symbol_watch._scanner, "fetch_dolthub_calendar_range", fake_range)

    symbol_watch.refresh_symbol_watch(days=10, config={"symbol_watch": {"liquid_only": False}})

    today = date.today()
    assert captured["start"] == today
    assert captured["end"] == _calendar.nth_trading_day(today, 10)


def test_refresh_symbol_watch_writes_progress_and_prunes_out_of_scope_symbols(monkeypatch, snapshot_path):
    """OLD isn't in this pass's rows at all -- it must be pruned immediately, not carried forward
    forever (no future pass would ever refresh it)."""
    symbol_watch._write_snapshot({"OLD": {"symbol": "OLD", "earnings_date": "2026-08-01"}}, 1.0, 1.0, 1, 1)

    rows = [
        {"symbol": "AAPL", "date": "2026-08-20", "timing": "After market close"},
        {"symbol": "MSFT", "date": "2026-08-21", "timing": "Before market open"},
    ]
    monkeypatch.setattr(
        symbol_watch._scanner, "fetch_dolthub_calendar_range", lambda start, end, config: rows
    )

    seen_symbols = []

    def fake_compute(symbol, earnings_date, timing, config):
        seen_symbols.append(symbol)
        return {"symbol": symbol, "earnings_date": earnings_date.isoformat(), "earnings_timing": timing}

    monkeypatch.setattr(symbol_watch, "_compute_symbol_entry", fake_compute)

    result = symbol_watch.refresh_symbol_watch(days=14, config={"symbol_watch": {"liquid_only": False}})

    assert result["ok"] is True
    assert result["total"] == 2
    assert result["done"] == 2
    assert seen_symbols == ["AAPL", "MSFT"]

    final = symbol_watch.read_snapshot()
    assert final["pass_completed_at"] is not None
    assert set(final["symbols"]) == {"AAPL", "MSFT"}  # OLD pruned -- it was never in this pass's scope


def test_refresh_symbol_watch_keeps_in_scope_symbol_visible_mid_pass(monkeypatch, snapshot_path):
    """MSFT IS in this pass's rows, just not reached yet (compute is monkeypatched to only ever
    run for the one symbol under test) -- its last-known-good reading must still be visible in the
    snapshot written after symbol 0/2, not disappear until this pass recomputes it."""
    symbol_watch._write_snapshot(
        {"MSFT": {"symbol": "MSFT", "earnings_date": "2026-08-21", "winrate": 0.5}}, 1.0, 1.0, 1, 1
    )

    rows = [
        {"symbol": "AAPL", "date": "2026-08-20", "timing": "After market close"},
        {"symbol": "MSFT", "date": "2026-08-21", "timing": "Before market open"},
    ]
    monkeypatch.setattr(
        symbol_watch._scanner, "fetch_dolthub_calendar_range", lambda start, end, config: rows
    )

    written_snapshots = []
    original_write = symbol_watch._write_snapshot

    def spy_write(symbols, *args, **kwargs):
        written_snapshots.append(dict(symbols))
        return original_write(symbols, *args, **kwargs)

    monkeypatch.setattr(symbol_watch, "_write_snapshot", spy_write)
    monkeypatch.setattr(
        symbol_watch,
        "_compute_symbol_entry",
        lambda symbol, earnings_date, timing, config: {"symbol": symbol},
    )

    symbol_watch.refresh_symbol_watch(days=14, config={"symbol_watch": {"liquid_only": False}})

    # The very first write (progress 0/2, before either symbol is computed) already carries
    # MSFT's prior reading -- it was in scope from the start, never dropped then re-added.
    assert written_snapshots[0] == {"MSFT": {"symbol": "MSFT", "earnings_date": "2026-08-21", "winrate": 0.5}}


def test_refresh_symbol_watch_prefilters_to_watch_universe_by_default(monkeypatch, snapshot_path):
    rows = [
        {"symbol": "AAPL", "date": "2026-08-20", "timing": "After market close"},
        {"symbol": "ILLIQUIDCO", "date": "2026-08-21", "timing": "Before market open"},
    ]
    monkeypatch.setattr(
        symbol_watch._scanner, "fetch_dolthub_calendar_range", lambda start, end, config: rows
    )
    monkeypatch.setattr(symbol_watch._scanner, "fetch_watch_universe", lambda: {"AAPL", "MSFT"})

    seen_symbols = []
    monkeypatch.setattr(
        symbol_watch,
        "_compute_symbol_entry",
        lambda symbol, earnings_date, timing, config: seen_symbols.append(symbol) or {"symbol": symbol},
    )

    # A non-empty but liquid_only-default config -- {} is falsy and would fall through to a real
    # _load_config() disk read (works locally, fails in CI with no config file present).
    result = symbol_watch.refresh_symbol_watch(days=10, config={"symbol_watch": {}})

    assert result["total"] == 1
    assert seen_symbols == ["AAPL"]  # ILLIQUIDCO never reached the expensive per-symbol fetch


def test_refresh_symbol_watch_skips_universe_filter_when_disabled(monkeypatch, snapshot_path):
    rows = [{"symbol": "ILLIQUIDCO", "date": "2026-08-21", "timing": "Before market open"}]
    monkeypatch.setattr(
        symbol_watch._scanner, "fetch_dolthub_calendar_range", lambda start, end, config: rows
    )
    called = []
    monkeypatch.setattr(symbol_watch._scanner, "fetch_watch_universe", lambda: called.append(1) or set())
    monkeypatch.setattr(
        symbol_watch,
        "_compute_symbol_entry",
        lambda symbol, earnings_date, timing, config: {"symbol": symbol},
    )

    result = symbol_watch.refresh_symbol_watch(days=10, config={"symbol_watch": {"liquid_only": False}})

    assert result["total"] == 1
    assert called == []  # liquid_only off -- fetch_watch_universe must never even be called


def test_refresh_symbol_watch_degrades_to_unfiltered_on_universe_fetch_failure(monkeypatch, snapshot_path):
    """A failed/empty universe fetch must scan everyone, not no one -- 'couldn't determine' is
    not the same claim as 'nothing qualifies'."""
    rows = [
        {"symbol": "AAPL", "date": "2026-08-20", "timing": "After market close"},
        {"symbol": "MSFT", "date": "2026-08-21", "timing": "Before market open"},
    ]
    monkeypatch.setattr(
        symbol_watch._scanner, "fetch_dolthub_calendar_range", lambda start, end, config: rows
    )
    monkeypatch.setattr(symbol_watch._scanner, "fetch_watch_universe", lambda: None)
    monkeypatch.setattr(
        symbol_watch,
        "_compute_symbol_entry",
        lambda symbol, earnings_date, timing, config: {"symbol": symbol},
    )

    result = symbol_watch.refresh_symbol_watch(days=10, config={"symbol_watch": {}})

    assert result["total"] == 2


def test_cmd_refresh_dispatches_with_days(monkeypatch):
    captured = {}

    def fake_refresh(days, config=None):
        captured["days"] = days
        return {"ok": True}

    monkeypatch.setattr(symbol_watch, "refresh_symbol_watch", fake_refresh)

    class Args:
        days = 21

    result = symbol_watch.cmd_refresh(Args())
    assert result == {"ok": True}
    assert captured["days"] == 21
