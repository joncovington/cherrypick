"""The vol term structure at entry: VIX9D, VIX and VIX3M, recorded on every position (2026-10-09).

Why these three. A two-year modelled backtest (2024-10..2026-10, the module's own strike, expiry and
fee rules, priced off a smile fitted to this ledger's legs) found every arm lost money when the
prior session closed with VIX9D at or above VIX -- the next nine days priced richer than the next
thirty, an event inside the window -- and that bounce was the only arm positive with that gate
open, in both years. That is a backtest that picked its own best gate out of six, so it is a
hypothesis to measure forward, not a rule: recording the reading on every position lets
`bwb regime-split` cut each arm's own results by it with no new arm and no measurement break. VIX3M
rides along for the suite's other term-structure read (VIX/VIX3M, curve's and contango's signal).

RECORDING only: nothing in the entry decision reads it, so it changes nothing bwb does or measures.
Raw prints only -- the ratios are derived at read time (`gate_state`), the suite's store-the-measure
rule (`cherrypick.core.regime`). A print older than `max_age_seconds` is stored as no number with
its reason, never as the last value: an index print freezes once the feed goes quiet, and a frozen
VIX9D would read as a calm market (curve's rule 6).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# (column prefix, cache symbol). Index symbols print as trades, never as quotes (streamcache).
SYMBOLS = (("entry_vix9d", "VIX9D"), ("entry_vix", "VIX"), ("entry_vix3m", "VIX3M"))

# Columns this writes on the position row (declared in db._ADDED_COLUMNS).
COLUMNS = (
    "entry_vix9d",
    "entry_vix9d_age",
    "entry_vix",
    "entry_vix_age",
    "entry_vix3m",
    "entry_vix3m_age",
    "entry_regime_reason",
)

# The front-of-curve gate the backtest found: open while VIX9D sits below VIX. Read-side only.
VIX9D_VIX_MAX = 1.0


def measure(cache_path: str, *, max_age_seconds: float = 300, now_ts: float | None = None) -> dict[str, Any]:
    """The `entry_vix*` columns, read from the cache now. Never raises; a print it cannot use is
    stored as no number, and `entry_regime_reason` names every one that failed."""
    from cherrypick.core import db as _db

    now_ts = time.time() if now_ts is None else now_ts
    out: dict[str, Any] = {c: None for c in COLUMNS}
    if not Path(cache_path).exists():
        out["entry_regime_reason"] = "cache: no_cache_file"
        return out
    try:
        # Read-only: the streamer is the cache's single writer, and a reader must never create it.
        conn = _db.connect_ro(cache_path)
        try:
            rows = {
                r[0]: (r[1], r[2])
                for r in conn.execute(
                    "SELECT symbol, last, updated_at FROM stream_trades WHERE symbol IN (?, ?, ?)",
                    tuple(sym for _, sym in SYMBOLS),
                )
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a reading not taken is recorded as such
        out["entry_regime_reason"] = f"cache: {type(exc).__name__}: {exc}"[:200]
        return out
    problems = []
    for col, sym in SYMBOLS:
        last, updated = rows.get(sym, (None, None))
        if last is None or updated is None or float(last) <= 0:
            problems.append(f"{sym}:missing")
            continue
        age = round(now_ts - float(updated), 1)
        out[f"{col}_age"] = age
        if age > max_age_seconds:
            problems.append(f"{sym}:stale")
            continue
        out[col] = float(last)
    out["entry_regime_reason"] = ",".join(problems) or None
    return out


def gate_state(row: dict, *, vix9d_vix_max: float = VIX9D_VIX_MAX) -> str:
    """`open` / `shut` / `unmeasured` for one position row: VIX9D/VIX below `vix9d_vix_max` is open.
    A row recorded before 2026-10-09, or with either print missing or stale, is `unmeasured` --
    never assigned to a side."""
    v9, vix = row.get("entry_vix9d"), row.get("entry_vix")
    if v9 is None or vix is None or vix <= 0:
        return "unmeasured"
    return "open" if v9 / vix < vix9d_vix_max else "shut"


def split(conn, *, vix9d_vix_max: float = VIX9D_VIX_MAX) -> dict:
    """Each arm's CLOSED positions cut by the gate: count, net, net per trade, add-on fires.
    Read-side only; the gate never decided an entry."""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM bwb_positions WHERE status = 'closed' ORDER BY arm, entry_session"):
        row = dict(r)
        state = gate_state(row, vix9d_vix_max=vix9d_vix_max)
        b = out.setdefault(row["arm"], {}).setdefault(
            state, {"positions": 0, "net_pnl": 0.0, "fired": 0, "sessions": set()}
        )
        b["positions"] += 1
        b["net_pnl"] += (row["gross_pnl"] or 0.0) - (row["fees"] or 0.0)
        b["fired"] += 1 if row.get("addon_fired_at") else 0
        b["sessions"].add(row["entry_session"])
    for arm in out.values():
        for b in arm.values():
            b["net_pnl"] = round(b["net_pnl"], 2)
            b["net_per_trade"] = round(b["net_pnl"] / b["positions"], 2) if b["positions"] else None
            b["first_session"], b["last_session"] = min(b["sessions"]), max(b["sessions"])
            del b["sessions"]
    return {"gate": f"VIX9D/VIX < {vix9d_vix_max:g} at entry", "arms": out}
