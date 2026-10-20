"""Our own short-put and covered-call lists, built from the afternoon income chains.

The vendor's income lists stop on 2026-10-24. These replace its short-put and covered-call tabs with
the suite's own universe and rule:

- **Names**: every name the afternoon chain fetch read (`fetch_screener_greeks.py income`): the
  options-tradable ones (weekly options and $100M a day in the stock, the liquidity verdict) plus
  the reference funds that pass the same test.
- **Short put**: the put nearest `incomerules.SHORT_PUT_DELTA` (16) on the cycle monthly.
- **Within the study's tolerance**: a pick whose delta misses its target by more than
  `incomerules.DELTA_TOLERANCE` (0.05) is no row, counted -- the replay took no such entry.
- **Covered call**: the call nearest `incomerules.COVERED_CALL_DELTA` (16) above the price, on the
  reading's covered-call expiry (`cc`: the monthly 30-45 days out, else the cycle monthly). A reading
  from before that block (2026-10-19) has no covered-call list.

Each option's delta is Black-Scholes on its own IV, solved from its mid at `incomerules.RATE`, the
screen rules' way. The price a row states is the WORKING price (`legbars.working_premium`: the mid
less a quarter of the width), the one a limit at the mid stepped down is modelled to fill at; the
return is that premium over the cash it ties up -- the strike for a put, the share price for a call --
and the annual figure is simple (x 365 / days). The liquidity verdict is the reading's own
(`passes`, against `core.legbars`), never re-judged here.

**No row is a recommendation.** The rule is the least-bad cell of two studies that found no edge
(see `cherrypick.core.incomerules`), and the list is ordered by symbol, never by yield: a yield
ranking would read as a ranking by edge. Earnings are judged against each trade's own expiry:
`in-trade` when the next report falls between the session and expiry, `clear` when it falls after,
`unknown` when a company has no date, `none` for a fund.

    python -m cherrypick.technicals income-lists [--day YYYY-MM-DD] [--source FILE]

Network-free: it reads the chain file and the security list, and writes
`income-lists/<session>.json` under this package's data dir (a `--source` run writes `.trial.json`).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import bs, incomerules, legbars
from cherrypick.core import jsonio as _jsonio

from . import paths, securities

LISTS = ("short-puts", "covered-calls")
LIST_VERSION = 1


def income_dir() -> Path:
    """The afternoon income chains (`fetch_screener_greeks.py income`). Read-only here."""
    return paths.market_report_dir() / "screener-greeks" / "income"


# ------------------------------------------------------------------------------------------------
# Pure: pricing a chain and picking the strike.


def priced(options: list[dict], spot: float, dte: int, rate: float = incomerules.RATE) -> list[dict]:
    """The options with a two-sided quote, each with its mid, the IV solved from it and its delta.
    An option whose mid no vol prices is left out."""
    if not spot or not dte or dte <= 0:
        return []
    years = dte / 365.0
    out = []
    for o in options:
        bid, ask, k, right = o.get("bid"), o.get("ask"), o.get("strike"), o.get("right")
        if bid is None or ask is None or k is None or right not in ("C", "P") or ask < bid or ask <= 0:
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        iv = bs.implied_vol(spot, float(k), years, mid, right, rate)
        d = bs.delta(spot, float(k), years, iv, right, rate) if iv else None
        if d is None:
            continue
        out.append({**o, "strike": float(k), "mid": mid, "iv": iv, "delta": d})
    return out


def nearest(options: list[dict], right: str, target: float, spot: float) -> dict | None:
    """The out-of-the-money option of `right` whose |delta| is nearest `target`; the lower strike on
    a tie, so the pick is the same every run."""
    pool = [
        o
        for o in options
        if o["right"] == right and (o["strike"] < spot if right == "P" else o["strike"] > spot)
    ]
    if not pool:
        return None
    return min(pool, key=lambda o: (abs(abs(o["delta"]) - target), o["strike"]))


def within(o: dict | None, target: float) -> dict | None | bool:
    """`o` when its |delta| is inside the tolerance of `target`; False when it is outside; None for
    no option at all."""
    if o is None:
        return None
    return o if abs(abs(o["delta"]) - target) <= incomerules.DELTA_TOLERANCE + 1e-9 else False


def earnings_state(entry: dict, session: str, expiry: str, kind: str | None) -> str:
    """in-trade / clear / unknown / none (see the module note)."""
    if kind in securities.NO_EARNINGS:
        return "none"
    er = entry.get("earnings_date")
    if not er:
        return "unknown"
    if session <= er <= expiry:
        return "in-trade"
    return "clear" if er > expiry else "unknown"


def row(sym: str, entry: dict, o: dict, expiry: str, dte: int, session: str, kind: str | None) -> dict:
    spot = float(entry["spot"])
    work = legbars.working_premium(o)
    basis = o["strike"] if o["right"] == "P" else spot
    ret = work / basis if work is not None and basis else None
    ex = entry.get("ex_dividend_date")
    return {
        "symbol": sym,
        "type": kind,
        "spot": round(spot, 4),
        "expiry": expiry,
        "dte": dte,
        "strike": o["strike"],
        "option": o.get("option"),
        "delta": round(o["delta"], 4),
        "iv": round(o["iv"], 4),
        "bid": o.get("bid"),
        "ask": o.get("ask"),
        "mid": round(o["mid"], 4),
        "working": work,
        "premium": None if work is None else round(work * 100, 2),
        "return": None if ret is None else round(ret, 6),
        "annual": None if ret is None or not dte else round(ret * 365 / dte, 6),
        "distance": round(o["strike"] / spot - 1, 6),
        "open_interest": o.get("open_interest"),
        "volume": o.get("volume"),
        "passes": o.get("passes"),
        "quiet": o.get("quiet"),
        "earnings": earnings_state(entry, session, expiry, kind),
        "earnings_date": entry.get("earnings_date"),
        "earnings_estimated": entry.get("earnings_estimated"),
        # A call written across an ex-date can be assigned early for the dividend: flagged, not modelled.
        "ex_dividend_in_trade": bool(ex and session <= ex <= expiry) if o["right"] == "C" else None,
        "iv_index": entry.get("iv_index"),
    }


def build_lists(doc: dict, kinds: dict[str, str] | None = None) -> dict[str, Any]:
    """{"lists": {list: [rows by symbol]}, "dropped": {list: {reason: count}}} from one income reading."""
    session = str(doc.get("session") or "")
    kinds = kinds or {}
    lists: dict[str, list] = {name: [] for name in LISTS}
    dropped: dict[str, dict[str, int]] = {name: {} for name in LISTS}

    def drop(name: str, why: str) -> None:
        dropped[name][why] = dropped[name].get(why, 0) + 1

    for sym, entry in sorted((doc.get("symbols") or {}).items()):
        spot = entry.get("spot")
        if entry.get("error") or not spot:
            for name in LISTS:
                drop(name, "no chain")
            continue
        kind = kinds.get(sym)
        puts = priced(
            [o for o in entry.get("options") or [] if o.get("right") == "P"], spot, entry.get("dte")
        )
        p = within(nearest(puts, "P", incomerules.SHORT_PUT_DELTA, spot), incomerules.SHORT_PUT_DELTA)
        if p is False:
            drop("short-puts", "no strike within the delta tolerance")
        elif p:
            lists["short-puts"].append(row(sym, entry, p, entry["expiry"], entry["dte"], session, kind))
        else:
            drop("short-puts", "no priced put out of the money")
        cc = entry.get("cc")
        if not cc:
            drop("covered-calls", "reading has no covered-call expiry")
            continue
        calls = priced([o for o in cc.get("options") or [] if o.get("right") == "C"], spot, cc.get("dte"))
        c = within(nearest(calls, "C", incomerules.COVERED_CALL_DELTA, spot), incomerules.COVERED_CALL_DELTA)
        if c is False:
            drop("covered-calls", "no strike within the delta tolerance")
        elif c:
            r = row(sym, entry, c, cc["expiry"], cc["dte"], session, kind)
            r["window"] = cc.get("window")
            lists["covered-calls"].append(r)
        else:
            drop("covered-calls", "no priced call out of the money")
    return {"lists": lists, "dropped": dropped}


# ------------------------------------------------------------------------------------------------
# The build.


def _kinds(day: str) -> dict[str, str]:
    """{store spelling: type} from the security list readers use (`securities.latest`)."""
    rows, _ = securities.latest(day)
    return {str(r["symbol"]).replace("/", "."): r.get("type") for r in rows if r.get("symbol")}


def latest_reading(day: str | None = None) -> Path | None:
    """The newest day's income file (never a trial or provisional one) on or before `day`."""
    day = day or date.today().isoformat()
    root = income_dir()
    days = sorted(p.stem for p in root.glob("????-??-??.json")) if root.is_dir() else []
    pick = max((d for d in days if d <= day), default=None)
    return None if pick is None else root / f"{pick}.json"


def build(day: str | None = None, source: Path | None = None) -> dict[str, Any]:
    path = source or latest_reading(day)
    if path is None or not path.exists():
        return {
            "ok": False,
            "reason": f"no income reading on or before {day or 'today'} (report-income-chains)",
        }
    doc = json.loads(path.read_text(encoding="utf-8"))
    session = str(doc.get("session") or path.stem[:10])
    built = build_lists(doc, _kinds(session))
    out = {
        "version": LIST_VERSION,
        "session": session,
        "built_at": datetime.now(UTC).isoformat(),
        "chain_taken_at": doc.get("taken_at"),
        "chain_complete": doc.get("complete"),
        "chain_problem": doc.get("problem"),
        "source": path.name,
        "rule": {
            "short_put_delta": incomerules.SHORT_PUT_DELTA,
            "covered_call_delta": incomerules.COVERED_CALL_DELTA,
            "delta_tolerance": incomerules.DELTA_TOLERANCE,
            "rate": incomerules.RATE,
            "price": "working (mid less a quarter of the width)",
            "chains": doc.get("rule"),
            "bars": doc.get("bars"),
        },
        "names": len(doc.get("symbols") or {}),
        **built,
    }
    name = f"{session}{'.trial' if source else ''}.json"
    written = _jsonio.write_json_atomic(
        paths.data_dir() / "income-lists" / name, out, indent=None, default=None
    )
    return {
        "ok": True,
        "file": str(written),
        "session": session,
        "names": out["names"],
        "rows": {k: len(v) for k, v in built["lists"].items()},
        "dropped": built["dropped"],
    }
