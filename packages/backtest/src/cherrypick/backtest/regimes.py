"""The recorder's regime rows (`gex_regime_history`), derived from a stored session's profile. Pure
over the store: no vendor call, so a session fetched before this existed is backfilled in place.

The recorder writes one row per ~5 minutes with net GEX, the gamma flip and the net walls, computed
by `core.gex.compute_gex_profile`, `net_walls` and `nearest_zero_gamma` over its chain. flies'
`wall-clear` arm reads those walls (`core.regime.gex_at`), so a vendor session without them leaves
that gate unmeasured. The same three core functions run here over each stored profile row, at the
spot standing at the profile's instant. Two differences, both declared: the chain is the profile's
strikes (the fetched window, not the recorder's whole subscription), and there is no volume, so the
volume and flow columns stay NULL.
"""

from __future__ import annotations

import bisect

from cherrypick.core.gex import compute_gex_profile, nearest_zero_gamma, net_walls


def regime_row(rows: list[dict], spot: float) -> dict | None:
    """One regime reading from one profile row set, or None when the profile carries nothing."""
    entries, greeks, oi = [], {}, {}
    for r in rows:
        strike = float(r["strike"])
        for right, side in (("C", "call"), ("P", "put")):
            sym = f"{right}{strike}"
            entries.append({"strike_price": strike, "streamer_symbol": sym, "option_type": right})
            gamma = r.get(f"{side}_gamma")
            if gamma is not None:
                greeks[sym] = {"gamma": float(gamma), "iv": float(r.get(f"{side}_iv") or 0.0)}
            if r.get(f"{side}_oi") is not None:
                oi[sym] = float(r[f"{side}_oi"])
    profile = compute_gex_profile(entries, greeks, oi, {}, spot)
    if not profile.get("ok"):
        return None
    series = profile["series"]
    call_wall, put_wall = net_walls(series, "net_gex")
    return {
        "spot": spot,
        "net_gex": profile["totals"].get("net_gex"),
        "zero_gamma": nearest_zero_gamma(series, spot, "net_gex"),
        "call_wall": call_wall,
        "put_wall": put_wall,
    }


def derive(conn, symbol: str, day: str) -> int:
    """(Re)write `day`'s regime rows from its stored profile and spot trail. Returns rows written."""
    sym = symbol.upper()
    profiles: dict[float, list[dict]] = {}
    for r in conn.execute(
        "SELECT ts, strike, call_oi, put_oi, call_gamma, put_gamma, call_iv, put_iv FROM gex_profile_history "
        "WHERE symbol = ? AND trade_date = ? ORDER BY ts",
        (sym, day),
    ):
        profiles.setdefault(float(r["ts"]), []).append(dict(r))
    trail = [
        (float(r["ts"]), float(r["spot"]))
        for r in conn.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
            (sym, day),
        )
    ]
    trail_ts = [t for t, _ in trail]
    conn.execute("DELETE FROM gex_regime_history WHERE symbol = ? AND trade_date = ?", (sym, day))
    written = 0
    for ts, rows in sorted(profiles.items()):
        i = bisect.bisect_right(trail_ts, ts) - 1
        if i < 0:
            continue
        reading = regime_row(rows, trail[i][1])
        if reading is None:
            continue
        conn.execute(
            "INSERT INTO gex_regime_history (symbol, trade_date, ts, spot, net_gex, net_gex_vol, zero_gamma, "
            "call_wall, put_wall, expiration) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sym, day, ts, reading["spot"], reading["net_gex"], None, reading["zero_gamma"],
             reading["call_wall"], reading["put_wall"], day),
        )  # fmt: skip
        written += 1
    return written
