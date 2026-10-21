"""A fundamentals score in the vendor's terms, from Dolt's point-in-time estimates (round 5).

The vendor's webinars (2024-12 onward) judge a business on four measures, each against its
industry: net margin (weighted most), estimated revenue growth, estimated EPS growth, and valuation
(forward P/E in every example, price/sales when earnings are negative). Since 2026-10 the label is
a continuous score whose clear tails are "Compelling" or "Weak"; the scale, weights and threshold
are never stated. docs/signal-log-plan.md ("Round 5") declares ours:

- **Measures, as of each weekly estimate snapshot** (Dolt `earnings`, Sundays, from 2017-10):
  - forward P/E: the raw close of the last session at or before the snapshot / the "Next Year"
    consensus EPS; where that EPS is zero or negative the name ranks behind every profitable one on
    valuation, ordered among themselves by price/sales (market value / "Next Year" consensus sales);
  - estimated EPS and revenue growth: "Next Year" consensus against its year-ago figure (the current
    year's), (next - prior) / |prior|;
  - net margin: the last four reported quarters' net income / sales, a quarter counted only
    PUBLICATION_LAG days after it ends (Dolt keeps period ends, not filing dates).
- **Score:** each measure's percentile within the name's group that week (cheaper is better for
  valuation), net margin counted twice: (valuation + EPS growth + revenue growth + 2 x margin) / 5.
  A name missing any measure, or in a group of fewer than MIN_GROUP scored names, has no score.
- **Label:** the top LABEL_FRACTION of that week's scores are "compelling", the bottom "weak".

The landing copies only what the score reads: the "Next Year" estimate rows and the quarterly
income statements, into history.db beside the bars.
"""

from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import land as _land
from . import paths

START = "2017-10-01"
PUBLICATION_LAG = 45  # days after a quarter ends before its results are counted
MARGIN_WEIGHT = 2.0
LABEL_FRACTION = 0.15
MIN_GROUP = 5
QUARTERS = 4
QUARTER_SPAN_DAYS = 400  # four quarters must end within this, or the trailing year has a hole

SCHEMA = """
CREATE TABLE IF NOT EXISTS estimates (
    symbol TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL,
    period_end TEXT, consensus REAL, year_ago REAL,
    PRIMARY KEY (symbol, date, kind));
CREATE INDEX IF NOT EXISTS estimates_by_date ON estimates (date);
CREATE TABLE IF NOT EXISTS quarters (
    symbol TEXT NOT NULL, period_end TEXT NOT NULL,
    sales REAL, net_income REAL, shares REAL,
    PRIMARY KEY (symbol, period_end));
"""


def connect(path: Path | None = None):
    from . import history

    conn = history.connect(path or paths.history_db())
    conn.executescript(SCHEMA)
    return conn


def land(cfg: dict | None = None, today: date | None = None, path: Path | None = None, progress=None) -> dict:
    """Copy the "Next Year" EPS and sales estimates and every quarterly income statement from the
    `earnings` clone. Re-run safe: rows are replaced wholesale, as the bars are."""
    cfg = {**_land.DEFAULTS, "earnings_db": "earnings", **(cfg or {})}
    today = today or date.today()
    conn = connect(path)
    newest = conn.execute("SELECT MAX(date) FROM estimates").fetchone()[0]
    first = START if newest is None else (date.fromisoformat(newest) - timedelta(days=14)).isoformat()
    report = {"from": first, "estimates": 0, "quarters": 0}
    started = time.monotonic()
    try:
        dolt = _land._connect(cfg, cfg["earnings_db"])
    except Exception as exc:  # noqa: BLE001 -- no server is a failed landing, reported, not a crash
        conn.close()
        return {"ok": False, "reason": f"dolt unreachable: {type(exc).__name__}: {exc}"}
    f = _land._f
    try:
        cur = dolt.cursor()
        spans = _land.windows(first, today)
        for k, (lo, hi) in enumerate(spans, 1):
            for kind, table in (("eps", "eps_estimate"), ("sales", "sales_estimate")):
                cur.execute(
                    f"SELECT act_symbol, date, period_end_date, consensus, year_ago FROM {table} "
                    "WHERE date >= %s AND date < %s AND period = 'Next Year'",
                    (lo, hi),
                )
                rows = [
                    (s, d.isoformat(), kind, pe.isoformat() if pe else None, f(c), f(y))
                    for s, d, pe, c, y in cur.fetchall()
                ]
                conn.executemany("INSERT OR REPLACE INTO estimates VALUES (?, ?, ?, ?, ?, ?)", rows)
                report["estimates"] += len(rows)
            conn.commit()
            if progress:
                progress(k, len(spans), lo, report["estimates"])
        cur.execute(
            "SELECT act_symbol, date, sales, net_income, average_shares FROM income_statement "
            "WHERE period = 'Quarter'"
        )
        rows = [(s, d.isoformat(), f(sa), f(ni), f(sh)) for s, d, sa, ni, sh in cur.fetchall()]
        conn.executemany("INSERT OR REPLACE INTO quarters VALUES (?, ?, ?, ?, ?)", rows)
        report["quarters"] = len(rows)
        conn.commit()
    finally:
        dolt.close()
    report.update(ok=True, seconds=round(time.monotonic() - started))
    conn.close()
    return report


# ------------------------------------------------------------------------------------- the measures


@dataclass(frozen=True)
class Measures:
    pe: float | None  # price / next-year EPS, None when that EPS is <= 0
    ps: float | None  # market value / next-year sales
    eps_growth: float | None
    revenue_growth: float | None
    margin: float | None


def growth(nxt: float | None, prior: float | None) -> float | None:
    if nxt is None or prior is None or prior == 0:
        return None
    return (nxt - prior) / abs(prior)


def trailing_margin(quarters: list[tuple[str, float | None, float | None]], snapshot: str) -> float | None:
    """Net income / sales over the last QUARTERS quarters published by `snapshot` (each counted
    PUBLICATION_LAG days after it ends), oldest first in `quarters` as (period_end, sales, net)."""
    known = [q for q in quarters if q[0] <= cutoff_for(snapshot)]
    last = known[-QUARTERS:]
    if len(last) < QUARTERS:
        return None
    span = (date.fromisoformat(last[-1][0]) - date.fromisoformat(last[0][0])).days
    if span > QUARTER_SPAN_DAYS or any(s is None or n is None for _, s, n in last):
        return None
    sales = sum(s for _, s, _ in last)
    return sum(n for _, _, n in last) / sales if sales > 0 else None


def measures(
    price: float | None,
    eps: tuple[float | None, float | None] | None,
    sales: tuple[float | None, float | None] | None,
    shares: float | None,
    margin: float | None,
) -> Measures | None:
    """One name's measures from its snapshot rows: `eps` and `sales` are (consensus, year_ago)."""
    if not price or eps is None or sales is None or eps[0] is None or sales[0] is None:
        return None
    pe = price / eps[0] if eps[0] > 0 else None
    ps = price * shares / sales[0] if shares and sales[0] > 0 else None
    return Measures(pe, ps, growth(*eps), growth(*sales), margin)


def _percentiles(values: dict[str, float], higher_is_better: bool = True) -> dict[str, float]:
    """0..1 by rank, 1 the best: the share of the other names strictly worse (ties share a place)."""
    n = len(values)
    if n < 2:
        return {s: 0.5 for s in values}
    ordered = sorted(values.values())
    out = {}
    for s, v in values.items():
        worse = bisect_left(ordered, v) if higher_is_better else n - bisect_right(ordered, v)
        out[s] = worse / (n - 1)
    return out


def valuation_key(m: Measures) -> tuple[int, float] | None:
    """Cheaper sorts first: every profitable name by P/E, then the unprofitable ones by P/S."""
    if m.pe is not None:
        return (0, m.pe)
    if m.ps is not None:
        return (1, m.ps)
    return None


def scores(by_symbol: dict[str, Measures], group_of: dict[str, str]) -> dict[str, float]:
    """The score of every name with all four measures, percentiles taken within its group."""
    groups: dict[str, list[str]] = {}
    for s, m in by_symbol.items():
        g = group_of.get(s)
        if g is None or valuation_key(m) is None:
            continue
        if m.eps_growth is None or m.revenue_growth is None or m.margin is None:
            continue
        groups.setdefault(g, []).append(s)
    out: dict[str, float] = {}
    for names in groups.values():
        if len(names) < MIN_GROUP:
            continue
        val = _percentiles({s: valuation_key(by_symbol[s]) for s in names}, higher_is_better=False)
        eps = _percentiles({s: by_symbol[s].eps_growth for s in names})
        rev = _percentiles({s: by_symbol[s].revenue_growth for s in names})
        mar = _percentiles({s: by_symbol[s].margin for s in names})
        for s in names:
            out[s] = (val[s] + eps[s] + rev[s] + MARGIN_WEIGHT * mar[s]) / (3 + MARGIN_WEIGHT)
    return out


def labels(score_by_symbol: dict[str, float], fraction: float = LABEL_FRACTION) -> dict[str, str]:
    """ "compelling" for the top `fraction` of scores, "weak" for the bottom; the rest unlabelled."""
    order = sorted(score_by_symbol, key=lambda s: score_by_symbol[s])
    k = int(len(order) * fraction)
    if k == 0:
        return {}
    return {**{s: "weak" for s in order[:k]}, **{s: "compelling" for s in order[-k:]}}


# ---------------------------------------------------------------------------------- reading history


def snapshot_dates(conn) -> list[str]:
    return [d for (d,) in conn.execute("SELECT DISTINCT date FROM estimates ORDER BY date")]


def cutoff_for(snapshot: str) -> str:
    """The last quarter end whose results count on `snapshot`."""
    return (date.fromisoformat(snapshot) - timedelta(days=PUBLICATION_LAG)).isoformat()


def snapshot_on(conn, day: str) -> str | None:
    """The newest estimate snapshot on or before `day`."""
    snaps = snapshot_dates(conn)
    k = bisect_right(snaps, day) - 1
    return snaps[k] if k >= 0 else None


def market_percentiles(
    conn, snapshot: str, group_of: dict[str, str], by_industry: bool = False
) -> dict[str, float]:
    """Every scored name's place on `snapshot`, 0..1 across the whole market (1 the best): the
    `scores` (measures ranked within the name's group) of every name with estimates, a group and a
    price that day, then ranked against each other. `conn` is the history store (estimates, quarters
    and every listed name's raw bars). With `by_industry`, the group is the name's industry among the
    names measured (`peer_groups`), `group_of` (the sectors) the fallback. The signal board labels its
    tails (`board.py`)."""
    est: dict[str, dict[str, tuple]] = {}
    for sym, kind, c, y in conn.execute(
        "SELECT symbol, kind, consensus, year_ago FROM estimates WHERE date = ?", (snapshot,)
    ):
        if sym in group_of:
            est.setdefault(sym, {})[kind] = (c, y)
    cutoff = cutoff_for(snapshot)
    ms: dict[str, Measures] = {}
    for sym, r in est.items():
        qs = conn.execute(
            "SELECT period_end, sales, net_income, shares FROM quarters WHERE symbol = ? ORDER BY period_end",
            (sym,),
        ).fetchall()
        px = conn.execute(
            "SELECT close FROM bars WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (sym, snapshot),
        ).fetchone()
        published = [q for q in qs if q[0] <= cutoff]
        shares = published[-1][3] if published else None
        margin = trailing_margin([(q[0], q[1], q[2]) for q in qs], snapshot)
        m = measures(px[0] if px else None, r.get("eps"), r.get("sales"), shares, margin)
        if m is not None:
            ms[sym] = m
    if by_industry:
        complete = [
            s
            for s, m in ms.items()
            if valuation_key(m) is not None and None not in (m.eps_growth, m.revenue_growth, m.margin)
        ]
        group_of = peer_groups(complete, group_of)
    sc = scores(ms, group_of)
    return _percentiles(sc) if sc else {}


def industries() -> dict[str, str]:
    """{symbol: Yahoo's industry} (`scripts/fetch_sectors.py`), where it gave one."""
    import json

    try:
        yahoo = json.loads(paths.yahoo_sectors().read_text(encoding="utf-8")).get("names") or {}
    except (OSError, ValueError):
        return {}
    return {s: r["industry"] for s, r in yahoo.items() if isinstance(r, dict) and r.get("industry")}


def peer_groups(names: list[str], sector_of: dict[str, str] | None = None) -> dict[str, str]:
    """{symbol: the peers it is ranked against}: its industry where `MIN_GROUP` or more of `names`
    share it, else "sector:<its sector>". A name with neither is left out, so it is not scored.

    Chosen 2026-10-17: the user's thesis is a company against its competitors, and Yahoo's 144
    industries (median 11 names) are those, where the 11 sectors (median 163) put banks, insurers and
    brokers in one group. On the saved boards it reproduces more of the fundamentals tags too."""
    sector_of = sector_of if sector_of is not None else {s: v[0] for s, v in sector_sources().items()}
    ind = industries()
    size: dict[str, int] = {}
    for s in names:
        if s in ind:
            size[ind[s]] = size.get(ind[s], 0) + 1
    out = {}
    for s in names:
        if s in ind and size[ind[s]] >= MIN_GROUP:
            out[s] = ind[s]
        elif s in sector_of:
            out[s] = f"sector:{sector_of[s]}"
    return out


def sector_map() -> dict[str, str]:
    """The market report's sector map (`universe/sectors.json`): today's names only."""
    import json

    try:
        path = paths.market_report_dir() / "universe" / "sectors.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {s: row["sector"] for s, row in (doc.get("sectors") or {}).items() if row.get("sector")}


def sector_sources() -> dict[str, tuple[str, str]]:
    """{symbol: (sector, source)}: the vendor's map (`sector_map`) where it has the name, else
    Yahoo's (`scripts/fetch_sectors.py`, relabelled to the vendor's sector names; it agreed with the
    vendor's on all 120 names sampled, 2026-10-17). The earnings calendar, the signal board and,
    from 2026-10-20, the report's sector measures read this."""
    import json

    try:
        yahoo = json.loads(paths.yahoo_sectors().read_text(encoding="utf-8")).get("names") or {}
    except (OSError, ValueError):
        yahoo = {}
    out = {
        s: (row["sector"], "yahoo") for s, row in yahoo.items() if isinstance(row, dict) and row.get("sector")
    }
    out.update({s: (v, "vendor") for s, v in sector_map().items()})
    return out
