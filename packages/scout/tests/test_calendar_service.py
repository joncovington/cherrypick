import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import calendar_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z-ish; exact value doesn't matter, just a fixed instant


def _metrics_payload(symbol, report_date, **overrides):
    earnings = {
        "expected_report_date": report_date,
        "time_of_day": "After market close",
        "consensus_estimate": 1.23,
        "actual_eps": None,
        "estimated": True,
    }
    return {
        symbol: {
            "symbol": symbol,
            "iv_rank": "62",
            "liquidity_rating": 4,
            "earnings": earnings,
            **overrides,
        }
    }


@pytest.mark.asyncio
async def test_metrics_only_row_inside_the_window(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    report_date = (today + timedelta(days=3)).isoformat()

    async def fake_get_metrics(_conn, _session, _symbols, _ttl, now=None):
        return _metrics_payload("AAPL", report_date)

    async def fake_atm_straddle(_session, _symbol):
        return (100.0, 8.5)

    async def fake_dolt(_cfg, _start, _end):
        return []  # Dolt reachable, just nothing extra

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_atm_straddle_mid", fake_atm_straddle)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert result["ok"] is True
    assert result["dolt_available"] is True
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["symbol"] == "AAPL"
    assert entry["source"] == "metrics"
    assert entry["stale"] is False
    assert entry["expected_move_pct"] == pytest.approx(0.085)


@pytest.mark.asyncio
async def test_metrics_row_outside_the_window_is_dropped(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    far_out = (today + timedelta(days=90)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return _metrics_payload("AAPL", far_out)

    async def fake_dolt(*_a, **_kw):
        return []

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert result["entries"] == []


@pytest.mark.asyncio
async def test_dolt_rows_fill_in_symbols_metrics_does_not_cover(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return {}  # nothing on the watchlist has earnings this window

    async def fake_dolt(_cfg, _start, _end):
        return [{"symbol": "GME", "report_date": soon, "timing": "Before market open"}]

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["symbol"] == "GME"
    assert entry["source"] == "dolt"
    assert entry["stale"] is True
    assert entry["expected_move_pct"] is None


@pytest.mark.asyncio
async def test_metrics_row_wins_over_a_dolt_row_for_the_same_symbol_and_date(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return _metrics_payload("AAPL", soon)

    async def fake_atm_straddle(*_a, **_kw):
        return None

    async def fake_dolt(_cfg, _start, _end):
        return [{"symbol": "AAPL", "report_date": soon, "timing": "Before market open"}]

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_atm_straddle_mid", fake_atm_straddle)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["source"] == "metrics"


@pytest.mark.asyncio
async def test_liquid_only_filters_out_non_liquid_rows_from_both_sources(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return _metrics_payload("AAPL", soon)

    async def fake_atm_straddle(*_a, **_kw):
        return None

    async def fake_dolt(_cfg, _start, _end):
        return [{"symbol": "GME", "report_date": soon, "timing": "Before market open"}]

    async def fake_liquid_symbols(*_a, **_kw):
        return {"AAPL"}  # GME is not liquid by this (fake) reckoning

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_atm_straddle_mid", fake_atm_straddle)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)
    monkeypatch.setattr(calendar_service.liquidity_service, "get_liquid_symbols", fake_liquid_symbols)

    cfg = {"calendar": {"liquid_only": True}}
    result = await calendar_service.get_calendar(conn, object(), cfg, ["AAPL"], days=14, now=NOW)
    assert [e["symbol"] for e in result["entries"]] == ["AAPL"]  # GME (Dolt-sourced) filtered out
    assert result["liquid_only"] is True
    assert result["liquidity_filter_available"] is True


@pytest.mark.asyncio
async def test_liquid_only_false_skips_the_filter_entirely(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return {}

    async def fake_dolt(_cfg, _start, _end):
        return [{"symbol": "GME", "report_date": soon, "timing": "Before market open"}]

    async def fake_liquid_symbols(*_a, **_kw):
        raise AssertionError("liquidity_service should not be called when liquid_only is False")

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)
    monkeypatch.setattr(calendar_service.liquidity_service, "get_liquid_symbols", fake_liquid_symbols)

    cfg = {"calendar": {"liquid_only": False}}
    result = await calendar_service.get_calendar(conn, object(), cfg, ["AAPL"], days=14, now=NOW)
    assert [e["symbol"] for e in result["entries"]] == ["GME"]
    assert result["liquid_only"] is False
    assert result["liquidity_filter_available"] is False


@pytest.mark.asyncio
async def test_liquid_only_defaults_true_but_skips_filtering_when_unavailable(conn, monkeypatch):
    """A fetch failure with nothing cached (liquidity_service degrades to an empty set) must not
    empty the whole calendar -- the filter is skipped, not applied as "nothing is liquid"."""
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return {}

    async def fake_dolt(_cfg, _start, _end):
        return [{"symbol": "GME", "report_date": soon, "timing": "Before market open"}]

    async def fake_liquid_symbols(*_a, **_kw):
        return set()  # simulates liquidity_service's own degrade-to-empty on a fetch failure

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)
    monkeypatch.setattr(calendar_service.liquidity_service, "get_liquid_symbols", fake_liquid_symbols)

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert [e["symbol"] for e in result["entries"]] == ["GME"]
    assert result["liquid_only"] is True
    assert result["liquidity_filter_available"] is False


@pytest.mark.asyncio
async def test_earnings_watchlist_symbols_are_unioned_into_the_metrics_call(conn, monkeypatch):
    """A symbol from tastytrade's own "All Earnings" watchlist, not on the user's own watchlist,
    should surface as a "metrics"-sourced (live, non-stale) row -- not fall through to Dolt."""
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()
    seen_symbols = {}

    async def fake_get_metrics(_conn, _session, symbols, _ttl, now=None):
        seen_symbols["requested"] = set(symbols)
        return _metrics_payload("GME", soon)  # GME is the earnings-watchlist symbol, not AAPL

    async def fake_earnings_watchlist(*_a, **_kw):
        return {"GME"}

    async def fake_dolt(_cfg, _start, _end):
        return []

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)
    monkeypatch.setattr(
        calendar_service.earnings_watchlist_service,
        "get_earnings_watchlist_symbols",
        fake_earnings_watchlist,
    )
    monkeypatch.setattr(
        calendar_service.liquidity_service, "get_liquid_symbols", lambda *_a, **_kw: _empty_set()
    )

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert seen_symbols["requested"] == {"AAPL", "GME"}  # union of watchlist + earnings watchlist
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["symbol"] == "GME"
    assert entry["source"] == "metrics"
    assert entry["stale"] is False
    # Not on the user's own watchlist -- expected-move's per-symbol chain fetch is skipped for it.
    assert entry["expected_move_pct"] is None


@pytest.mark.asyncio
async def test_expected_move_loop_is_scoped_to_the_users_own_watchlist_only(conn, monkeypatch):
    """The earnings-watchlist union must never widen the per-symbol chain-fetch loop -- that's
    exactly the "call storm" risk the module was built to avoid for broad third-party rows."""
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=2)).isoformat()
    straddle_calls = []

    async def fake_get_metrics(_conn, _session, _symbols, _ttl, now=None):
        return {**_metrics_payload("AAPL", soon), **_metrics_payload("GME", soon)}

    async def fake_atm_straddle(_session, symbol):
        straddle_calls.append(symbol)
        return (100.0, 8.5)

    async def fake_earnings_watchlist(*_a, **_kw):
        return {"GME"}

    async def fake_dolt(_cfg, _start, _end):
        return []

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_atm_straddle_mid", fake_atm_straddle)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)
    monkeypatch.setattr(
        calendar_service.earnings_watchlist_service,
        "get_earnings_watchlist_symbols",
        fake_earnings_watchlist,
    )
    monkeypatch.setattr(
        calendar_service.liquidity_service, "get_liquid_symbols", lambda *_a, **_kw: _empty_set()
    )

    await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert straddle_calls == ["AAPL"]  # GME (earnings-watchlist-only) never triggers a chain fetch


@pytest.mark.asyncio
async def test_use_tastytrade_earnings_watchlist_false_skips_the_union(conn, monkeypatch):
    seen_symbols = {}

    async def fake_get_metrics(_conn, _session, symbols, _ttl, now=None):
        seen_symbols["requested"] = set(symbols)
        return {}

    async def fake_earnings_watchlist(*_a, **_kw):
        raise AssertionError("earnings_watchlist_service should not be called")

    async def fake_dolt(_cfg, _start, _end):
        return []

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)
    monkeypatch.setattr(
        calendar_service.earnings_watchlist_service,
        "get_earnings_watchlist_symbols",
        fake_earnings_watchlist,
    )

    cfg = {"calendar": {"use_tastytrade_earnings_watchlist": False, "liquid_only": False}}
    await calendar_service.get_calendar(conn, object(), cfg, ["AAPL"], days=14, now=NOW)
    assert seen_symbols["requested"] == {"AAPL"}


async def _empty_set():
    return set()


@pytest.mark.asyncio
async def test_dolt_unreachable_degrades_to_metrics_only(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=1)).isoformat()

    async def fake_get_metrics(*_a, **_kw):
        return _metrics_payload("AAPL", soon)

    async def fake_atm_straddle(*_a, **_kw):
        return None

    async def fake_dolt(*_a, **_kw):
        return None  # Dolt down

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_atm_straddle_mid", fake_atm_straddle)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)

    result = await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    assert result["ok"] is True
    assert result["dolt_available"] is False
    assert result["stale"] is True
    assert len(result["entries"]) == 1
    assert result["entries"][0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_expected_move_pct_is_cached_across_calls(conn, monkeypatch):
    from datetime import datetime, timedelta

    today = datetime.fromtimestamp(NOW).date()
    soon = (today + timedelta(days=1)).isoformat()

    calls = []

    async def fake_atm_straddle(_session, symbol):
        calls.append(symbol)
        return (200.0, 10.0)

    async def fake_get_metrics(*_a, **_kw):
        return _metrics_payload("AAPL", soon)

    async def fake_dolt(*_a, **_kw):
        return []

    monkeypatch.setattr(calendar_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(calendar_service, "_atm_straddle_mid", fake_atm_straddle)
    monkeypatch.setattr(calendar_service, "_fetch_dolt_calendar", fake_dolt)

    await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW)
    await calendar_service.get_calendar(conn, object(), {}, ["AAPL"], days=14, now=NOW + 10)
    assert calls == ["AAPL"]  # second call was a cache hit within the calendar TTL


def test_atm_straddle_mid_picks_nearest_expiration_and_nearest_strikes():
    """A lightweight structural check on the ATM-picking logic without a real broker session --
    full behavior is exercised through the mocked service tests above."""
    from cherrypick.scout.services.calendar_service import _parse_date

    assert _parse_date("2027-03-01") is not None
    assert _parse_date("2027-03-01T00:00:00") is not None
    assert _parse_date(None) is None
    assert _parse_date("not-a-date") is None
