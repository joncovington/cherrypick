"""Item 65, item 47's mirror: a long straddle across an earnings report when its implied move is
below its past moves (docs/edge-hypotheses-plan.md).

    python scripts/earnings_straddle_item65.py [--host H] [--port P] [--out-dir DIR]

Everything but the structure is item 47's, imported so the two cannot drift apart: item 31's events,
calendar and paired control sessions, the past-move table, the expected move (0.85 x the front
weekly's at-the-money straddle mid over the close), the windows, the clustered t and the pass rule's
shape. The selector is item 47's turned over:

- *cheap*: with at least item 47's `MIN_PRIOR` earlier reports, the expected move **below** both the
  mean close move of the last `SHORT_PRIOR` reports and the mean of every earlier report.

The structure is the one item 47 already stored the chains for: the first standard monthly at least
`MIN_DTE` days after the entry session. Its at-the-money straddle (the strike nearest the entry close
with both sides two-sided) is **bought** at the entry session's close at mid plus the suite's
slippage and **sold** at the reaction session's close at mid less it. The fees are a two-leg open with
no sell legs and a two-leg close with two. A leg without a two-sided exit quote is sold at intrinsic
less its entry spread, and counted. The control is the same straddle on item 31's control session (its
own monthly and at-the-money strike), sold the session after.

The measure is the net in basis points of the stock's notional (100 x the entry close). Results go under
the backtest data dir (results/research/item65/), never into the repo: the inputs are licensed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import fees, robustness

_spec = importlib.util.spec_from_file_location(
    "earnings_strangle_item47", Path(__file__).with_name("earnings_strangle_item47.py")
)
e47 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("earnings_strangle_item47", e47)
_spec.loader.exec_module(e47)
e31 = e47.e31

STRESS_COSTS = e47.STRESS_COSTS
STRESS_FEES = e47.STRESS_FEES
ARMS = ("cheap", "other", "all")


def cheap(sel: dict, em_pct: float | None) -> bool:
    """Item 47's selector turned over: the expected move below both past means."""
    return em_pct is not None and em_pct < sel["short_mean"] and em_pct < sel["long_mean"]


def atm_strike(chain: dict, spot: float) -> float | None:
    both = sorted(
        {k for k, _ in chain if e47._two_sided(chain.get((k, "C"))) and e47._two_sided(chain.get((k, "P")))}
    )
    return min(both, key=lambda k: (abs(k - spot), k)) if both else None


def price_long(symbol: str, strike: float, entry: dict, exit_: dict, exit_spot: float, spot: float) -> dict:
    """One straddle bought at entry and sold at exit: dollars and basis points of 100 x spot, at the
    working costs and at the stress. A leg without a two-sided exit quote is sold at intrinsic less its
    entry spread."""
    legs = [(strike, "C"), (strike, "P")]
    e_quotes = [{"bid": entry[k][0], "ask": entry[k][1]} for k in legs]
    debit = 100.0 * sum((q["bid"] + q["ask"]) / 2 for q in e_quotes)
    x_quotes, x_value, filled = [], 0.0, 0
    for k, q in zip(legs, e_quotes, strict=True):
        x = exit_.get(k)
        if e47._two_sided(x):
            x_quotes.append({"bid": x[0], "ask": x[1]})
            x_value += 100.0 * (x[0] + x[1]) / 2
        else:
            filled += 1
            x_value += 100.0 * max(0.0, e47.intrinsic(k[0], k[1], exit_spot) - (q["ask"] - q["bid"]))
    fee = fees.ic_open_fee(symbol, 1, legs=2, sell_legs=0, ndigits=4) + fees.ic_close_fee(
        symbol, 1, legs=2, sell_legs=2, ndigits=4
    )
    out = {"debit": debit, "exit_value": x_value, "fees": fee, "exit_unquoted_legs": filled}
    for label, cfg, f in (("working", {}, 1.0), ("stress", STRESS_COSTS, STRESS_FEES)):
        slip = fees.slippage_dollars(e_quotes, 1, cfg) + (
            fees.slippage_dollars(x_quotes, 1, cfg) if x_quotes else 0.0
        )
        net = x_value - debit - slip - f * fee
        out[label] = {"net": net, "bp": 1e4 * net / (100.0 * spot)}
    return out


def trade(conn, symbol: str, need: tuple, bars: dict) -> tuple[dict | None, str | None]:
    entry, reaction, exp = need
    spot_bar, exit_bar = bars.get(entry), bars.get(reaction)
    if not spot_bar or not exit_bar:
        return None, "no_bars"
    spot, exit_spot = spot_bar[3], exit_bar[3]
    at_entry = e47.chain(conn, symbol, entry, exp)
    if not at_entry:
        return None, "no_entry_chain"
    k = atm_strike(at_entry, spot)
    if k is None:
        return None, "no_atm_strike"
    at_exit = e47.chain(conn, symbol, reaction, exp)
    if not at_exit:
        return None, "no_exit_chain"
    p = price_long(symbol, k, at_entry, at_exit, exit_spot, spot)
    return {
        "debit": p["debit"],
        "net": p["working"]["net"],
        "bp": p["working"]["bp"],
        "stress_bp": p["stress"]["bp"],
        "exit_unquoted_legs": p["exit_unquoted_legs"],
        "strike": k,
        "expiration": exp.isoformat(),
    }, None


def verdict(design: dict, holdout: dict, other_mean: float | None) -> dict:
    """Item 47's rule for its selected arm, applied to `cheap`, with `beats_other` for its
    `beats_unselected`."""
    out = e47.verdict(design, holdout, other_mean)
    if "design_checks" in out:
        out["design_checks"]["beats_other"] = out["design_checks"].pop("beats_unselected")
    return out


def cmd_study(args) -> int:
    from cherrypick.backtest import chainfetch, settings

    events, doc, reports, span = e47._inputs(e47._dir(None, "item31"))
    names = sorted({e["symbol"] for e in events})
    bars = e47.load_bars(names, args.host, args.port)
    rows = {s: [(date.fromisoformat(d), w) for d, w in v] for s, v in doc["reports"].items()}
    moves = {s: e47.move_table(rows.get(s, []), bars.get(s, {})) for s in names}
    store = chainfetch.db_path(settings.load(None))
    conn = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    drops: dict[str, int] = defaultdict(int)
    trades: list[dict] = []
    for e in events:
        sym = e["symbol"]
        report = date.fromisoformat(e["report_date"])
        window = e47.window_of(report)
        if window is None:
            drops["outside_windows"] += 1
            continue
        b = bars.get(sym, {})
        d = e31.event_dates(e)
        spot_bar = b.get(d["entry"])
        em = None
        if d["front"] is not None and spot_bar:
            em = e47.expected_move(e47.chain(conn, sym.upper(), d["entry"], d["front"]), spot_bar[3])
        sel = e47.selection(moves.get(sym, []), report, em)
        if sel is None:
            drops["under_min_prior"] += 1
            continue
        if em is None:
            drops["no_expected_move"] += 1
            continue
        need = e47.legs_needed(e, reports, span)
        t, why = trade(conn, sym.upper(), need["event"], b)
        if t is None:
            drops[why] += 1
            continue
        c = None
        if need["control"] is not None:
            c, cwhy = trade(conn, sym.upper(), need["control"], b)
            if c is None:
                drops[f"control_{cwhy}"] += 1
        else:
            drops["no_control_session"] += 1
        trades.append(
            {
                **t,
                "symbol": sym,
                "report_date": e["report_date"],
                "entry_session": e["entry_session"],
                "window": window,
                "cheap": cheap(sel, em),
                "em_over_short": em / sel["short_mean"] if sel["short_mean"] else None,
                "control_bp": c["bp"] if c else None,
                "time_half": 0 if report < e47.HALF_START else 1,
                "name_half": robustness.name_half(sym),
            }
        )
    conn.close()
    result: dict = {"item": 65, "drops": dict(drops), "arms": {}}
    for window in ("design", "holdout"):
        w = [t for t in trades if t["window"] == window]
        for arm, pick in (
            ("cheap", lambda t: t["cheap"]),
            ("other", lambda t: not t["cheap"]),
            ("all", lambda t: True),
        ):
            result["arms"].setdefault(arm, {})[window] = e47.arm_stats(
                [t for t in w if pick(t)], bootstrap=(window == "design" and arm == "cheap")
            )
    ch = result["arms"]["cheap"]
    result["verdict"] = verdict(ch["design"], ch["holdout"], result["arms"]["other"]["design"]["net"]["mean"])
    result["exit_unquoted_legs"] = sum(t["exit_unquoted_legs"] for t in trades)
    result["licence"] = "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo"
    out = e47._dir(args.out_dir, "item65")
    (out / "trades.json").write_text(json.dumps(trades), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": result["verdict"]["verdict"],
                "trades": len(trades),
                "file": str(out / "results.json"),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--out-dir")
    return cmd_study(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
