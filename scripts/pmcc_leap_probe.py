"""Which ~1-year expirations each pmcc symbol LISTS, and how fine their strike grids are.

A held-long arm picks its long from the broker's own listing (`clock.leap_expiration`: the standard
monthly nearest 360 DTE within [240, 540], any listed date only when no monthly is in the band). A
symbol that lists nothing in that band would refuse `no_leap_listed` every session; a pick whose grid
stops short of the 0.90-0.95-delta strikes would refuse on the long every session; and a LEAP grid
much finer than expected would widen the streamer's deep window past the subscription budget -- so all
three are checked here, against the real chain, before the shield arms are switched on.

First run, 2026-10-04: nearest-to-360 alone picked the end-of-quarter 2027-09-30, whose grids stop
far above the deep band (SLV at 39, QQQ at 525); the third-Friday 2027-09-17 lists deep strikes on
every symbol. That is why the pick prefers standard monthlies.

Read-only: one chain listing per symbol through the shared credential, the universe-builder pattern.
It reaches the network, so it is a script and never on a loop path; it reads no ledger and writes
nothing. The spot for the deep-band count comes from the local stream cache when it holds one.

    python scripts/pmcc_leap_probe.py [--symbols XSP QQQ GLD IWM SLV] [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import date
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.pmcc import clock

DEFAULT_SYMBOLS = ["XSP", "QQQ", "GLD", "IWM", "SLV"]
# How deep the held-long long sits, per symbol (a 0.925-delta ~1-year call, estimated 2026-10-04):
# the band whose strikes the deep window must cover.
DEEP_PCT = {"XSP": 0.30, "QQQ": 0.35, "GLD": 0.35, "IWM": 0.35, "SLV": 0.45}


def _spot(symbol: str) -> float | None:
    path = Path(_home.home()) / "data" / "marketdata" / "stream_cache.db"
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    return float(row[0]) if row and row[0] else None


async def _chains(session, symbols: list[str]) -> dict:
    """Every symbol's chain on ONE event loop: the session's client is bound to the loop it was
    first used on, so a loop per symbol fails from the second symbol on."""
    from tastytrade.instruments import get_option_chain

    out = {}
    for symbol in symbols:
        try:
            out[symbol] = await get_option_chain(session, symbol)
        except Exception as exc:  # noqa: BLE001 -- one symbol's failure is that symbol's answer
            out[symbol] = exc
    return out


def _calls(options) -> list[float]:
    calls = [o for o in options if str(getattr(o, "option_type", "")).upper().endswith(("C", "CALL"))]
    return sorted({float(o.strike_price) for o in (calls or options)})


def probe(chain: dict, symbol: str, today: date) -> dict:
    listed = sorted(e.isoformat() for e in chain)
    band = [e for e in listed if 240 <= (date.fromisoformat(e) - today).days <= 540]
    pick = clock.leap_expiration(listed, today)
    lowest = {e: _calls(chain[date.fromisoformat(e)])[0] for e in band}
    out = {"symbol": symbol, "listed": len(listed), "in_band": band, "lowest_strike": lowest, "pick": pick}
    if pick is None:
        return out
    strikes = _calls(chain[date.fromisoformat(pick["long_expiration"])])
    spacings = sorted({round(b - a, 2) for a, b in zip(strikes, strikes[1:], strict=False)})
    out.update({"strikes": len(strikes), "low": strikes[0], "high": strikes[-1], "spacings": spacings[:6]})
    spot = _spot(symbol)
    if spot:
        pct = DEEP_PCT.get(symbol, 0.40)
        out["spot"] = spot
        out["deep_band"] = [round(spot * (1 - pct), 2), round(spot, 2)]
        out["strikes_in_deep_band"] = sum(1 for k in strikes if spot * (1 - pct) <= k <= spot)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    session = SessionManager(store).get_session()
    today = date.today()
    symbols = [s.upper() for s in args.symbols]
    chains = asyncio.run(_chains(session, symbols))
    results = []
    for symbol in symbols:
        chain = chains.get(symbol)
        if isinstance(chain, Exception):
            results.append({"symbol": symbol, "error": f"{type(chain).__name__}: {chain}"})
            continue
        results.append(probe(chain or {}, symbol, today))
    if args.json:
        print(json.dumps({"ok": True, "date": today.isoformat(), "results": results}, indent=2))
        return 0
    for r in results:
        if "error" in r:
            print(f"{r['symbol']:5} ERROR {r['error']}")
            continue
        pick = r["pick"]
        if pick is None:
            print(f"{r['symbol']:5} NOTHING listed in [240, 540] DTE ({r['listed']} expirations listed)")
            continue
        deep = (
            f" | spot {r['spot']:.2f}: {r['strikes_in_deep_band']} strikes in {r['deep_band']}"
            if "spot" in r
            else " | no cached spot for the deep-band count"
        )
        print(
            f"{r['symbol']:5} picks {pick['long_expiration']} ({pick['long_dte']} DTE) | "
            f"{r['strikes']} strikes {r['low']:g}-{r['high']:g}, spacing {r['spacings']}{deep}"
        )
        print(f"      lowest listed strike per expiry in band: {r['lowest_strike']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
