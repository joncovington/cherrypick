"""The vendor's income screens, re-created: which expiry and strike each list row gets.

**What it reproduces.** Three lists the vendor publishes every session -- short puts, covered calls
and credit spreads, one row per name -- saved by `scripts/fetch_vendor_edition.py` to
`market-report/vendor-screeners/<date>/`. Each row names an expiry, a strike (two for a spread) and
the return figures. This module states the rule each list follows and scores it against the lists,
from the broker's own chain recorded the same evening by `scripts/fetch_screener_greeks.py`
(`market-report/screener-greeks/<date>.json`: every listed strike of the row's expiry, and bid/ask,
delta and IV for the strikes around the vendor's). Local files only: no broker, no network.

**The rules, as measured (2026-10-14, re-checked 2026-10-16 over six sessions):**

- **Short put:** the expiry nearest 30 days; the put whose |delta| is nearest 0.40. Delta is
  Black-Scholes on that strike's own broker IV at a 4% rate -- the broker's own delta reproduces
  fewer rows, and a single IV per expiry (no skew) or the symbol's IV index fewer still. 91% of
  monthly rows, 77% of weekly. The misses are one strike either side, evenly: input noise, not a
  different rule (the vendor's own chain, kept by the chart capture, puts the median |delta| of its
  chosen puts at 0.400).
- **Covered call:** the expiry nearest 45 days; the call nearest |delta| 0.165. 72% monthly, 51%
  weekly. Also a delta rule: the vendor's own deltas at its chosen calls sit between 0.15 and 0.17
  for half the rows. Weeklies list strikes closer together, so the same delta noise flips more picks.
- **Credit spread:** the expiry nearest 45 days; the short leg the first listed strike out of the
  money (183 of 184 rows) -- not the ~25 delta the vendor's teaching material names; that is the
  LONG leg, nearest |delta| 0.25 (65%, misses one strike either side); and the credit at least a
  third of the width (no row below 33.06%). WHICH names make the spread list is not reproduced.
- **Returns:** raw = mid / share price, annualised = raw x 365 / days to expiry, not compounded --
  every short-put row, and about 98% of covered calls. The other covered calls divide by the price
  LESS the mid (the covered position's cost); what decides which is not known yet, so the score
  counts only the main rule.

**Not reproduced here:** which names make each list (the short-put and covered-call lists take the
vendor's liquidity rank 1-2 and a bullish read; the spread list's selection is open), and the
expiry where no chain listing was recorded (the universe builder's and the screener script's
listings cover a few hundred names a night; the rest are scored on strike only).

`score-screeners` writes one file per session to `screeners/<date>.json` under this package's data
directory and prints the totals across every scored session.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import paths

RATE = 0.04


@dataclass(frozen=True)
class Rule:
    side: str  # "P" or "C": the option the rule picks by delta
    target_dte: int
    delta: float


SHORT_PUT = Rule("P", 30, 0.40)
COVERED_CALL = Rule("C", 45, 0.165)
SPREAD_DTE = 45
SPREAD_LONG_DELTA = 0.25
SPREAD_MIN_CREDIT = 0.331  # credit / width; the lists' lowest is 33.06%

LISTS = (("short-puts", "shortPuts"), ("covered-calls", "coveredCalls"), ("credit-spreads", "creditSpreads"))


# ------------------------------------------------------------------------------------------- rules
def bs_delta(
    side: str, spot: float, strike: float, years: float, iv: float, rate: float = RATE
) -> float | None:
    if not (spot and strike and years and iv) or years <= 0 or iv <= 0:
        return None
    d1 = (math.log(spot / strike) + (rate + iv * iv / 2) * years) / (iv * math.sqrt(years))
    n = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return n if side == "C" else n - 1


def nearest_delta_strike(
    ivs: dict[float, float], side: str, spot: float, dte: float, target: float
) -> float | None:
    """The strike whose |delta| is nearest `target`, from each strike's own IV; ties to the lower."""
    scored = []
    for k, iv in ivs.items():
        d = bs_delta(side, spot, k, dte / 365, iv)
        if d is not None:
            scored.append((abs(abs(d) - target), k))
    return min(scored)[1] if scored else None


def first_otm(listed: list[float], price: float, side: str) -> float | None:
    """The first listed strike out of the money: at or below the price for a put, at or above for a call."""
    if side == "P":
        below = [k for k in listed if k <= price]
        return max(below) if below else None
    above = [k for k in listed if k >= price]
    return min(above) if above else None


def nearest_expiry(expiries: list[date], today: date, target_dte: int) -> date | None:
    """The listed expiry nearest `target_dte` days out; a tie goes to the earlier one."""
    live = [e for e in expiries if e > today]
    return min(live, key=lambda e: (abs((e - today).days - target_dte), e)) if live else None


def returns(mid: float, stock_price: float, dte: float) -> tuple[float, float] | None:
    """(raw, annualised) in percent: mid / share price, and that x 365 / days. Simple, not compounded."""
    if not stock_price or not dte:
        return None
    raw = mid / stock_price * 100
    return raw, raw * 365 / dte


# ------------------------------------------------------------------------------------------ inputs
def _pct(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return None


def _mdy(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def _monthly(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def _side_ivs(expiry: dict | None, side: str) -> dict[float, float]:
    out = {}
    for k, legs in ((expiry or {}).get("strikes") or {}).items():
        leg = legs.get(side) or {}
        if leg.get("iv"):
            out[float(k)] = leg["iv"]
    return out


def load_lists(root: Path, day: str) -> dict[str, list[dict]] | None:
    d = root / "vendor-screeners" / day
    out = {}
    for name, key in LISTS:
        p = d / f"{name}.json"
        if not p.exists():
            return None
        out[name] = json.loads(p.read_text(encoding="utf-8"))["body"].get(key) or []
    return out


def load_listings(root: Path, day: str) -> dict[str, list[date]]:
    """Every expiry listed per symbol that day, from whichever chain listing recorded it."""
    out: dict[str, list[date]] = {}
    for p in (
        root / "universe" / "chains" / f"{day}.json",
        root / "screener-greeks" / "chains" / f"{day}.json",
    ):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for sym, exps in doc.items():
            out.setdefault(
                sym, sorted({date.fromisoformat(e["expiration"]) for e in exps if e.get("expiration")})
            )
    return out


# ------------------------------------------------------------------------------------------- score
def _tally() -> dict:
    return {"hit": 0, "n": 0}


def _add(t: dict, hit: bool) -> None:
    t["hit"] += bool(hit)
    t["n"] += 1


def score_day(root: Path, day: str) -> dict | None:
    """Every row of one session scored against the rules; None when the lists or greeks are missing."""
    lists = load_lists(root, day)
    gpath = root / "screener-greeks" / f"{day}.json"
    if lists is None or not gpath.exists():
        return None
    greeks = json.loads(gpath.read_text(encoding="utf-8"))
    chains = greeks.get("symbols") or {}
    listings = load_listings(root, day)
    today = date.fromisoformat(day)
    out: dict = {"day": day, "greeks_complete": greeks.get("complete", True)}

    for name, rule in (("short-puts", SHORT_PUT), ("covered-calls", COVERED_CALL)):
        s = {"rows": len(lists[name]), "strike": {"monthly": _tally(), "weekly": _tally()}}
        s["expiry"], s["returns"], s["misses"] = _tally(), _tally(), []
        # The rule's own strike for every row it could judge, so a reader (the console's screener
        # page) shows our pick beside the vendor's without re-implementing the rule.
        s["picks"] = {}
        for row in lists[name]:
            sym = row["symbol"]
            exp = _mdy(row["expiry"])
            listed = listings.get(sym.replace(".", "/")) or listings.get(sym)
            if listed:
                _add(s["expiry"], nearest_expiry(listed, today, rule.target_dte) == exp)
            r = returns(row["midPrice"], row["stockPrice"], row["daysToExpiry"])
            raw, ann = _pct(row.get("rawReturn")), _pct(row.get("annualizedReturn"))
            if r and raw is not None and ann is not None:
                # The vendor divides the UNROUNDED mid and shows it to the cent, so the raw figure may
                # sit up to half a cent's share of the price from ours, plus its own rounding.
                tol = 0.005 / row["stockPrice"] * 100 + 0.0051
                _add(
                    s["returns"],
                    abs(r[0] - raw) <= tol and abs(r[1] - ann) <= tol * 365 / row["daysToExpiry"] + 0.0051,
                )
            g = chains.get(sym.replace(".", "/")) or chains.get(sym)
            ivs = _side_ivs((g or {}).get("expiries", {}).get(exp.isoformat()), rule.side)
            if len(ivs) < 3:
                continue
            pick = nearest_delta_strike(
                ivs, rule.side, g.get("spot") or row["stockPrice"], row["daysToExpiry"], rule.delta
            )
            hit = pick == float(row["strikePrice"])
            s["picks"][sym] = pick
            _add(s["strike"]["monthly" if _monthly(exp) else "weekly"], hit)
            if not hit:
                s["misses"].append(
                    {"symbol": sym, "expiry": exp.isoformat(), "vendor": row["strikePrice"], "ours": pick}
                )
        out[name] = s

    cs = {"rows": len(lists["credit-spreads"])}
    cs["expiry"], cs["short"], cs["long"], cs["floor"], cs["misses"] = (
        _tally(),
        _tally(),
        _tally(),
        _tally(),
        [],
    )
    cs["picks"] = {}
    for row in lists["credit-spreads"]:
        sym = row["symbol"]
        exp = _mdy(row["expiry"])
        side = "P" if row["type"] == "Put" else "C"
        listed = listings.get(sym.replace(".", "/")) or listings.get(sym)
        if listed:
            _add(cs["expiry"], nearest_expiry(listed, today, SPREAD_DTE) == exp)
        prem = row.get("premium") or {}
        pct = _pct(prem.get("percentage"))
        if pct is not None:
            _add(cs["floor"], pct / 100 >= SPREAD_MIN_CREDIT - 0.0005)
        g = chains.get(sym.replace(".", "/")) or chains.get(sym)
        e = (g or {}).get("expiries", {}).get(exp.isoformat())
        if not e:
            continue
        sell, buy = row["strike"]["sell"], row["strike"]["buy"]
        short = first_otm(e.get("listed_strikes") or [], row["price"], side)
        _add(cs["short"], short == sell)
        ivs = {k: v for k, v in _side_ivs(e, side).items() if (k < sell if side == "P" else k > sell)}
        long = None
        if ivs:
            dte = (exp - today).days
            long = nearest_delta_strike(ivs, side, g.get("spot") or row["price"], dte, SPREAD_LONG_DELTA)
            _add(cs["long"], long == buy)
        cs["picks"][sym] = [short, long]
        if short != sell or (ivs and long != buy):
            cs["misses"].append({"symbol": sym, "vendor": [sell, buy], "ours": [short, long]})
    out["credit-spreads"] = cs
    return out


def scores_dir() -> Path:
    return paths.data_dir() / "screeners"


def list_days(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "screener-greeks").glob("????-??-??.json"))


def score_all(root: Path | None = None, out_dir: Path | None = None, rescore: bool = False) -> dict:
    """Score every session with lists and greeks (unscored ones only, unless `rescore`), then total."""
    root = root or paths.market_report_dir()
    out_dir = out_dir or scores_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    scored = []
    for day in list_days(root):
        path = out_dir / f"{day}.json"
        if path.exists() and not rescore:
            continue
        result = score_day(root, day)
        if result is not None:
            path.write_text(json.dumps(result, indent=1), encoding="utf-8")
            scored.append(day)
    return {"scored": scored, **totals(out_dir)}


def _rate(t: dict) -> str | None:
    return f"{t['hit']}/{t['n']} = {t['hit'] / t['n']:.1%}" if t["n"] else None


def totals(out_dir: Path) -> dict:
    """Hit rates summed over every scored session, and per session, for the report."""
    agg: dict = {}
    days = []
    for p in sorted(out_dir.glob("????-??-??.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        days.append(d["day"])
        for name in ("short-puts", "covered-calls"):
            a = agg.setdefault(
                name, {"monthly": _tally(), "weekly": _tally(), "expiry": _tally(), "returns": _tally()}
            )
            for kind in ("monthly", "weekly"):
                for k in ("hit", "n"):
                    a[kind][k] += d[name]["strike"][kind][k]
            for part in ("expiry", "returns"):
                for k in ("hit", "n"):
                    a[part][k] += d[name][part][k]
        a = agg.setdefault("credit-spreads", {p: _tally() for p in ("expiry", "short", "long", "floor")})
        for part in a:
            for k in ("hit", "n"):
                a[part][k] += d["credit-spreads"][part][k]
    return {
        "sessions": days,
        "short-puts": {k: _rate(v) for k, v in agg.get("short-puts", {}).items()},
        "covered-calls": {k: _rate(v) for k, v in agg.get("covered-calls", {}).items()},
        "credit-spreads": {k: _rate(v) for k, v in agg.get("credit-spreads", {}).items()},
    }
