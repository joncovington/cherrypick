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
import time
from dataclasses import dataclass, field
from pathlib import Path

# Read-only opens go through cherrypick.core.db.connect_ro: it percent-escapes the path, so a
# directory containing '?', '#' or '%' cannot silently change the URI's meaning. The local
# copies interpolated the path raw, where a '#' truncated the URI and opened a DIFFERENT,
# empty database — which a provider reports as "nothing cached" rather than as an error.
from cherrypick.core import clock as _clock
from cherrypick.core.db import connect_ro as _connect_ro
from cherrypick.core.streamcache import read_spot as _core_read_spot


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
    # How old the inputs behind this snapshot are, in seconds (None when nothing was read). A GEX
    # surface built from a dead feed renders exactly like a live one, which is how a stalled
    # streamer stayed invisible here; carrying the age lets the viewer say so. Deliberately
    # ANNOTATED, not filtered: this is a read-only dashboard, and a stale chart labelled stale is
    # more useful to a human than a blank one. The trading path (flies' provider) does refuse.
    input_age_seconds: float | None = None


# SQLite's default host-parameter limit is 999; stay under it with room to spare.
_SPOT_CHUNK = 900


def _normalise_iv(raw_iv: float) -> float:
    """Stream cache stores IV as a raw decimal (0.20); the chart wants percent. Values already > 1
    are assumed to be percent already (defensive, matches MEIC's dashboard)."""
    return raw_iv if raw_iv > 1 else raw_iv * 100


def read_spot(db_path: Path | str, symbol: str, *, max_age_seconds: float | None = None) -> float | None:
    """The underlying's latest spot (``stream_trades.last``) for one symbol, read-only. ``None``
    when the cache is missing, the symbol isn't cached, or the print is older than
    ``max_age_seconds``. Passing ``None`` keeps the last known price whatever its age."""
    return _core_read_spot(db_path, symbol, max_age_seconds=max_age_seconds)


def read_spots(
    db_path: Path | str, symbols: list[str], *, max_age_seconds: float | None = None
) -> dict[str, float]:
    """Latest spot for several symbols at once, read-only. Absent symbols are simply missing.

    A symbol whose print is older than ``max_age_seconds`` is treated as absent, which is the point
    rather than an optimisation. This feeds the spot TRAIL, and the trail is a record of what the
    market did: writing a frozen print as a fresh sample every tick makes a stalled feed and a
    quiet market look identical, which is the one thing the suite's recording rules exist to
    prevent. Skipping leaves a GAP, and a gap is legible as "we were not receiving prices".

    Chunked under SQLite's variable limit, the same way the other providers in the suite do it.
    """
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    db_path = Path(db_path)
    if not syms or not db_path.exists():
        return {}
    now = time.time()
    out: dict[str, float] = {}
    conn = _connect_ro(db_path)
    try:
        for i in range(0, len(syms), _SPOT_CHUNK):
            chunk = syms[i : i + _SPOT_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            for r in conn.execute(
                f"SELECT symbol, last, updated_at FROM stream_trades WHERE symbol IN ({placeholders})",
                chunk,
            ):
                if r["last"] is None:
                    continue
                if max_age_seconds is not None:
                    updated = r["updated_at"]
                    if updated is None or (now - float(updated)) > max_age_seconds:
                        continue
                out[str(r["symbol"]).upper()] = float(r["last"])
    finally:
        conn.close()
    return out


def snapshot_from_stream_cache(
    db_path: Path | str, symbol: str, today: str | None = None
) -> GexSnapshot:
    """Build a ``GexSnapshot`` for ``symbol`` from a MEIC-style stream cache, read-only.

    Returns a snapshot with ``spot``/``expiration`` possibly ``None`` when the symbol (or its chain)
    isn't cached yet — the caller reports that as "not ready" rather than an error.

    ``today`` (ET, ISO) bounds which expirations may be used; it is a parameter so a test can pin it.
    """
    symbol = symbol.strip().upper()
    db_path = Path(db_path)
    if not db_path.exists():
        return GexSnapshot(symbol=symbol, spot=None, expiration=None, source="missing")

    conn = _connect_ro(db_path)
    try:
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None

        # Candidate expirations for this underlying, nearest first. The underlying_symbol filter
        # matters: XSP and SPX share 0DTE dates, so an expiration-only match would blend two chains.
        #
        # **Never an expiration that has already passed**, and this is the load-bearing clause. It
        # used to order by ABS(JULIANDAY(expiration) - JULIANDAY('now')), which ranks yesterday's
        # chain as near as tomorrow's and nearer than the day after. Nothing prunes `stream_greeks`,
        # so an expired chain keeps stale non-zero gammas, satisfies the has-greeks test in the loop
        # below, and wins — producing a GEX reading frozen at one value for the whole session.
        #
        # It was not rare. Measured 2026-08-26 over `gex_regime_history`: 3,991 of 10,516 recorded
        # readings (38%) came from a chain that had already expired, nearly all of them a single
        # constant net_gex repeated for hours — 96 readings on 2026-08-19 all reading -16.99bn off
        # the expired 08-18 chain, and so on across 23 sessions. Those readings reach the advisor's
        # fact pack as `market.gex.today_counts` and the overview's gamma flip and walls, and they
        # are what made the advisor report a regime signal that "three sources in this pack describe
        # three different ways".
        #
        # A forward-only ordering, so "nearest" means nearest AHEAD. When nothing valid has greeks
        # the loop below falls back to the nearest future expiration and the caller reports "not
        # ready", which is the correct answer and the one a stale chain was silently replacing.
        today = today or _clock.today_iso()
        exps = [
            r["expiration"]
            for r in conn.execute(
                "SELECT expiration FROM stream_chain WHERE underlying_symbol = ? "
                "AND expiration >= ? "
                "GROUP BY expiration ORDER BY JULIANDAY(expiration) - JULIANDAY(?)",
                (symbol, today, today),
            )
        ]
        if not exps:
            return GexSnapshot(symbol=symbol, spot=spot, expiration=None)

        # Pick the nearest expiration that actually has LIVE greeks. Nearest-by-date alone can land on a
        # future expiration that has chain metadata but no greeks yet — the streamer only subscribes
        # Greeks/Summary/Trade for its active 0DTE ATM window, so an all-strikes metadata chain for a
        # later date reads as all-zero GEX. Fall back to plain nearest (a "not ready" zero profile) when
        # no cached expiration has greeks.
        expiration = exps[0]
        entries: list[dict] = []
        chain_syms: list[str] = []
        for cand in exps:
            cand_entries: list[dict] = []
            cand_syms: list[str] = []
            for row in conn.execute(
                "SELECT data_json FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (cand, symbol),
            ):
                try:
                    opt = json.loads(row["data_json"])
                except Exception:
                    continue
                sym = opt.get("streamer_symbol")
                if not sym:
                    continue
                cand_syms.append(sym)
                cand_entries.append(
                    {
                        "strike_price": opt.get("strike_price"),
                        "streamer_symbol": sym,
                        "option_type": opt.get("option_type"),
                        "shares_per_contract": opt.get("shares_per_contract") or 100,
                    }
                )
            if not cand_syms:
                continue
            if cand == exps[0]:  # remember the nearest as the fallback
                expiration, entries, chain_syms = cand, cand_entries, cand_syms
            ph = ", ".join("?" * len(cand_syms))
            has_greeks = conn.execute(
                f"SELECT COUNT(*) FROM stream_greeks WHERE symbol IN ({ph}) "
                "AND gamma IS NOT NULL AND gamma != 0",
                cand_syms,
            ).fetchone()[0]
            if has_greeks:
                expiration, entries, chain_syms = cand, cand_entries, cand_syms
                break

        greeks: dict[str, dict] = {}
        oi: dict[str, int] = {}
        volume: dict[str, int] = {}
        oldest_age: float | None = None
        now_ts = time.time()

        def _note_age(updated) -> None:
            nonlocal oldest_age
            if updated is None:
                return
            age = now_ts - float(updated)
            if oldest_age is None or age > oldest_age:
                oldest_age = age

        if chain_syms:
            # Filter every follow-up read to this chain's own symbols — an unfiltered SELECT * would
            # scan every other tracked symbol's rows on each refresh, for no benefit.
            ph = ", ".join("?" * len(chain_syms))
            for r in conn.execute(f"SELECT * FROM stream_greeks WHERE symbol IN ({ph})", chain_syms):
                _note_age(r["updated_at"])
                greeks[r["symbol"]] = {
                    "gamma": float(r["gamma"] or 0),
                    "iv": _normalise_iv(float(r["iv"] or 0)),
                }
            # Live OI comes from DXLink Summary events (stream_oi), never the static chain metadata.
            for r in conn.execute(
                f"SELECT symbol, open_interest, updated_at FROM stream_oi WHERE symbol IN ({ph})",
                chain_syms,
            ):
                _note_age(r["updated_at"])
                oi[r["symbol"]] = int(r["open_interest"] or 0)
            # Live per-option volume comes from DXLink Trade events (stream_trades.volume).
            for r in conn.execute(
                f"SELECT symbol, volume FROM stream_trades WHERE symbol IN ({ph})", chain_syms
            ):
                volume[r["symbol"]] = int(r["volume"] or 0)

        return GexSnapshot(
            symbol=symbol,
            spot=spot,
            expiration=expiration,
            chain_entries=entries,
            greeks=greeks,
            oi=oi,
            volume=volume,
            input_age_seconds=round(oldest_age, 1) if oldest_age is not None else None,
        )
    finally:
        conn.close()
