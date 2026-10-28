"""The fair-value file: one DCF estimate a name a session, for the income lists and the Screeners tab.

`cherrypick.core.valuation` holds the arithmetic; this module reads its inputs from the history
store and writes `valuation/<session>.json`. For every liquid optionable name on the security list:

- **spot** is the last close at or before the session (`bars`);
- **the fiscal years** are Dolt's annual statements published by the session (`statements.years`,
  the 45-day publication lag);
- **beta** is the OLS slope of the name's weekly log returns on SPY's over the trailing
  `BETA_YEARS`, both from the store's adjusted bars as adjusted on the session;
- **market cap** is spot times the last fiscal year's shares;
- **consensus growth** is the "Next Year" sales consensus against its year-ago figure at the newest
  weekly estimate snapshot on or before the session (`fundamentals.growth`), when the store has one.

The broker's beta and market cap (`market-report/iv/tastytrade.json`, the newest day at or before the
session) ride along as a cross-check: `beta_disagrees` when the two betas differ by more than
`BETA_DISAGREE`, `shares_stale` when spot times Dolt's shares is more than `SHARES_STALE` off the
broker's market cap. Ours are the figures used; the broker's are shown.

A fund, and a stock in a sector a DCF on operating cash flow does not describe (`NOT_DCF_SECTORS`:
banks earn interest, REITs are valued on their assets), is `not-applicable`. A name without
statements is `no-statements`; one the model refuses carries the model's reason. **A model estimate of
value, tested 2018-26 with no edge (scripts/fair_value_history.py), never a ranking**: rows are by symbol, and the income lists carry the
figure beside their own, never ordered by it.

    python -m cherrypick.technicals valuation [--day YYYY-MM-DD]

Local files only: the history store, the security list, the sector maps and the broker's IV file.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from cherrypick.core import jsonio as _jsonio
from cherrypick.core import treasury as _treasury
from cherrypick.core import valuation as _val

from . import fundamentals, paths, securities, statements, store

VERSION = 1
NOT_DCF_SECTORS = ("Financials", "Real Estate")
BETA_YEARS = 3
BETA_STEP = 5  # sessions between return samples: weekly
MIN_RETURNS = 52  # a year of weekly returns, or no beta
BETA_DISAGREE = 0.5
SHARES_STALE = 0.10
INDEX = "SPY"


def out_dir() -> Path:
    return paths.data_dir() / "valuation"


def latest(day: str | None = None) -> Path | None:
    """The newest valuation file on or before `day`."""
    day = day or date.today().isoformat()
    root = out_dir()
    days = sorted(p.stem for p in root.glob("????-??-??.json")) if root.is_dir() else []
    pick = max((d for d in days if d <= day), default=None)
    return None if pick is None else root / f"{pick}.json"


def load(day: str | None = None) -> dict | None:
    """The newest file's document, or None when none has been built."""
    path = latest(day)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------- the inputs


def log_returns(closes: list[float], step: int = BETA_STEP) -> list[float]:
    """Log returns over every `step` closes, oldest first; a non-positive close breaks the series."""
    out = []
    for k in range(step, len(closes), step):
        a, b = closes[k - step], closes[k]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def aligned_returns(
    stock: dict[str, float], index: dict[str, float], as_of: str, years: int = BETA_YEARS
) -> tuple[list[float], list[float]]:
    """(stock returns, index returns) over the sessions both have in the trailing `years`."""
    start = (date.fromisoformat(as_of) - timedelta(days=365 * years)).isoformat()
    dates = sorted(d for d in stock if start <= d <= as_of and d in index)
    return log_returns([stock[d] for d in dates]), log_returns([index[d] for d in dates])


def closes_as_of(conn, symbol: str, as_of: str) -> dict[str, float]:
    return {b.date: b.close for b in store.adjusted_bars(conn, symbol, as_of=as_of) if b.date <= as_of}


def spot_on(conn, symbol: str, as_of: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT date, close FROM bars WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (symbol, as_of),
    ).fetchone()
    return None if row is None or row[1] is None else (row[0], float(row[1]))


def consensus_growth(conn, symbol: str, as_of: str, snapshot: str | None = None) -> float | None:
    """The "Next Year" sales consensus against its year-ago figure at `snapshot` (default: the
    newest estimate snapshot on or before `as_of`; a build finds it once and passes it, since the
    snapshot read scans the whole estimates table)."""
    snap = snapshot or fundamentals.snapshot_on(conn, as_of)
    if snap is None:
        return None
    row = conn.execute(
        "SELECT consensus, year_ago FROM estimates WHERE symbol = ? AND date = ? AND kind = 'sales'",
        (symbol, snap),
    ).fetchone()
    return None if row is None else fundamentals.growth(row[0], row[1])


def broker_readings(as_of: str) -> dict[str, dict]:
    """{symbol: the broker's market-metrics reading} on the newest day at or before `as_of`."""
    try:
        doc = json.loads(paths.tastytrade_iv_rank().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = doc.get("days") or {}
    pick = max((d for d in days if d <= as_of), default=None)
    return dict(days.get(pick) or {}) if pick else {}


# ------------------------------------------------------------------------------------------ one name


def _flags(v: _val.Valuation, broker: dict | None, market_cap: float) -> list[str]:
    flags = list(v.flags)
    b = (broker or {}).get("beta")
    if b is not None and abs(float(b) - v.beta_raw) > BETA_DISAGREE:
        flags.append("beta_disagrees")
    mc = (broker or {}).get("market_cap")
    if mc and market_cap and abs(market_cap / float(mc) - 1.0) > SHARES_STALE:
        flags.append("shares_stale")
    return flags


def value_one(
    conn,
    symbol: str,
    as_of: str,
    kind: str | None,
    sector: str | None,
    index_closes: dict[str, float],
    broker: dict | None,
    snapshot: str | None = None,
    rf: float = _val.incomerules.RATE,
) -> dict[str, Any]:
    """One row of the file. Never raises on a name's data: the row says what stopped it."""
    row: dict[str, Any] = {
        "symbol": symbol,
        "type": kind,
        "sector": sector,
        "state": None,
        "reason": None,
        "spot": None,
        "spot_date": None,
        "fiscal_year_end": None,
        "fair_value": None,
        "low": None,
        "high": None,
        "gap": None,
        "wacc": None,
        "beta": None,
        "beta_raw": None,
        "beta_broker": None if broker is None or broker.get("beta") is None else float(broker["beta"]),
        "market_cap": None,
        "market_cap_broker": None
        if broker is None or not broker.get("market_cap")
        else float(broker["market_cap"]),
        "growth_y1": None,
        "ebit_margin": None,
        "terminal_share": None,
        "implied_ev_ebitda": None,
        "consensus_used": False,
        "flags": [],
    }
    if kind is not None and kind != "Stock":
        row.update(state="not-applicable", reason="fund")
        return row
    if sector in NOT_DCF_SECTORS:
        row.update(state="not-applicable", reason=sector.lower())
        return row
    spot = spot_on(conn, symbol, as_of)
    if spot is None:
        row.update(state="refused", reason="no_price")
        return row
    row.update(spot_date=spot[0], spot=round(spot[1], 4))
    years = statements.years(conn, symbol, as_of)
    if not years:
        row.update(state="no-statements", reason="no annual statements published by the session")
        return row
    row["fiscal_year_end"] = years[-1].end
    s, i = aligned_returns(closes_as_of(conn, symbol, as_of), index_closes, as_of)
    b = _val.beta(s, i) if len(s) >= MIN_RETURNS else None
    if b is None:
        row.update(state="refused", reason="no_beta")
        return row
    row["beta_raw"] = round(b, 4)
    market_cap = spot[1] * years[-1].shares
    row["market_cap"] = round(market_cap, 2)
    g = consensus_growth(conn, symbol, as_of, snapshot)
    row["consensus_used"] = g is not None
    out = _val.value(years, market_cap, b, consensus_growth=g, rf=rf)
    if isinstance(out, _val.Refusal):
        row.update(state="refused", reason=out.reason)
        return row
    row.update(
        state="valued",
        fair_value=round(out.fair_value, 2),
        low=round(out.low, 2),
        high=round(out.high, 2),
        gap=round(spot[1] / out.fair_value - 1.0, 4),
        wacc=round(out.wacc, 4),
        beta=round(out.beta, 4),
        growth_y1=round(out.drivers.growth[0], 4),
        ebit_margin=round(out.drivers.ebit_margin, 4),
        terminal_share=round(out.terminal_share, 4),
        implied_ev_ebitda=None if out.implied_ev_ebitda is None else round(out.implied_ev_ebitda, 2),
        flags=_flags(out, broker, market_cap),
    )
    return row


# ----------------------------------------------------------------------------------------- the build


def risk_free(as_of: str) -> tuple[float, str]:
    """(rate, where it came from): the 10-year Treasury par yield of the last curve before the
    session (`core.treasury`, 2026-10-28), else the suite's declared rate when no file is on disk."""
    read = _treasury.yield_before(as_of, "10y")
    if read is None:
        return _val.incomerules.RATE, "incomerules.RATE (no Treasury file)"
    day, pct = read
    return pct / 100.0, f"treasury 10y {day.isoformat()}"


def rule(rf: float = _val.incomerules.RATE, rf_source: str = "incomerules.RATE") -> dict[str, Any]:
    return {
        "forecast_years": _val.FORECAST_YEARS,
        "terminal_growth": _val.TERMINAL_GROWTH,
        "risk_free": rf,
        "risk_free_source": rf_source,
        "min_wacc": _val.MIN_WACC,
        "margin_years": _val.MARGIN_YEARS,
        "equity_premium": _val.EQUITY_PREMIUM,
        "beta": f"OLS of weekly log returns on {INDEX}, {BETA_YEARS} years, bounded {_val.BETA_BOUNDS}",
        "growth_bounds": _val.GROWTH_BOUNDS,
        "tax_bounds": _val.TAX_BOUNDS,
        "kd_bounds": _val.KD_BOUNDS,
        "min_years": _val.MIN_YEARS,
        "min_spread": _val.MIN_SPREAD,
        "terminal_heavy": _val.TERMINAL_HEAVY,
        "grid": {"wacc": _val.WACC_STEPS, "growth": _val.GROWTH_STEPS},
        "not_dcf_sectors": NOT_DCF_SECTORS,
        "publication_lag_days": fundamentals.PUBLICATION_LAG,
        "edge": "none measured; a model estimate of value, never a ranking",
    }


def build(day: str | None = None, path: Path | None = None, names: list[str] | None = None) -> dict[str, Any]:
    """Value the liquid optionable names as of `day` and write `valuation/<day>.json`."""
    as_of = day or date.today().isoformat()
    db = path or paths.history_db()
    if not db.exists():
        return {"ok": False, "reason": f"no history store at {db} (history land, history fundamentals)"}
    rows_sec, pick = securities.latest(as_of)
    kinds = {str(r["symbol"]).replace("/", "."): r.get("type") for r in rows_sec if r.get("symbol")}
    if names is None:
        names, source = securities.liquid_optionable(as_of)
    else:
        source = "given"
    sectors = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    broker = broker_readings(as_of)
    conn = store.connect(db)
    try:
        conn.executescript(statements.SCHEMA)
        index_closes = closes_as_of(conn, INDEX, as_of)
        if not index_closes:
            return {"ok": False, "reason": f"no {INDEX} bars in the history store"}
        # One snapshot read for the whole build: `snapshot_on` scans the estimates table (~0.3 s), and
        # a name at a time it was most of the first run's 22 minutes (2026-10-27).
        snapshot = fundamentals.snapshot_on(conn, as_of)
        rf, rf_source = risk_free(as_of)
        rows = {}
        for sym in sorted(names):
            rows[sym] = value_one(
                conn,
                sym,
                as_of,
                kinds.get(sym),
                sectors.get(sym),
                index_closes,
                broker.get(sym),
                snapshot,
                rf,
            )
    finally:
        conn.close()
    counts: dict[str, int] = {}
    for r in rows.values():
        key = r["state"] if r["state"] == "valued" else f"{r['state']}: {r['reason']}"
        counts[key] = counts.get(key, 0) + 1
    out = {
        "version": VERSION,
        "session": as_of,
        "built_at": datetime.now(UTC).isoformat(),
        "universe": source,
        "security_list": pick.get("day"),
        "rule": rule(rf, rf_source),
        "names": len(rows),
        "states": counts,
        "rows": rows,
    }
    written = _jsonio.write_json_atomic(out_dir() / f"{as_of}.json", out, indent=None, default=None)
    return {"ok": True, "file": str(written), "session": as_of, "names": len(rows), "states": counts}
