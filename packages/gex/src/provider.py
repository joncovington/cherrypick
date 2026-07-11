"""Snapshot providers — turn a data source into a ``GexSnapshot`` the core aggregator consumes.

The only provider today reads a MEIC-style ``stream_cache.db`` **read-only** (``?mode=ro``): the streamer
writes live option-chain data there (chain metadata, greeks, DXLink Summary open-interest, DXLink Trade
per-option volume), and we only ever read it — never the broker, never the network. This is the exact
cache path MEIC's own dashboard uses, lifted here so the umbrella can surface GEX without importing the
MEIC module's internals. The provider owns the (MEIC-specific) stream-cache schema; the pure GEX math it
feeds lives in ``cherrypick.core.gex`` and is shared with MEIC.

Precondition: MEIC's streamer must be running (or have run this session) so the cache is populated —
open interest and live per-option volume exist *only* because the streamer subscribes Summary + Trade
for the ATM/GEX window.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GexSnapshot:
    """An already-fetched option-chain snapshot, ready for ``compute_gex_profile``.

    ``strike_scale`` maps a scaled underlying (e.g. XSP options quoted at 1/10 of SPX) back into the
    requested symbol's price domain; the file provider never rescales, so it is always 1.0 here.
    """

    symbol: str
    spot: float | None
    expiration: str | None
    chain_entries: list[dict] = field(default_factory=list)
    greeks: dict[str, dict] = field(default_factory=dict)
    oi: dict[str, int] = field(default_factory=dict)
    volume: dict[str, int] = field(default_factory=dict)
    source: str = "stream_cache"
    strike_scale: float = 1.0


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open the stream cache strictly read-only (mirrors report._connect_ro in the umbrella) so a
    viewer can never mutate the streamer's live database."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _normalise_iv(raw_iv: float) -> float:
    """Stream cache stores IV as a raw decimal (0.20); the chart wants percent. Values already > 1
    are assumed to be percent already (defensive, matches MEIC's dashboard)."""
    return raw_iv if raw_iv > 1 else raw_iv * 100


def snapshot_from_stream_cache(db_path: Path | str, symbol: str) -> GexSnapshot:
    """Build a ``GexSnapshot`` for ``symbol`` from a MEIC-style stream cache, read-only.

    Returns a snapshot with ``spot``/``expiration`` possibly ``None`` when the symbol (or its chain)
    isn't cached yet — the caller reports that as "not ready" rather than an error.
    """
    symbol = symbol.strip().upper()
    db_path = Path(db_path)
    if not db_path.exists():
        return GexSnapshot(symbol=symbol, spot=None, expiration=None, source="missing")

    conn = _connect_ro(db_path)
    try:
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None

        # Nearest expiration for this underlying. The underlying_symbol filter matters: XSP and SPX
        # share 0DTE dates, so an expiration-only match would blend two chains.
        exp_row = conn.execute(
            "SELECT expiration FROM stream_chain WHERE underlying_symbol = ? "
            "ORDER BY ABS(JULIANDAY(expiration) - JULIANDAY('now')) LIMIT 1",
            (symbol,),
        ).fetchone()
        if not exp_row:
            return GexSnapshot(symbol=symbol, spot=spot, expiration=None)
        expiration = exp_row["expiration"]

        chain_rows = conn.execute(
            "SELECT data_json FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
            (expiration, symbol),
        ).fetchall()

        entries: list[dict] = []
        chain_syms: list[str] = []
        for row in chain_rows:
            try:
                opt = json.loads(row["data_json"])
            except Exception:
                continue
            sym = opt.get("streamer_symbol")
            if not sym:
                continue
            chain_syms.append(sym)
            entries.append({
                "strike_price":        opt.get("strike_price"),
                "streamer_symbol":     sym,
                "option_type":         opt.get("option_type"),
                "shares_per_contract": opt.get("shares_per_contract") or 100,
            })

        greeks: dict[str, dict] = {}
        oi: dict[str, int] = {}
        volume: dict[str, int] = {}
        if chain_syms:
            # Filter every follow-up read to this chain's own symbols — an unfiltered SELECT * would
            # scan every other tracked symbol's rows on each refresh, for no benefit.
            ph = ", ".join("?" * len(chain_syms))
            for r in conn.execute(f"SELECT * FROM stream_greeks WHERE symbol IN ({ph})", chain_syms):
                greeks[r["symbol"]] = {
                    "gamma": float(r["gamma"] or 0),
                    "iv": _normalise_iv(float(r["iv"] or 0)),
                }
            # Live OI comes from DXLink Summary events (stream_oi), never the static chain metadata.
            for r in conn.execute(f"SELECT symbol, open_interest FROM stream_oi WHERE symbol IN ({ph})", chain_syms):
                oi[r["symbol"]] = int(r["open_interest"] or 0)
            # Live per-option volume comes from DXLink Trade events (stream_trades.volume).
            for r in conn.execute(f"SELECT symbol, volume FROM stream_trades WHERE symbol IN ({ph})", chain_syms):
                volume[r["symbol"]] = int(r["volume"] or 0)

        return GexSnapshot(
            symbol=symbol, spot=spot, expiration=expiration,
            chain_entries=entries, greeks=greeks, oi=oi, volume=volume,
        )
    finally:
        conn.close()
