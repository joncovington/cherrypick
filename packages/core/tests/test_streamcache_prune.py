"""Pruning the SHARED stream cache.

The cache is written by one producer and read by every module, so it accumulates dead weight no
single module owns: expirations that have passed, and underlyings the suite has retired. Neither
costs meaningful disk. What they cost is a reader picking one up believing it is current -- flies
did exactly that on 2026-08-20, matched a six-day-old copy of the next day's chain, and lost the
session to `no_fresh_quotes` refusals.
"""

from __future__ import annotations

import time

import pytest

from cherrypick.core import streamcache

TODAY = "2026-09-02"
FRESH = time.time()
OLD = time.time() - 30 * 86400


@pytest.fixture
def conn(tmp_path):
    c = streamcache.connect(tmp_path / "cache.db")
    rows = [
        # symbol, expiration, underlying, updated_at
        (".SPX260904C7900", "2026-09-04", "SPX", FRESH),   # declared, future  -> keep
        (".SPX260818C7700", "2026-08-18", "SPX", FRESH),   # declared, LONG expired -> drop
        (".SPX260901C7700", "2026-09-01", "SPX", FRESH),   # declared, just expired -> keep (tail)
        (".NDX260731C24000", "2026-07-31", "NDX", OLD),    # retired + expired -> drop
        (".TQQQ261218C90", "2026-12-18", "TQQQ", OLD),     # RETIRED but far-future expiry -> drop
        (".VXX261016C25", "2026-10-16", "VXX", FRESH),     # declared, future -> keep
    ]
    for sym, exp, und, ts in rows:
        c.execute(
            "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json,"
            " updated_at) VALUES (?,?,?,?,?)",
            (sym, exp, und, "{}", ts),
        )
    # Option rows for two of the chains, plus CASH symbols that live in these tables and are in no
    # chain at all -- the vol complex the cascade must never touch.
    for sym in (".SPX260818C7700", ".NDX260731C24000", ".SPX260904C7900"):
        c.execute("INSERT INTO stream_quotes (symbol, bid, ask, updated_at) VALUES (?,1,2,?)", (sym, FRESH))
        c.execute("INSERT INTO stream_greeks (symbol, gamma, updated_at) VALUES (?,0.01,?)", (sym, FRESH))
    for cash in ("VIX", "VIX3M", "SPX", "XLK", "TLT"):
        c.execute("INSERT INTO stream_quotes (symbol, bid, ask, updated_at) VALUES (?,1,2,?)", (cash, FRESH))
    c.commit()
    yield c
    c.close()


DECLARED = ["SPX", "VXX", "XSP"]


def prune(conn, **kw):
    return streamcache.prune_cache(conn, declared_underlyings=DECLARED, today=TODAY, **kw)


def chains(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT streamer_symbol FROM stream_chain")}


def quotes(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT symbol FROM stream_quotes")}


def test_a_dry_run_reports_without_deleting(conn):
    """Dry by default, like `settle-expired` and `migrate-home`: a human reads what would go first."""
    before = chains(conn)
    report = prune(conn)
    assert report["applied"] is False
    assert report["chain_rows"] == 3  # the two expired-past-the-tail, plus the retired TQQQ
    assert chains(conn) == before, "a dry run wrote something"


def test_it_drops_passed_expirations_but_keeps_a_short_tail(conn):
    prune(conn, apply=True)
    surviving = chains(conn)
    assert ".SPX260818C7700" not in surviving, "an expiration two weeks past is dead weight"
    assert ".SPX260901C7700" in surviving, "yesterday's expiry is inside the tail a late audit reads"
    assert ".SPX260904C7900" in surviving


def test_it_drops_an_undeclared_underlying_however_future_its_expiry(conn):
    """The retired-symbol half. TQQQ's expiration is months out, so the expiry rule alone would
    keep it forever -- what retires it is that no module declares it any more."""
    prune(conn, apply=True)
    assert ".TQQQ261218C90" not in chains(conn)


def test_a_declared_symbol_is_never_pruned_however_stale_its_rows(conn):
    """The clause that keeps this safe. A module that stops streaming for a week -- a holiday, an
    outage -- must not have its chain deleted out from under it."""
    conn.execute("UPDATE stream_chain SET updated_at = ? WHERE underlying_symbol = 'VXX'", (OLD,))
    conn.commit()
    prune(conn, apply=True)
    assert ".VXX261016C25" in chains(conn), "a declared symbol was pruned for being quiet"


def test_a_recently_dropped_symbol_keeps_its_rows_until_the_window_passes(conn):
    """Both clauses are required: undeclared AND untouched. A module that re-declares a symbol it
    dropped this morning must find its chain intact."""
    conn.execute("UPDATE stream_chain SET updated_at = ? WHERE underlying_symbol = 'TQQQ'", (FRESH,))
    conn.commit()
    prune(conn, apply=True)
    assert ".TQQQ261218C90" in chains(conn)


def test_the_cascade_never_touches_a_cash_symbol(conn):
    """The one that would be catastrophic. VIX, the sector ETFs and every underlying live in
    stream_quotes and are in NO chain, so a cascade keyed on 'not referenced by a chain row' and
    not on the option-symbol test would delete the entire vol complex on its first run."""
    prune(conn, apply=True)
    assert {"VIX", "VIX3M", "SPX", "XLK", "TLT"} <= quotes(conn)


def test_the_cascade_removes_the_option_rows_it_orphaned(conn):
    report = prune(conn, apply=True)
    surviving = quotes(conn)
    assert ".SPX260818C7700" not in surviving and ".NDX260731C24000" not in surviving
    assert ".SPX260904C7900" in surviving, "an option whose chain survived kept its quote"
    assert report["orphaned_option_rows"] == 4  # 2 quotes + 2 greeks


def test_it_is_idempotent(conn):
    prune(conn, apply=True)
    again = prune(conn, apply=True)
    assert again["chain_rows"] == 0 and again["orphaned_option_rows"] == 0


def test_the_producer_prunes_with_the_symbols_it_actually_bound():
    """Wired into `_backfill_history`, beside the close purge, at the same once-per-connection
    cadence -- a backlog drain, not a per-event concern.

    It passes `self.symbols`, the set this process actually SUBSCRIBED, rather than re-reading the
    request union: a symbol being streamed can then never be pruned out from under itself, even if
    a module rewrites its request file mid-session. Wrapped, because a failed prune must cost the
    prune and never the stream.
    """
    import inspect

    from cherrypick.core import streamer as _streamer

    src = inspect.getsource(_streamer.ChainStreamer._backfill_history)
    assert "prune_cache(" in src, "the producer no longer prunes the cache it owns"
    assert "declared_underlyings=self.symbols" in src, "prune must use the bound subscription set"
    assert "except Exception" in src, "a failed prune must not take the stream down"
