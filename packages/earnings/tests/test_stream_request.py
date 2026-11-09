"""Declaring this module's stream needs to the producer.

The properties that matter are about the two halves being on different clocks: underlyings bind when
the producer starts (so a change there costs a restart), while legs are re-read every subscription
poll (so a position opening or closing must not look like a reason for one).

The leg-source assertions run the query through the PRODUCER's own reader rather than a local
reimplementation, so a change to what it accepts fails here rather than in production. That package
is not a dependency of this one — this module is a pure consumer and imports nothing from it — so the
whole monorepo has it and a standalone earnings install does not, and those tests skip there.
"""

import argparse
import json
import sqlite3

import pytest
from cherrypick.core import streamrequests as sr

from cherrypick.earnings import db_paper, stream_request

registry = pytest.importorskip(
    "cherrypick.streamer.registry",
    reason="the producer package is not installed; the leg-source contract cannot be checked against it",
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A request directory and a paper book of our own — never the developer's real ones."""
    directory = tmp_path / "stream_requests"
    directory.mkdir()
    monkeypatch.setattr(sr, "requests_dir", lambda: directory)
    monkeypatch.setattr(db_paper, "DB_PATH", tmp_path / "paper_trades.db")
    db_paper.cmd_init_db(argparse.Namespace())
    return directory


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _open_trade(order_id, symbol, streamer_symbols):
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "strategy": "iron_fly",
                    "expiration": "2026-08-21",
                    "entry_credit": 2.0,
                    "legs_json": "[]",
                }
            )
        )
    )
    db_paper.cmd_set_open_legs(
        _ns(data=json.dumps({"order_id": order_id, "streamer_symbols": streamer_symbols}))
    )


def _open_trade_row(order_id, symbol):
    """Just the trades row -- for tests where the entry path itself registers the legs."""
    db_paper.cmd_save_trade(
        _ns(
            data=json.dumps(
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "strategy": "iron_fly",
                    "expiration": "2026-08-21",
                    "entry_credit": 2.0,
                    "legs_json": "[]",
                }
            )
        )
    )


def _legs_the_producer_would_subscribe():
    """Run the request's own leg source exactly the way the producer does: read-only, one SELECT."""
    src = stream_request.leg_sources(db_paper.DB_PATH)[0]
    assert registry._is_single_select(src["query"]), "the producer only runs a single SELECT"
    return registry._legs_from_source(src)


def test_the_underlyings_are_declared_quote_only_never_as_chain_symbols():
    """The underlyings go in `legs`, and `symbols` stays empty. Both get subscribed; only one is
    affordable.

    A `symbols` entry is an UNDERLYING, and the producer auto-subscribes an ATM window of its nearest
    expiration for each -- ~488 subscriptions apiece by the budget estimator's own model, bound once
    at startup so a grown set forces a recycle. This module cannot use that window (its wings and
    back months sit outside it by construction) and only ever wanted the spot quote, which is one
    subscription. At ~488 each against ~4,000 of suite headroom, eight names would have exhausted the
    budget -- and the control-book widening takes a night from about one name to dozens.
    """
    stream_request.write(["AAPL", "msft "], db_path=db_paper.DB_PATH)
    payload = json.loads((sr.requests_dir() / "earnings.json").read_text(encoding="utf-8"))

    assert payload["legs"] == ["AAPL", "MSFT"], "cleaned, uppercased and sorted"
    assert payload["symbols"] == [], (
        "a symbol here costs a ~488-subscription chain window this module cannot reach into, "
        "and binds at producer startup so growth forces a recycle"
    )


def test_the_producer_picks_up_an_open_positions_legs():
    _open_trade("T1", "AAPL", [".AAPL260821C190", ".AAPL260821P190"])
    assert sorted(_legs_the_producer_would_subscribe()) == [".AAPL260821C190", ".AAPL260821P190"]


def test_closing_a_position_drops_its_legs_without_a_restart():
    """Legs are re-read every poll, so the producer stops holding them on the next one — no recycle,
    which is what keeps a morning of closes from restarting the feed its consumers are reading."""
    _open_trade("T1", "AAPL", [".AAPL260821C190"])
    db_paper.cmd_save_close(_ns(data=json.dumps({"order_id": "T1", "exit_debit": 1.0, "pnl": 100.0})))
    db_paper.cmd_clear_open_legs(_ns(order_id="T1"))

    assert _legs_the_producer_would_subscribe() == []


def test_a_close_that_failed_to_clear_its_legs_still_drops_them():
    """The query joins trades and filters on closed_at, so a half-finished close cannot leave a
    symbol subscribed forever — the producer has no way to know it is dead."""
    _open_trade("T1", "AAPL", [".AAPL260821C190"])
    db_paper.cmd_save_close(_ns(data=json.dumps({"order_id": "T1", "exit_debit": 1.0, "pnl": 100.0})))
    # deliberately do NOT clear open_leg_symbols

    assert _legs_the_producer_would_subscribe() == []


def test_legs_of_several_positions_are_unioned():
    _open_trade("T1", "AAPL", [".AAPL260821C190"])
    _open_trade("T2", "MSFT", [".MSFT260821P400", ".MSFT260821P390"])
    assert len(_legs_the_producer_would_subscribe()) == 3


def test_a_request_written_before_the_table_exists_contributes_nothing(tmp_path):
    """The producer may read this before the earnings module has ever opened its book. A missing
    table must contribute nothing rather than break the poll for every other module."""
    empty = tmp_path / "no_tables.db"
    sqlite3.connect(empty).close()
    src = stream_request.leg_sources(empty)[0]
    assert registry._legs_from_source(src) == []


def test_registration_never_raises_into_the_caller(monkeypatch):
    """An unregistered symbol is a data-availability problem the provider already surfaces; refusing
    to run the loop over it would trade that for an outage."""
    monkeypatch.setattr(stream_request, "write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    stream_request.register(["AAPL"])  # must not raise


def test_the_entry_path_registers_the_legs_it_just_opened(tmp_path, monkeypatch):
    """Both ends of this plumbing were correct and nothing connected them.

    `open_leg_symbols` is created, migrated, queried by the producer and tested in isolation -- and
    until 2026-08-25 nothing in the entry path ever wrote it. The table sat empty against 64 trades,
    so this module's legs were never streamed and every mark fell back to the broker. `legs_json`
    carried no `streamer_symbol` either, which is the same missing step: `provider.snapshot` refuses
    a whole position without it.
    """
    from cherrypick.earnings import strat_test_harness as runner

    legs = [
        {"symbol": "OCC-A", "action": "Buy to Open", "quantity": 1},
        {"symbol": "OCC-B", "action": "Sell to Open", "quantity": 1},
    ]
    quotes = {
        "OCC-A": {"bid": 1.0, "ask": 1.0, "iv": 0.5, "delta": 0.3, "streamer_symbol": ".XYZ_A"},
        "OCC-B": {"bid": 2.0, "ask": 2.0, "iv": 0.5, "delta": -0.3, "streamer_symbol": ".XYZ_B"},
    }

    stamped = runner._with_streamer_symbols(legs, quotes)
    assert [x["streamer_symbol"] for x in stamped] == [".XYZ_A", ".XYZ_B"], (
        "the chain's own symbol must ride onto the leg -- never derived from the OCC string"
    )

    runner._register_open_legs("T-NEW", stamped)
    _open_trade_row("T-NEW", "XYZ")
    assert sorted(_legs_the_producer_would_subscribe()) == [".XYZ_A", ".XYZ_B"]


def test_a_leg_with_no_streamer_symbol_never_blocks_the_trade(monkeypatch):
    """A position is already saved by the time its legs register. Failing the trade over a telemetry
    write would trade a data-quality problem for a missing trade -- so an unmappable leg degrades to
    broker pricing, which is exactly what every position did before this was wired up."""
    from cherrypick.earnings import strat_test_harness as runner

    stamped = runner._with_streamer_symbols([{"symbol": "OCC-A"}], {})
    assert stamped[0]["streamer_symbol"] is None
    runner._register_open_legs("T-NONE", stamped)  # must not raise

    def boom(*a, **k):
        raise RuntimeError("db is gone")

    monkeypatch.setattr(runner.db_paper, "cmd_set_open_legs", boom)
    runner._register_open_legs("T-BOOM", [{"symbol": "X", "streamer_symbol": ".X"}])  # must not raise


def test_the_entry_quote_fetch_surfaces_the_streamer_symbol(monkeypatch):
    """Covers the DISCARD SITE, not just the stamping.

    `_leg_quotes_for_symbols` already receives `streamer_symbol` from the chain and kept only
    bid/ask/iv/delta, which is how every leg reached the ledger unmappable. A test that hands the
    stamping helper a ready-made quote dict cannot see that -- verified by putting the discard back
    and watching this file stay green.
    """
    from cherrypick.earnings import scanner
    from cherrypick.earnings import strat_test_harness as runner

    occ = "GE    260717C00360000"
    monkeypatch.setattr(
        scanner,
        "fetch_quotes_by_symbol",
        lambda u, exp, syms, price: {
            occ: {"bid": 1.0, "ask": 1.2, "iv": 0.4, "delta": 0.5, "streamer_symbol": ".GE260717C360"}
        },
    )

    out = runner._leg_quotes_for_symbols("GE", [occ], 100.0)

    assert out[occ]["streamer_symbol"] == ".GE260717C360", (
        "discarding this here is what left 64 trades unmappable to the stream cache"
    )
