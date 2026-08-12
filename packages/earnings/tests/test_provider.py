"""Pricing an open position from the shared stream cache.

Built against the real `cherrypick.core.streamcache` DDL rather than a hand-written mock, so an
upstream schema change fails here instead of silently producing empty snapshots in production.

The properties worth holding:
  - quotes come back keyed by OCC, so a stream snapshot and a REST snapshot are interchangeable,
  - every leg must be usable, because half a structure priced is no answer at all,
  - a stale or crossed quote is refused rather than degraded,
  - greeks and spot are optional, and their absence never costs a position its perfectly good quotes,
  - a leg whose streamer symbol was never captured is its own refusal, not a feed problem.
"""

import json
import sqlite3
import time

import pytest
from cherrypick.core.streamcache import DDL

from cherrypick.earnings import provider


@pytest.fixture()
def cache(tmp_path):
    """A stream cache shaped exactly like the one the producer writes."""
    path = tmp_path / "stream_cache.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    return path


# An iron fly: two shorts at the body, two long wings. OCC symbols are what the strategies index
# quotes by; streamer symbols are what the cache stores them under.
LEGS = [
    {
        "symbol": "AAPL  260821C00190000",
        "streamer_symbol": ".AAPL260821C190",
        "action": "Sell to Open",
        "quantity": 1,
    },
    {
        "symbol": "AAPL  260821P00190000",
        "streamer_symbol": ".AAPL260821P190",
        "action": "Sell to Open",
        "quantity": 1,
    },
    {
        "symbol": "AAPL  260821C00200000",
        "streamer_symbol": ".AAPL260821C200",
        "action": "Buy to Open",
        "quantity": 1,
    },
    {
        "symbol": "AAPL  260821P00180000",
        "streamer_symbol": ".AAPL260821P180",
        "action": "Buy to Open",
        "quantity": 1,
    },
]


def _trade(legs=None, symbol="AAPL"):
    return {"order_id": "T1", "symbol": symbol, "legs_json": json.dumps(LEGS if legs is None else legs)}


def seed(cache_path, *, quotes=None, age=0.0, spot=190.0, spot_age=0.0, greeks=True, greek_age=0.0):
    now = time.time()
    prices = quotes or {leg["streamer_symbol"]: (1.00, 1.10) for leg in LEGS}
    conn = sqlite3.connect(cache_path)
    for symbol, (bid, ask) in prices.items():
        conn.execute(
            "INSERT OR REPLACE INTO stream_quotes (symbol, bid, ask, mid, updated_at) VALUES (?,?,?,?,?)",
            (symbol, bid, ask, (bid + ask) / 2 if bid is not None and ask is not None else None, now - age),
        )
        if greeks:
            conn.execute(
                "INSERT OR REPLACE INTO stream_greeks (symbol, delta, iv, updated_at) VALUES (?,?,?,?)",
                (symbol, 0.42, 0.55, now - greek_age),
            )
    if spot is not None:
        conn.execute(
            "INSERT OR REPLACE INTO stream_trades (symbol, last, updated_at) VALUES (?,?,?)",
            ("AAPL", spot, now - spot_age),
        )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- the happy path
def test_a_fully_quoted_position_prices(cache):
    seed(cache)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["ok"] and snap["source"] == "stream"
    assert set(snap["quotes"]) == {leg["symbol"] for leg in LEGS}
    assert snap["quotes"]["AAPL  260821C00190000"]["ask"] == 1.10
    assert snap["fresh"] == 4 and snap["stale"] == 0


def test_the_snapshot_prices_a_close_through_the_existing_path(cache):
    """Interchangeability, asserted rather than asserted-about: the shared exit-debit function must
    accept this snapshot with no adaptation."""
    from cherrypick.earnings import scanner

    seed(cache)
    snap = provider.snapshot(_trade(), db_path=cache)
    debit = scanner.compute_generic_exit_debit(LEGS, snap["quotes"])

    # Buy back two shorts at ask (1.10 each), sell two longs at bid (1.00 each).
    assert debit == pytest.approx(2 * 1.10 - 2 * 1.00)


def test_greeks_ride_along_for_the_leg_delta_stops(cache):
    seed(cache)
    quote = provider.snapshot(_trade(), db_path=cache)["quotes"]["AAPL  260821C00190000"]
    assert quote["delta"] == 0.42 and quote["iv"] == 0.55


def test_spot_comes_from_the_underlyings_own_stream(cache):
    seed(cache, spot=193.25)
    assert provider.snapshot(_trade(), db_path=cache)["spot"] == 193.25


# --------------------------------------------------------------------------- refusals
def test_a_stale_leg_refuses_the_whole_position(cache):
    """Half a structure priced is not half an answer — compute_generic_exit_debit needs every leg."""
    seed(cache, age=provider.DEFAULT_MAX_QUOTE_AGE_SECONDS + 60)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["ok"] is False and snap["reason"] == "missing_leg_quotes"
    assert snap["stale"] == 4 and snap["fresh"] == 0


def test_one_missing_leg_refuses_the_position_and_names_it(cache):
    quotes = {leg["streamer_symbol"]: (1.00, 1.10) for leg in LEGS[:3]}
    seed(cache, quotes=quotes)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["reason"] == "missing_leg_quotes"
    assert snap["missing"] == ["AAPL  260821P00180000"]  # named in OCC, the caller's vocabulary


def test_a_crossed_quote_is_refused(cache):
    """A bid above the ask is a feed artefact; pricing a close against it invents money."""
    quotes = {leg["streamer_symbol"]: (1.00, 1.10) for leg in LEGS}
    quotes[LEGS[0]["streamer_symbol"]] = (2.00, 1.00)
    seed(cache, quotes=quotes)
    assert provider.snapshot(_trade(), db_path=cache)["reason"] == "missing_leg_quotes"


def test_a_zero_ask_is_refused(cache):
    quotes = {leg["streamer_symbol"]: (1.00, 1.10) for leg in LEGS}
    quotes[LEGS[0]["streamer_symbol"]] = (0.0, 0.0)
    seed(cache, quotes=quotes)
    assert provider.snapshot(_trade(), db_path=cache)["reason"] == "missing_leg_quotes"


def test_a_leg_with_no_streamer_symbol_is_its_own_refusal(cache):
    """A gap in what entry stored, not a feed problem — the two want different fixes, so they get
    different reasons rather than both surfacing as 'missing quotes'."""
    legs = [dict(leg) for leg in LEGS]
    legs[2].pop("streamer_symbol")
    seed(cache)
    snap = provider.snapshot(_trade(legs), db_path=cache)

    assert snap["reason"] == "legs_missing_streamer_symbol"
    assert snap["missing"] == ["AAPL  260821C00200000"]


def test_a_missing_cache_refuses_rather_than_raising(tmp_path):
    snap = provider.snapshot(_trade(), db_path=tmp_path / "not_there.db")
    assert snap["ok"] is False and snap["reason"] == "no_stream_cache"


def test_an_unparseable_legs_json_refuses(cache):
    seed(cache)
    snap = provider.snapshot({"order_id": "T1", "symbol": "AAPL", "legs_json": "{not json"}, db_path=cache)
    assert snap["reason"] == "no_legs_recorded"


# --------------------------------------------------------------------------- optional data
def test_missing_greeks_do_not_cost_a_position_its_quotes(cache):
    """A leg-delta stop skips its check this tick; the profit target does not care. Refusing here
    would throw away good quotes over a measurement nothing was waiting on."""
    seed(cache, greeks=False)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["ok"] is True
    assert snap["quotes"]["AAPL  260821C00190000"]["delta"] is None


def test_stale_greeks_are_dropped_but_the_quotes_stand(cache):
    seed(cache, greek_age=provider.DEFAULT_MAX_QUOTE_AGE_SECONDS + 60)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["ok"] is True
    assert snap["quotes"]["AAPL  260821C00190000"]["iv"] is None


def test_an_unsubscribed_underlying_costs_only_spot(cache):
    """Earnings names turn over weekly, so a position's underlying may not be streamed yet. The
    checks that need spot skip; the ones that do not, run."""
    seed(cache, spot=None)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["ok"] is True and snap["spot"] is None


def test_stale_spot_is_refused_without_refusing_the_position(cache):
    seed(cache, spot_age=provider.DEFAULT_MAX_SPOT_AGE_SECONDS + 60)
    snap = provider.snapshot(_trade(), db_path=cache)

    assert snap["ok"] is True and snap["spot"] is None


# --------------------------------------------------------------------------- the spread gate
def test_the_widest_leg_spread_is_reported(cache):
    """What the execution gate reads: an opening-auction width can exceed the edge being managed,
    and a target computed from that mid is arithmetic rather than a price."""
    quotes = {leg["streamer_symbol"]: (1.00, 1.10) for leg in LEGS}
    quotes[LEGS[0]["streamer_symbol"]] = (0.50, 1.50)  # 100% of mid
    seed(cache, quotes=quotes)

    assert provider.snapshot(_trade(), db_path=cache)["max_spread_pct"] == pytest.approx(1.0)


def test_spread_pct_is_unknown_rather_than_zero_without_a_mid():
    """A zero mid would divide to infinity or silently report a tight spread; neither is true."""
    assert provider.spread_pct({"bid": 0.0, "ask": 0.0, "mid": 0.0}) is None


# --------------------------------------------------------------------------- symbol capture
def test_streamer_symbols_are_attached_from_the_chain():
    """Read from the broker rather than derived: the OCC-to-DXLink transformation looks mechanical
    and is not, and an invented symbol would not fail loudly — it would just never match."""
    from cherrypick.earnings import scanner

    legs = [{"symbol": "AAPL  260821C00190000", "action": "Sell to Open", "quantity": 1}]
    attached = scanner.attach_streamer_symbols(legs, {"AAPL  260821C00190000": ".AAPL260821C190"})

    assert attached[0]["streamer_symbol"] == ".AAPL260821C190"
    assert legs[0].get("streamer_symbol") is None  # the input is not mutated


def test_a_leg_the_chain_did_not_know_is_left_unmarked_rather_than_guessed():
    from cherrypick.earnings import scanner

    legs = [{"symbol": "AAPL  260821C00190000", "action": "Sell to Open", "quantity": 1}]
    assert "streamer_symbol" not in scanner.attach_streamer_symbols(legs, {})[0]
