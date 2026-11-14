"""Item 77: item 57's weekly index put re-read under the three verdict bars.

A retest of item 57, said out loud: the same trades on the same weekly expiries, the unhedged 10-delta
equity-index put (SPX, SPY, QQQ, IWM) sold 7 days before a non-monthly weekly expiry and held to it,
read as item 57 built them (`trade_pair` over its chain-path store). Item 57 judged mean net in bp
of the forward notional. This judges what the book is run for, through the study kit's
`checks` (`cherrypick.backtest.study.checks`):

- **capital:** each trade's net, less the 3-month bill on its buying power over its days, above zero;
- **tail:** the 99th-percentile drawdown over one year of cycles under one year of mean net;
- **delta:** the short put's net less its entry delta financed on the same capital.

The measure is each trade's return on its buying power, the equal-weight mean over the names present
in a cycle, one cycle one observation (SPX's notional is ten times SPY's, so dollars would be SPX's
read alone). Two buying-power models are judged (no Holm step: at t above 2 on 80 or more cycles
a passing p is under Holm's strictest bar for two, so it could never bind):

- `cash`: cash-secured, the strike x 100 less the premium (an IRA's hold);
- `margin`: Reg T for a naked put, the larger of a share of the underlying less the put's
  out-of-the-money amount and a tenth of the strike, plus the premium; the share is 15% on SPX (a
  broad-based index option) and 20% on the three funds. The forward stands in for the spot.

Reported, never judged: item 57's hedged arm under both models, each half of the window (split
2021-06-01, item 57's), each name alone, and the cycles after 2026-10-30, the forward read item 57
named. The window was read by item 57, so this verdict is provisional by declaration: the forward
cycles are the fresh evidence. Loading item 57's script is the point of a retest of its trades,
not a shared piece the kit should hold.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import bs, treasury

from cherrypick.backtest.study import checks as ck

_HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


i57 = _load("index_put_weekly_item57")
i46 = i57.i46

ARMS = ("cash", "margin")
LEG = i57.LEG
#: Reg T naked-put shares of the underlying, and the floor as a share of the strike.
MARGIN_SHARE = {"SPX": 0.15}
MARGIN_SHARE_DEFAULT = 0.20
MARGIN_FLOOR = 0.10
T_BAR = 2.0
MIN_CYCLES = i46.MIN_CYCLES
RESAMPLES = 10_000
SEED = 77
SPLIT = "2021-06-01"
FORWARD_AFTER = i57.FORWARD_AFTER
RATE_STALE_DAYS = 10


def cash_bp(strike: float, premium: float) -> float:
    """A cash-secured put's hold: the strike's cash less the premium taken in."""
    return 100.0 * strike - premium


def margin_bp(symbol: str, fwd: float, strike: float, premium: float) -> float:
    """Reg T on a naked put, the forward standing in for the spot."""
    share = MARGIN_SHARE.get(symbol, MARGIN_SHARE_DEFAULT)
    otm = 100.0 * max(fwd - strike, 0.0)
    return max(share * 100.0 * fwd - otm, MARGIN_FLOOR * 100.0 * strike) + premium


def short_put_delta(fwd: float, strike: float, years: float, iv: float) -> float:
    """The short put's delta in shares at entry (positive: a short put is long the underlying)."""
    return -100.0 * bs.delta(fwd, strike, years, iv, "P", 0.0)


def trade_row(symbol: str, expiry: date, sessions: dict, fwds: dict, rate) -> dict | None:
    """One name's trade on one expiry in both arms' terms, or None when item 57 dropped it."""
    pair = i57.trade_pair(symbol, sessions, fwds, expiry, LEG)
    if "drop" in pair:
        return None
    s = i46.entry_session(expiry, i57.TENOR)
    years = i46.years_to(s, expiry)
    [leg] = i46.pick_leg(sessions[s], fwds[s], years, LEG)
    f0, f1 = fwds[s], fwds[expiry]
    days = (expiry - s).days
    premium = pair["unhedged"]["premium"]
    r = rate(s.isoformat())
    delta = short_put_delta(f0, leg["strike"], years, leg["iv"])
    out = {"symbol": symbol, "expiry": expiry.isoformat(), "entry": s.isoformat(), "days": days, "rate": r}
    models = {
        "cash": cash_bp(leg["strike"], premium),
        "margin": margin_bp(symbol, f0, leg["strike"], premium),
    }
    for arm, bp in models.items():
        out[arm] = {
            "bp": bp,
            "net": pair["unhedged"]["net"] / bp,
            "hedged": pair["hedged"]["net"] / bp,
            "financed": ck.financed_delta(delta, f0, f1, bp, r, days) / bp,
        }
    return out


def cycles(rows: list[dict], arm: str, key: str = "net") -> list[dict]:
    """Per expiry, the equal-weight mean over the names present of `key` (a return on buying power),
    with the cycle's days and bill rate (the same session for every name)."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["expiry"]].append(r)
    out = []
    for e in sorted(by):
        rs = by[e]
        out.append({
            "expiry": e,
            "value": statistics.fmean(r[arm][key] for r in rs),
            "financed": statistics.fmean(r[arm]["financed"] for r in rs),
            "days": rs[0]["days"],
            "rate": rs[0]["rate"],
            "names": len(rs),
        })  # fmt: skip
    return out


def per_year(cs: list[dict]) -> int:
    """Cycles a year: a year's days over the mean gap between cycles (about 40 for the non-monthly
    weeklies, since the monthly weeks are left out)."""
    if len(cs) < 2:
        return 1
    first, last = date.fromisoformat(cs[0]["expiry"]), date.fromisoformat(cs[-1]["expiry"])
    gap = (last - first).days / (len(cs) - 1)
    return max(1, round(365.25 / gap))


def bars(cs: list[dict]) -> dict:
    """The three bars over one arm's cycles."""
    nets = [c["value"] for c in cs]
    n = len(cs)
    capital = ck.capital_check(nets, [1.0] * n, [c["days"] for c in cs], [c["rate"] for c in cs],
                               t_bar=T_BAR, min_n=MIN_CYCLES)  # fmt: skip
    tail = ck.tail_check(
        nets, per_year=per_year(cs) if cs else 1, min_n=MIN_CYCLES, resamples=RESAMPLES, seed=SEED
    )
    delta = ck.delta_check(nets, [c["financed"] for c in cs], t_bar=T_BAR, min_n=MIN_CYCLES)
    return {"capital": capital, "tail": tail, "delta": delta}


def judge(by_arm: dict[str, dict]) -> dict[str, dict]:
    """Each arm's verdict block. No Holm step: at a t bar of 2 on `MIN_CYCLES` or more cycles a
    passing bar's one-sided p is under 0.024, below Holm's strictest bar for two arms (0.025), so it
    could never bind (item 73's reasoning)."""
    return {a: ck.verdict(b["capital"], b["tail"], b["delta"]) for a, b in by_arm.items()}


def reported(rows: list[dict], arm: str) -> dict:
    """Context beside an arm's verdict: the halves, each name, the hedged arm, the forward cycles."""
    judged = [r for r in rows if r["expiry"] <= i57.LAST_EXPIRY.isoformat()]
    fwd = [r for r in rows if r["expiry"] > FORWARD_AFTER.isoformat()]

    def mean_excess(rs, key="net"):
        cs = cycles(rs, arm, key)
        if not cs:
            return {"cycles": 0}
        ex = [c["value"] - ck.bill_on(1.0, c["rate"], c["days"]) for c in cs]
        return {"cycles": len(cs), "mean_excess": statistics.fmean(ex)}

    return {
        "halves": {
            "first": mean_excess([r for r in judged if r["expiry"] < SPLIT]),
            "second": mean_excess([r for r in judged if r["expiry"] >= SPLIT]),
        },
        "names": {s: mean_excess([r for r in judged if r["symbol"] == s]) for s in i57.MEMBERS},
        "hedged": bars(cycles(judged, arm, "hedged")),
        "forward": mean_excess(fwd),
    }


def study(rate=None, log=None) -> dict:
    rate = rate or _rate_reader()
    conn = i46._chain_store()
    rows = []
    try:
        expiries = i57.weekly_expiries() + i57.forward_expiries(date.today())
        for sym in i57.MEMBERS:
            for e in expiries:
                got = i57._sessions_for(conn, sym, e)
                if got is None:
                    continue
                row = trade_row(sym, e, *got, rate)
                if row:
                    rows.append(row)
            if log:
                log(f"{sym}: done")
    finally:
        conn.close()
    judged = [r for r in rows if r["expiry"] <= i57.LAST_EXPIRY.isoformat()]
    blocks = judge({arm: bars(cycles(judged, arm)) for arm in ARMS})
    return {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 77,
        "retest_of": 57,
        "window": [i57.FIRST_EXPIRY.isoformat(), i57.LAST_EXPIRY.isoformat()],
        "provisional": True,
        "verdict": {arm: b["verdict"] for arm, b in blocks.items()},
        "arms": {arm: {**blocks[arm], "reported": reported(rows, arm)} for arm in ARMS},
        "trades": len(judged),
    }


def _rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item77"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_study(args) -> int:
    if not i46._chain_store_path().exists():
        print("no chain store: item 57's chain paths are needed", file=sys.stderr)
        return 2
    doc = study(log=lambda m: print(m, file=sys.stderr))
    path = Path(args.out) if args.out else _out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
