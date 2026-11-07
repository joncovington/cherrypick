"""How VIX has moved when the S&P moved: the risk matrix's realistic row.

The matrix's fixed rows shift IV by the same points whatever the market does, and say to read the
corner where a selloff meets a vol rise. This states that corner from history instead: for each
benchmark move the matrix models, the windows of `window` trading days (every start day since `since`)
whose SPX return landed near that move, and the MEDIAN ratio of VIX at the window's end to its start.
Near is within `band` of the move's size, never under `min_band`, so a 10% fall is read from falls of
7.5-12.5% and a 1% one from 0.5-1.5%.

The median, not a regression slope: VIX's answer bends (over a week since 2006, a 1-3% fall has lifted
VIX by about 13% and a 10% fall by about 40%, not ten times as much) and is lopsided (rises pull it
down less than falls push it up). Too few windows (`min_windows`) and the move has no figure, never a
guess. A pure function of the snapshot's Cboe rows (`spot_vol_history`, oldest first, [date, spx, vix]),
so a past session re-evaluates to the same row.
"""

from __future__ import annotations

import math
from typing import Any


def trading_days(horizon_days: int, floor: int) -> int:
    """The history window for a matrix horizon in calendar days: its trading days, at least `floor`
    (the time-held-still matrix is read against a week, as fast as a move of its size usually comes)."""
    return max(int(floor), round(horizon_days * 252 / 365))


def curve(
    history: dict[str, Any] | None,
    moves: list[float],
    window: int,
    cfg: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Per move, {move, vix_multiple, windows}: VIX's median end/start ratio over the `window`-day
    stretches whose SPX return was near the move; a move of zero is 1.0. None without history; a move
    with too few windows has `vix_multiple` None."""
    rows = [r for r in (history or {}).get("rows") or [] if str(r[0]) >= str(cfg["since"])]
    if len(rows) <= window:
        return None
    pairs = []
    for i in range(len(rows) - window):
        a, b = rows[i], rows[i + window]
        spx0, spx1, vix0, vix1 = float(a[1]), float(b[1]), float(a[2]), float(b[2])
        if spx0 > 0 and vix0 > 0 and vix1 > 0:
            pairs.append((spx1 / spx0 - 1.0, math.log(vix1 / vix0)))
    out = []
    for m in moves:
        if m == 0:
            out.append({"move": m, "vix_multiple": 1.0, "windows": None})
            continue
        half = max(float(cfg["min_band"]), float(cfg["band"]) * abs(m))
        near = sorted(lv for r, lv in pairs if abs(r - m) <= half)
        if len(near) < int(cfg["min_windows"]):
            out.append({"move": m, "vix_multiple": None, "windows": len(near)})
            continue
        mid = len(near) // 2
        med = near[mid] if len(near) % 2 else (near[mid - 1] + near[mid]) / 2.0
        out.append({"move": m, "vix_multiple": round(math.exp(med), 4), "windows": len(near)})
    return out


def through(history: dict[str, Any] | None) -> str | None:
    rows = (history or {}).get("rows") or []
    return str(rows[-1][0]) if rows else None
