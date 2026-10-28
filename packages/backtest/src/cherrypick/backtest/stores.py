"""Every vendor store under the licensed directory, declared: what it holds, the code that builds it,
and what reads it (2026-10-25).

The data is licensed and must stay purgeable (CLAUDE.md), so the backup skips it and nothing copies it
elsewhere. What keeps a store usable later is that it can be REBUILT: each entry names its fetch code,
committed in this repo, so a store lost or purged with the licence comes back by running that code.

`inventory` reads the vendor directory against this list: every declared store with its tables, rows
and date span (counts only, never a price), a declared store that is missing, and any `.db` file nobody
declared -- the Data tab shows that last kind as a fault, so a new store is declared as it is added.
A test drives the same check off the directory, so an undeclared writer is caught where it lands.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import paths

#: One entry per store file in the vendor directory. `built_by`: the command or committed script that
#: builds it (a script path is checked to exist by a test). `used_by`: who reads it.
STORES: list[dict[str, str]] = [
    {
        "file": "SPX-60s.db",
        "holds": "SPX intraday: 0DTE quote samples, spot, GEX profiles, daily closes, at 60 s",
        "built_by": "python -m cherrypick.backtest fetch --cadence 60 (nightly backtest-fetch)",
        "used_by": "validation; MEIC and flies replays; box_touch_history.py; gex_levels_history.py",
    },
    {
        "file": "SPX-15s.db",
        "holds": "SPX intraday at 15 s, a short span: the cadence comparison beside the 60 s store",
        "built_by": "python -m cherrypick.backtest fetch --cadence 15",
        "used_by": "validation's cadence read",
    },
    {
        "file": "eod_chains.db",
        "holds": "end-of-day option chains per (name, session, expiry), and daily expiry paths",
        "built_by": "python -m cherrypick.backtest fetch-chains --requests FILE; fetch-chain-paths",
        "used_by": "credit-spread items 14-17 (credit_spread_plan, credit_spread_item17); income checks",
    },
    {
        "file": "flow.db",
        "holds": "large options orders per name and session",
        "built_by": "python -m cherrypick.backtest fetch-flow --plan FILE",
        "used_by": "the flow-signal study (docs/flow-signal-plan.md)",
    },
    {
        "file": "addon_weeklies.db",
        "holds": "SPXW weekly put minute greeks over each bwb life, and 10:00 ATM calls",
        "built_by": "scripts/fetch_addon_weeklies.py",
        "used_by": "the bwb add-on standalone study (2026-10-23)",
    },
    {
        "file": "SPY-monthly-1545.db",
        "holds": "SPY standard-monthly chains at 15:45 ET, each trading day 2017 on",
        "built_by": "scripts/fetch_spy_monthly_1545.py",
        "used_by": "the strangle-management test (2026-10-20)",
    },
    {
        "file": "SPX-time-warp.db",
        "holds": "SPXW next-expiry chains 15:45-15:55 ET, and that expiry's next session at 5 min",
        "built_by": "scripts/fetch_spx_time_warp.py",
        "used_by": "item 19, the Time Warp test (docs/edge-hypotheses-plan.md)",
    },
    {
        "file": "SPX-convex-stack.db",
        "holds": "SPXW ~5-DTE chains 15:40-15:50 ET Mon/Thu, then that expiry to expiry at 5 min",
        "built_by": "scripts/fetch_spx_convex_stack.py",
        "used_by": "item 20, the Convex Stack test (docs/edge-hypotheses-plan.md)",
    },
]

DATE_COLUMNS = ("trade_date", "session", "day", "date", "expiration")


def script_of(entry: dict[str, str]) -> str | None:
    """The committed script an entry is built by, or None when it is a package verb."""
    first = entry["built_by"].split()[0]
    return first if first.startswith("scripts/") and first.endswith(".py") else None


def _shape(path: Path) -> dict[str, Any]:
    """Tables with rows and the span of their first date-like column. Counts only."""
    out: dict[str, Any] = {"tables": {}}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            col = next((c for c in DATE_COLUMNS if c in cols), None)
            span = (
                conn.execute(f'SELECT MIN("{col}"), MAX("{col}") FROM "{t}"').fetchone()
                if col and n
                else None
            )
            out["tables"][t] = {"rows": n, "span": list(span) if span and span[0] is not None else None}
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    finally:
        conn.close()
    return out


def _cached_shape(p: Path, cache: dict, on_miss=None) -> dict[str, Any]:
    """`_shape`, kept while the file's size and mtime are unchanged: a full count over the larger
    stores takes seconds, and the Data tab reads this on every refresh. `on_miss` runs after a
    recount, so a caller can persist the cache store by store."""
    st = p.stat()
    key = f"{st.st_size}:{int(st.st_mtime)}"
    hit = cache.get(p.name)
    if hit and hit.get("key") == key:
        return hit["shape"]
    shape = _shape(p)
    cache[p.name] = {"key": key, "shape": shape}
    if on_miss is not None:
        on_miss()
    return shape


def inventory(
    settings: dict, *, vendor_dir: Path | None = None, cache_path: Path | None = None
) -> dict[str, Any]:
    """The vendor directory against `STORES`: declared stores present or missing, and undeclared files.
    Shapes are cached (`cache_path`, default the results dir's `store_inventory.json`; counts only).

    The cache is written after EVERY recount, not once at the end (2026-10-28): the console's bridge
    gives this read 30 s, and when several multi-gigabyte stores had changed since the last read
    (the nightly 60 s append plus two new chain stores), one read could not finish inside it and the
    next read started every count over again -- the Backtest page stayed "unavailable" for as long
    as that held. Now each refresh banks the stores it managed, and the page recovers within a few.
    """
    d = vendor_dir or paths.vendor_dir(settings)
    cache_file = cache_path or (paths.results_dir() / "store_inventory.json")
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    on_disk = {p.name: p for p in sorted(d.glob("*.db"))} if d.exists() else {}
    declared = {e["file"] for e in STORES}

    def save() -> None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({k: v for k, v in cache.items() if k in on_disk}), encoding="utf-8"
            )
        except OSError:
            pass

    rows = []
    for e in STORES:
        p = on_disk.get(e["file"])
        row = {**e, "present": p is not None, "script": script_of(e)}
        if p is not None:
            row["bytes"] = p.stat().st_size
            row.update(_cached_shape(p, cache, save))
        rows.append(row)
    undeclared = [
        {"file": name, "bytes": p.stat().st_size, **_cached_shape(p, cache, save)}
        for name, p in on_disk.items()
        if name not in declared
    ]
    save()
    return {"dir": str(d), "stores": rows, "undeclared": undeclared}
