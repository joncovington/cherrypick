from datetime import date

import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import screener_service


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


def test_is_monthly_recognizes_the_third_friday():
    assert screener_service._is_monthly(date(2027, 3, 19))  # a real 3rd Friday
    assert not screener_service._is_monthly(date(2027, 3, 12))  # 2nd Friday
    assert not screener_service._is_monthly(date(2027, 3, 20))  # a Saturday


def test_iv_rank_frac_parses_the_sdks_string_field():
    assert screener_service._iv_rank_frac({"iv_rank": "0.547191662"}) == pytest.approx(0.547191662)
    assert screener_service._iv_rank_frac({"iv_rank": None}) is None
    assert screener_service._iv_rank_frac(None) is None
    assert screener_service._iv_rank_frac({"iv_rank": "garbage"}) is None


def test_passes_prefilter_gates_on_iv_rank_and_liquidity():
    cfg = {"screener": {"min_iv_rank": 25, "min_liquidity_rank": 3}}
    assert screener_service._passes_prefilter({"iv_rank": "0.30", "liquidity_rating": 4}, cfg) is True
    assert screener_service._passes_prefilter({"iv_rank": "0.10", "liquidity_rating": 4}, cfg) is False
    assert screener_service._passes_prefilter({"iv_rank": "0.30", "liquidity_rating": 2}, cfg) is False
    assert screener_service._passes_prefilter(None, cfg) is False


# --------------------------------------------------------------------------- chip filters


def test_chip_buckets():
    assert screener_service._iv_bucket(0.30) == "lt50"
    assert screener_service._iv_bucket(0.50) == "gte50"
    assert screener_service._liquidity_bucket(4) == "very"
    assert screener_service._liquidity_bucket(3) == "somewhat"
    assert screener_service._liquidity_bucket(1) == "not"
    assert screener_service._cap_bucket(1e9) == "small"
    assert screener_service._cap_bucket(5e9) == "medium"
    assert screener_service._cap_bucket(5e10) == "large"
    assert screener_service._cap_bucket(3e12) == "mega"


def test_an_explicit_iv_filter_replaces_the_config_gate():
    """Selecting the <50 chip must show a 10%-IVR name the default min_iv_rank=25 gate would veto."""
    cfg = {"screener": {"min_iv_rank": 25, "min_liquidity_rank": 3}}
    info = {"iv_rank": "0.10", "liquidity_rating": 4}
    assert screener_service._passes_prefilter(info, cfg, {"iv": {"lt50"}}) is True
    assert screener_service._passes_prefilter(info, cfg, {"iv": {"gte50"}}) is False


def test_an_explicit_liquidity_filter_replaces_the_config_gate():
    cfg = {"screener": {"min_iv_rank": 25, "min_liquidity_rank": 3}}
    info = {"iv_rank": "0.60", "liquidity_rating": 2}
    assert screener_service._passes_prefilter(info, cfg, {"liquidity": {"not"}}) is True
    assert screener_service._passes_prefilter(info, cfg, {"liquidity": {"very"}}) is False


def test_a_cap_filter_gates_on_market_cap_and_excludes_missing():
    cfg = {"screener": {}}
    large = {"iv_rank": "0.60", "liquidity_rating": 4, "market_cap": 5e10}
    no_cap = {"iv_rank": "0.60", "liquidity_rating": 4}
    assert screener_service._passes_prefilter(large, cfg, {"cap": {"large", "mega"}}) is True
    assert screener_service._passes_prefilter(large, cfg, {"cap": {"small"}}) is False
    # a missing market cap can't prove membership -- excluded while the filter is active
    assert screener_service._passes_prefilter(no_cap, cfg, {"cap": {"large"}}) is False
    # ...but with no cap filter, a missing market cap is not a gate at all
    assert screener_service._passes_prefilter(no_cap, cfg, {}) is True


def test_unfiltered_dimensions_keep_their_config_gates():
    """A cap-only chip selection must not disable the IV/liquidity defaults."""
    cfg = {"screener": {"min_iv_rank": 25, "min_liquidity_rank": 3}}
    low_iv = {"iv_rank": "0.10", "liquidity_rating": 4, "market_cap": 5e10}
    assert screener_service._passes_prefilter(low_iv, cfg, {"cap": {"large"}}) is False


def test_pick_expiration_prefers_a_monthly_within_the_window():
    # From 2027-02-05: 2027-03-12 is 35 days out (a weekly Friday), 2027-03-19 is 42 days out (the
    # 3rd Friday, i.e. the standard monthly) -- both land in [30, 45], so the monthly must win.
    today = date(2027, 2, 5)
    expirations = {
        "2027-03-12": [{"strike": 100, "option_type": "C"}],
        "2027-03-19": [{"strike": 100, "option_type": "C"}],
    }
    picked = screener_service._pick_expiration(expirations, today, dte_min=30, dte_max=45)
    assert picked is not None
    exp, _options, dte = picked
    assert exp == date(2027, 3, 19)
    assert dte == (date(2027, 3, 19) - today).days


def test_pick_expiration_falls_back_to_nearest_when_no_monthly_in_window():
    today = date(2027, 2, 1)
    expirations = {
        "2027-03-05": [{"strike": 100, "option_type": "C"}],
        "2027-03-12": [{"strike": 100, "option_type": "C"}],
    }
    picked = screener_service._pick_expiration(expirations, today, dte_min=30, dte_max=45)
    assert picked is not None
    exp, _options, _dte = picked
    assert exp == date(2027, 3, 12)  # closer to the window midpoint (37-38 DTE) than 03-05


def test_pick_expiration_returns_none_when_nothing_in_window():
    today = date(2027, 2, 1)
    expirations = {"2027-02-05": [{"strike": 100, "option_type": "C"}]}
    assert screener_service._pick_expiration(expirations, today, dte_min=30, dte_max=45) is None


class _FakeSession:
    pass


def _opt(strike, option_type, mid, symbol=None):
    return {
        "symbol": symbol or f"TEST{option_type}{strike}",
        "strike": strike,
        "option_type": option_type,
        "quote": {"bid": mid - 0.05, "ask": mid + 0.05, "mid": mid, "mark": mid},
    }


@pytest.mark.asyncio
async def test_run_screener_end_to_end_with_a_single_survivor(conn, monkeypatch):
    # 2027-03-19 is a real 3rd Friday, 42 days out from 2027-02-05 -- inside the default [30, 45] window.
    today = date(2027, 2, 5)
    monthly = date(2027, 3, 19)

    async def fake_get_metrics(_conn, _session, _symbols, _ttl, now=None):
        return {"AAPL": {"iv_rank": "0.50", "liquidity_rating": 4, "iv_30d": 0.3}}

    async def fake_get_rate(_conn, _session, now=None):
        return 0.05

    async def fake_get_candles(_conn, _session, _cfg, symbol, now=None):
        return {
            "ok": True,
            "symbol": symbol,
            "bars": [{"t": 0, "o": 100, "h": 101, "l": 99, "c": 100.0, "v": 1}],
        }

    chain_options = [
        _opt(85, "P", 0.50),
        _opt(90, "P", 1.00),
        _opt(95, "P", 2.00),
        _opt(105, "C", 2.00),
        _opt(110, "C", 1.00),
    ]
    quotes_by_symbol = {o["symbol"]: o["quote"] for o in chain_options}

    async def fake_get_expirations(_conn, _session, _cfg, symbol):
        return {
            "ok": True,
            "symbol": symbol,
            "as_of": 0,
            "stale": False,
            "expirations": {monthly.isoformat(): chain_options},
        }

    async def fake_get_quotes(_conn, _session, symbols):
        # Real quotes vary per strike -- a flat quote for every symbol would silently zero out the
        # spread's credit and make this test pass or fail for the wrong reason.
        return {sym: quotes_by_symbol[sym] for sym in symbols if sym in quotes_by_symbol}

    monkeypatch.setattr(screener_service.metrics_service, "get_metrics", fake_get_metrics)
    monkeypatch.setattr(screener_service.metrics_service, "get_risk_free_rate", fake_get_rate)
    monkeypatch.setattr(screener_service.candle_service, "get_candles", fake_get_candles)
    monkeypatch.setattr(screener_service.chain_service, "get_expirations", fake_get_expirations)
    monkeypatch.setattr(screener_service.chain_service, "get_quotes", fake_get_quotes)

    import datetime as _dt

    # `run_screener` derives `today` via `datetime.fromtimestamp(now).date()`; round-tripping through
    # the local timezone this way (rather than faking the clock) keeps the test independent of it.
    now = _dt.datetime.combine(today, _dt.time(hour=12)).timestamp()

    result = await screener_service.run_screener(
        conn, _FakeSession(), {"screener": {}, "refresh": {}}, ["aapl"], "put_credit_spread", now=now
    )
    assert result["ok"] is True
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["symbol"] == "AAPL"
    assert candidate["credit"] > 0
    assert candidate["pop"] is not None
    assert 0.0 <= candidate["pop"] <= 1.0
    assert candidate["composite_score"] > 0


@pytest.mark.asyncio
async def test_run_screener_rejects_an_unknown_strategy(conn):
    result = await screener_service.run_screener(conn, _FakeSession(), {}, [], "not_a_real_strategy")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_run_screener_skips_symbols_that_fail_prefilter(conn, monkeypatch):
    async def fake_get_metrics(_conn, _session, _symbols, _ttl, now=None):
        return {"AAPL": {"iv_rank": "0.05", "liquidity_rating": 4}}  # below min_iv_rank

    monkeypatch.setattr(screener_service.metrics_service, "get_metrics", fake_get_metrics)
    result = await screener_service.run_screener(
        conn,
        _FakeSession(),
        {"screener": {"min_iv_rank": 25, "min_liquidity_rank": 3}, "refresh": {}},
        ["aapl"],
        "put_credit_spread",
    )
    assert result["candidates"] == []
