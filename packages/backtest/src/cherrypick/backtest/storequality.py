"""A quality report per chain store, so a study reads what its data holds before it runs (#1083).

Each study used to write its own coverage step and found the same gaps again: item 54's missing
chains, item 52's refused name-days, NDX's greeks absent before 2026-05. This reads each chain store
once and reports, per symbol and year:

- `sessions`, `chains` (session and expiry pairs) and `rows`, and the mean strikes a chain holds;
- `two_sided`: the share of quotes with a bid above zero and an ask at or above it (the kit's
  `legbars.two_sided`), `zero_bid` and `crossed` (an ask under its bid) shares;
- for the intraday store, the share of quotes missing `delta` and missing `iv`;

and per symbol the store's request (or path, or name-day) outcomes: complete, empty, refused.

Counts and shares only; it is derived from licensed data all the same, so it is written under the
backtest results directory (`results/quality/`), never into the repo. `meets` checks a declared
floor against it: a declaration names the coverage it needs, and a study refuses below it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Per store: the quotes table, the request tables with their symbol column, and extra null checks.
STORES = {
    "eod_chains.db": {
        "quotes": "eod_chain_quotes",
        "requests": ("eod_chain_requests", "eod_chain_paths"),
        "nulls": (),
    },
    "intraday_chains.db": {
        "quotes": "intraday_chain_quotes",
        "requests": ("intraday_chain_requests",),
        "nulls": ("delta", "iv"),
    },
    "open_interest.db": {"quotes": None, "requests": ("oi_days",), "nulls": ()},
}


def _share(part, whole) -> float | None:
    return round(part / whole, 4) if whole else None


def quotes_by_year(conn: sqlite3.Connection, table: str, nulls: tuple[str, ...]) -> dict:
    """Per symbol and year: sessions, chains, rows, strikes per chain and the quote-shape shares."""
    null_cols = "".join(f", SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END)" for c in nulls)
    rows = conn.execute(
        f"SELECT symbol, substr(session, 1, 4) AS y, COUNT(DISTINCT session), "
        f"COUNT(DISTINCT session || expiration), COUNT(*), "
        f"SUM(CASE WHEN bid > 0 AND ask >= bid THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN bid IS NULL OR bid <= 0 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN bid > 0 AND ask < bid THEN 1 ELSE 0 END){null_cols} "
        f"FROM {table} GROUP BY symbol, y ORDER BY symbol, y"
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        sym, year, sessions, chains, n, two, zero, crossed = r[:8]
        year_doc = {
            "sessions": sessions,
            "chains": chains,
            "rows": n,
            # A chain holds a call and a put per strike: rows over chains, halved.
            "strikes_per_chain": round(n / chains / 2, 1) if chains else None,
            "two_sided": _share(two, n),
            "zero_bid": _share(zero, n),
            "crossed": _share(crossed, n),
        }
        for col, missing in zip(nulls, r[8:], strict=True):
            year_doc[f"{col}_missing"] = _share(missing, n)
        out.setdefault(sym, {})[year] = year_doc
    return out


def outcomes(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, int]]:
    """Per symbol, how the store's requests (or paths, or name-days) ended."""
    out: dict[str, dict[str, int]] = {}
    for sym, status, n in conn.execute(
        f"SELECT symbol, status, COUNT(*) FROM {table} GROUP BY symbol, status"
    ):
        out.setdefault(sym, {})[status] = n
    return out


def report(path: Path, store: str) -> dict:
    """One store's report, read-only."""
    spec = STORES[store]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        doc: dict = {"store": store}
        if spec["quotes"] and spec["quotes"] in tables:
            doc["quotes"] = quotes_by_year(conn, spec["quotes"], spec["nulls"])
        doc["requests"] = {t: outcomes(conn, t) for t in spec["requests"] if t in tables}
        return doc
    finally:
        conn.close()


def meets(doc: dict, symbol: str, years: list[str], *, min_two_sided: float, min_strikes: float) -> list[str]:
    """Why `symbol` falls short of a declared floor in `years` of a store report; empty when it
    meets it. A year with no quotes at all falls short. The floors are the declaration's: no default."""
    by_year = (doc.get("quotes") or {}).get(symbol, {})
    short = []
    for y in years:
        d = by_year.get(y)
        if d is None:
            short.append(f"{symbol} {y}: no quotes")
            continue
        if d["two_sided"] is None or d["two_sided"] < min_two_sided:
            short.append(f"{symbol} {y}: two-sided share under {min_two_sided}")
        if d["strikes_per_chain"] is None or d["strikes_per_chain"] < min_strikes:
            short.append(f"{symbol} {y}: under {min_strikes} strikes a chain")
    return short
