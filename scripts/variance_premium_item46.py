"""Item 46, the variance premium by underlying, tenor and strike (docs/edge-hypotheses-plan.md): its inputs.

    python scripts/variance_premium_item46.py plan [--out FILE]       # chain-path requests
    python scripts/variance_premium_item46.py coverage [--out FILE]   # what the store holds, and the MDE

`plan` writes the input of `fetch-chain-paths`: for every name, each standard monthly cycle from
`FIRST_EXPIRY` to `LAST_EXPIRY`, the expiry's daily end-of-day path from `PATH_DAYS` (66) calendar days
before it to the expiry itself, the convention the store already holds, so paths fetched for earlier
studies are skipped by the fetch rather than fetched again. It writes under the backtest data dir
(results/research/item46/), never into the repo, and prints counts only.

**Declared, as the plan states:** the equity-index group is SPX (its SPXW third-Friday expiry,
PM-settled), SPY, QQQ and IWM; the non-equity group is GLD, SLV and TLT. A monthly expiry is the third
Friday, or the trading day before it when that Friday is a holiday.

**The coverage step** reads entry quotes and each session's forward only, never an option's exit, so
it runs before any P&L is read. Per name: what became of each path request, the first complete cycle
(SPXW's third-Friday reach is the open question), and why a (cycle, tenor) has no trade: its entry or
expiry session missing, no forward, or no leg. Per cell (group x tenor x leg): the cycles with a trade
and the declared minimum detectable effect from the spread of the per-cycle realised-variance term,
half the entry dollar gamma times the squared session-to-session forward moves summed to expiry, in bp
of the entry forward notional, each group's names equal-weighted within a cycle.

The building blocks are the study's own, declared once here:
- *Forward:* put-call parity at the strike where the call and put mids are closest, F = K + C - P,
  undiscounted. A mid needs an ask above zero and a bid at or under it.
- *Entry:* the first trading day on or after expiry minus the tenor; years to expiry are calendar days
  over 365.
- *Legs:* `straddle` at the strike nearest the forward with both sides bid; `put25`, `put10`, `call25`
  at the bid strike of that right whose Black delta (on the forward, zero rate, IV from the mid) is
  nearest the target. A mid at or under intrinsic has no IV and is never picked.
- A session with no forward is a gap: the next move spans it, and gaps are counted.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs
from cherrypick.core import calendar as cal

GROUPS = {
    "equity_index": ("SPXW", "SPY", "QQQ", "IWM"),
    "non_equity": ("GLD", "SLV", "TLT"),
}
NAMES = tuple(n for names in GROUPS.values() for n in names)
FIRST_EXPIRY = date(2016, 3, 18)
LAST_EXPIRY = date(2026, 10, 16)
PATH_DAYS = 66
TENORS = (60, 45, 30, 14, 7)
TIME_SPLIT = date(2021, 6, 1)
#: leg -> (right, |delta| target); the straddle is the call and put at the strike nearest the forward
LEGS = {"straddle": None, "put25": ("P", 0.25), "put10": ("P", 0.10), "call25": ("C", 0.25)}
#: the pass bar: one-sided 0.025, Holm across the 40 cells (the first step is 0.025 / 40)
ALPHA = 0.025
CELLS = len(GROUPS) * len(TENORS) * len(LEGS)
MIN_CYCLES = 80
#: the chain store's spelling of a right -> the one `core.bs` and the pure code here use
STORE_SIDES = {"CALL": "C", "PUT": "P"}


# ------------------------------------------------------------------------------------------------
# Pure.


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday."""
    d = cal.nth_weekday(year, month, 4, 3)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def cycles(first: date = FIRST_EXPIRY, last: date = LAST_EXPIRY) -> list[date]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        e = monthly_expiry(y, m)
        if first <= e <= last:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def path_request(symbol: str, expiry: date) -> dict:
    return {
        "symbol": symbol,
        "expiration": expiry.isoformat(),
        "start": (expiry - timedelta(days=PATH_DAYS)).isoformat(),
        "through": expiry.isoformat(),
    }


def build_requests(names=NAMES, expiries=None) -> list[dict]:
    """Every name's every cycle, names in group order: pure."""
    expiries = cycles() if expiries is None else expiries
    return [path_request(sym, e) for sym in names for e in expiries]


def held(requests: list[dict], complete: set[tuple[str, str, str, str]]) -> int:
    """How many requests the store already holds complete, under the fetch's exact key."""
    return sum((r["symbol"], r["expiration"], r["start"], r["through"]) in complete for r in requests)


def mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or ask <= 0 or bid < 0 or bid > ask:
        return None
    return (bid + ask) / 2.0


def forward(chain: list[tuple[float, str, float | None, float | None]]) -> float | None:
    """Parity at the strike where the call and put mids are closest. `chain` is (strike, right, bid, ask)."""
    mids: dict[float, dict[str, float]] = {}
    for k, right, b, a in chain:
        m = mid(b, a)
        if m is not None:
            mids.setdefault(k, {})[right] = m
    pairs = [(k, s["C"], s["P"]) for k, s in mids.items() if "C" in s and "P" in s]
    if not pairs:
        return None
    k, c, p = min(pairs, key=lambda kcp: (abs(kcp[1] - kcp[2]), kcp[0]))
    return k + c - p


def entry_session(expiry: date, tenor: int) -> date:
    d = expiry - timedelta(days=tenor)
    return d if cal.is_trading_day(d) else cal.next_trading_day(d)


def years_to(session: date, expiry: date) -> float:
    return (expiry - session).days / 365.0


def pick_leg(chain, fwd: float, years: float, leg: str) -> list[dict] | None:
    """The leg's contracts at entry, each {strike, right, mid, iv}, or None when the chain has none."""
    bid = {}
    for k, right, b, a in chain:
        m = mid(b, a)
        if m is not None and b > 0:
            iv = bs.implied_vol(fwd, k, years, m, right, 0.0)
            if iv is not None and iv > 0.0001:
                bid[(k, right)] = {"strike": k, "right": right, "mid": m, "iv": iv}
    if LEGS[leg] is None:
        both = [k for (k, r) in bid if r == "C" and (k, "P") in bid]
        if not both:
            return None
        k = min(both, key=lambda s: (abs(s - fwd), s))
        return [bid[(k, "C")], bid[(k, "P")]]
    right, target = LEGS[leg]
    side = [c for (_, r), c in bid.items() if r == right]
    if not side:
        return None

    def gap(c):
        d = bs.delta(fwd, c["strike"], years, c["iv"], right, 0.0)
        return abs(abs(d) - target) if d is not None else 9.0

    return [min(side, key=lambda c: (gap(c), c["strike"]))]


def gamma(fwd: float, contracts: list[dict], years: float) -> float | None:
    """Black gamma per point of the forward, summed over the leg's contracts."""
    from cherrypick.backtest.greeks import bs_gamma

    gs = [bs_gamma(fwd, c["strike"], c["iv"], years) for c in contracts]
    return None if any(g is None for g in gs) else sum(gs)


def rv_term_bp(gam: float, forwards: list[float]) -> float | None:
    """Half the entry gamma times the squared forward moves to expiry, in bp of the entry forward
    notional (the contract multiplier cancels). `forwards` runs from entry to expiry, gaps removed."""
    if len(forwards) < 2 or forwards[0] <= 0:
        return None
    moves = sum((b - a) ** 2 for a, b in zip(forwards, forwards[1:], strict=False))
    return 0.5 * gam * moves / forwards[0] * 1e4


def group_cycle_means(per_name: dict[str, dict[str, float]], names) -> dict[str, float]:
    """{cycle: the equal-weight mean over the group's names with a value that cycle}."""
    by_cycle: dict[str, list[float]] = {}
    for n in names:
        for cyc, v in per_name.get(n, {}).items():
            by_cycle.setdefault(cyc, []).append(v)
    return {c: sum(vs) / len(vs) for c, vs in sorted(by_cycle.items())}


def cell_mde(values: list[float]) -> dict:
    """The declared MDE at both ends of the Holm ladder; a one-sample mean, so no unpaired factor."""
    from cherrypick.core.robustness import planned_mde

    n = len(values)
    out = {"cycles": n, "enough": n >= MIN_CYCLES}
    if n < 2:
        return out
    last = planned_mde(values, n, paired=True, alpha=ALPHA, sided=1)
    first = planned_mde(values, n, paired=True, alpha=ALPHA / CELLS, sided=1)
    out.update({"sd_bp": last["sd"], "mde_bp_holm_last": last["mde"], "mde_bp_holm_first": first["mde"]})
    return out


def trading_days(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def cycle_terms(sessions: dict[date, list], expiry: date) -> tuple[dict, dict]:
    """One (name, cycle): {tenor: {leg: rv term bp}} and {tenor: drop reason or gap count}. `sessions`
    maps each stored session of the path to its chain."""
    fwds = {d: forward(ch) for d, ch in sessions.items()}
    terms: dict[int, dict[str, float]] = {}
    notes: dict[int, dict] = {}
    for tenor in TENORS:
        s = entry_session(expiry, tenor)
        if s not in sessions:
            notes[tenor] = {"drop": "entry_missing"}
            continue
        if expiry not in sessions:
            notes[tenor] = {"drop": "expiry_missing"}
            continue
        if fwds.get(s) is None:
            notes[tenor] = {"drop": "no_forward"}
            continue
        span = trading_days(s, expiry)
        path = [fwds[d] for d in span if fwds.get(d) is not None]
        notes[tenor] = {"gaps": len(span) - len(path)}
        y = years_to(s, expiry)
        legs = {}
        for leg in LEGS:
            got = pick_leg(sessions[s], fwds[s], y, leg)
            g = gamma(fwds[s], got, y) if got else None
            v = rv_term_bp(g, path) if g is not None else None
            if v is None:
                notes[tenor].setdefault("no_leg", []).append(leg)
            else:
                legs[leg] = v
        terms[tenor] = legs
    return terms, notes


# ------------------------------------------------------------------------------------------------
# The stores and the steps.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item46"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chain_store_path() -> Path:
    from cherrypick.backtest import chainfetch, settings

    return chainfetch.db_path(settings.load(None))


def _chain_store() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_chain_store_path().as_posix()}?mode=ro", uri=True)


def _complete_paths() -> set[tuple[str, str, str, str]]:
    if not _chain_store_path().exists():
        return set()
    conn = _chain_store()
    try:
        return {
            tuple(r)
            for r in conn.execute(
                "SELECT symbol, expiration, start, through FROM eod_chain_paths WHERE status = 'complete'"
            )
        }
    finally:
        conn.close()


def _path_status(conn, req: dict) -> str:
    row = conn.execute(
        "SELECT status FROM eod_chain_paths"
        " WHERE symbol = ? AND expiration = ? AND start = ? AND through = ?",
        (req["symbol"], req["expiration"], req["start"], req["through"]),
    ).fetchone()
    return row[0] if row else "missing"


def _path_sessions(conn, req: dict) -> dict[date, list]:
    days = [
        r[0]
        for r in conn.execute(
            "SELECT session FROM eod_chain_requests"
            " WHERE symbol = ? AND expiration = ? AND status = 'complete' AND session BETWEEN ? AND ?",
            (req["symbol"], req["expiration"], req["start"], req["through"]),
        )
    ]
    return {
        date.fromisoformat(d): [
            (k, STORE_SIDES.get(r, r), b, a)
            for k, r, b, a in conn.execute(
                "SELECT strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? AND session = ?"
                " AND expiration = ?",
                (req["symbol"], d, req["expiration"]),
            )
        ]
        for d in sorted(days)
    }


def coverage() -> dict:
    conn = _chain_store()
    names: dict[str, dict] = {}
    # values[(name, tenor, leg)] = {cycle: rv term bp}
    values: dict[tuple[str, int, str], dict[str, float]] = {}
    try:
        for sym in NAMES:
            status_n: dict[str, int] = {}
            drops = {t: {} for t in TENORS}
            gaps = {t: 0 for t in TENORS}
            first = None
            for e in cycles():
                req = path_request(sym, e)
                st = _path_status(conn, req)
                status_n[st] = status_n.get(st, 0) + 1
                if st != "complete":
                    continue
                first = first or req["expiration"]
                terms, notes = cycle_terms(_path_sessions(conn, req), e)
                for t, note in notes.items():
                    if "drop" in note:
                        drops[t][note["drop"]] = drops[t].get(note["drop"], 0) + 1
                        continue
                    gaps[t] += note["gaps"]
                    for leg in note.get("no_leg", []):
                        drops[t][f"no_{leg}"] = drops[t].get(f"no_{leg}", 0) + 1
                for t, legs in terms.items():
                    for leg, v in legs.items():
                        values.setdefault((sym, t, leg), {})[req["expiration"]] = v
            names[sym] = {
                "paths": status_n,
                "first_complete_cycle": first,
                "drops": {str(t): d for t, d in drops.items()},
                "gap_sessions": {str(t): g for t, g in gaps.items()},
            }
    finally:
        conn.close()
    cells = {}
    for group, members in GROUPS.items():
        for t in TENORS:
            for leg in LEGS:
                per_name = {n: values.get((n, t, leg), {}) for n in members}
                means = group_cycle_means(per_name, members)
                cells[f"{group}/{t}/{leg}"] = cell_mde(list(means.values()))
    return {
        "item": "item46",
        "step": "coverage",
        "store": str(_chain_store_path()),
        "window": [FIRST_EXPIRY.isoformat(), LAST_EXPIRY.isoformat()],
        "names": names,
        "cells": cells,
        "note": (
            "entry quotes and forwards only, no option exit read; MDE in bp of entry forward notional "
            f"from the per-cycle realised-variance term, one-sided at {ALPHA} (Holm's last step) "
            f"and {ALPHA}/{CELLS} (its first); a cell with fewer than {MIN_CYCLES} cycles is no verdict"
        ),
    }


def cmd_plan(args) -> int:
    reqs = build_requests()
    path = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "names": len(NAMES),
                "cycles": len(cycles()),
                "requests": len(reqs),
                "held_complete": held(reqs, _complete_paths()),
                "file": str(path),
            }
        )
    )
    return 0


def cmd_coverage(args) -> int:
    if not _chain_store_path().exists():
        print("no chain store: run the item46/fetch-chain-paths row first", file=sys.stderr)
        return 2
    doc = coverage()
    path = Path(args.out) if args.out else _out_dir() / "coverage.json"
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    short = sum(not c["enough"] for c in doc["cells"].values())
    print(json.dumps({"cells": len(doc["cells"]), "cells_under_min_cycles": short, "file": str(path)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("coverage", cmd_coverage)):
        sp = sub.add_parser(name)
        sp.add_argument("--out")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
