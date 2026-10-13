"""The bwb VIX9D/VIX entry gate and the bounce add-on, over the modelled history from 2011.

    python scripts/bwb_regime_gate_history.py --cboe-dir .tmp/cboe            # validates first
    python scripts/bwb_regime_gate_history.py --cboe-dir .tmp/cboe --json .tmp/gate.json

Read-only. Needs VIX9D.csv, VIX.csv and SPX.csv (`bwb_history_backtest.py fetch DIR`).

**The question.** The live path refuses an entry when VIX9D/VIX is at or above `live.vix9d_vix_max`
(1.0 in the user's config, PR #162). That gate was the best of six picked on the 2024-10..2026-10
modelled run, so on that window it is in-sample by construction. 2011-01..2024-10 is the
out-of-sample test: thirteen years the gate was not chosen on. The second question is whether the
bounce add-on earns anything over control across that history, since the live pilot plan is bounce.

**Method.** Every position comes from `bwb_history_backtest.simulate`, scaled to one index level
(`--scale`, default 7000) so 2011's trade is today's trade. The gate reads the PRIOR session's
closes (the live gate reads them at 10:00; `same_day_agreement` says how often the prior close and
the same day's close fall on the same side of the bar). Results are per settlement session, the
way the ledger's `closed_session` counts them. Paired differences (bounce minus control) are taken
position by position, so they are the add-on's own P&L.

**Validation.** `bwb_history_backtest.require_validation` runs first, and nothing is reported if the
model no longer reproduces the paper ledger. `--skip-validate` stamps the output UNVALIDATED.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bwb_history_backtest as bt  # noqa: E402

# The window the gate was chosen on (the 2026-10-09 study's run).
IN_SAMPLE = ("2024-10-07", "2026-10-01")
THRESHOLDS = (0.90, 0.95, 1.00, 1.05)


def tag_regime(rows: list[dict], vix9d: dict[str, float], vix: dict[str, float]) -> None:
    v9, vx = bt.Series(vix9d), bt.Series(vix)
    for r in rows:
        a, b = v9.prior(r["entry"]), vx.prior(r["entry"])
        r["ratio_prior"] = a / b if a and b else None
        a2, b2 = vix9d.get(r["entry"]), vix.get(r["entry"])
        r["ratio_same_day"] = a2 / b2 if a2 and b2 else None


def window(rows: list[dict], lo: str, hi: str) -> list[dict]:
    return [r for r in rows if lo <= r["entry"] <= hi]


def block(rows: list[dict], arm: str) -> dict:
    if not rows:
        return {"n": 0}
    s = bt.summarize(rows, arm)
    return {k: s[k] for k in ("n", "fires", "net", "per_trade", "max_drawdown", "worst_session", "sessions")}


def paired(rows: list[dict], a: str, b: str) -> dict:
    """Per-settlement-session differences arm a minus arm b: total, mean, and a t statistic over
    sessions (sessions, not positions: one selloff moves a whole ladder)."""
    if not rows:
        return {"n": 0}
    na, nb = bt.session_nets(rows, a), bt.session_nets(rows, b)
    diffs = [na[k] - nb[k] for k in na]
    out = {"positions": len(rows), "sessions": len(diffs), "total": round(sum(diffs))}
    if len(diffs) > 2 and statistics.pstdev(diffs) > 0:
        out["t"] = round(statistics.mean(diffs) / (statistics.stdev(diffs) / math.sqrt(len(diffs))), 2)
    return out


def study(rows: list[dict], bar: float = 1.0) -> dict:
    rows = [r for r in rows if not r.get("refused") and r.get("ratio_prior") is not None]
    end = max(r["entry"] for r in rows)
    periods = {
        "out_of_sample": ("2011-01-01", "2024-10-04"),
        "in_sample": IN_SAMPLE,
        "since_in_sample": ("2026-10-02", end),
        "all": ("2011-01-01", end),
    }
    out: dict = {"bar": bar, "periods": {}}
    for name, (lo, hi) in periods.items():
        rs = window(rows, lo, hi)
        kept = [r for r in rs if r["ratio_prior"] < bar]
        skipped = [r for r in rs if r["ratio_prior"] >= bar]
        cell = {
            "window": [lo, hi],
            "positions": len(rs),
            "kept": len(kept),
            "skipped": len(skipped),
            "arms": {},
        }
        for arm in bt.ARMS:
            cell["arms"][arm] = {
                "ungated": block(rs, arm),
                "gated": block(kept, arm),
                "skipped": block(skipped, arm),
            }
        cell["bounce_minus_control"] = {
            "ungated": paired(rs, "bounce", "control"),
            "gated": paired(kept, "bounce", "control"),
        }
        cell["delta_minus_control"] = {
            "ungated": paired(rs, "delta", "control"),
            "gated": paired(kept, "delta", "control"),
        }
        out["periods"][name] = cell
    per_year: dict = defaultdict(dict)
    for r in rows:
        y = r["exp"][:4]
        for arm in bt.ARMS:
            c = per_year[y].setdefault(arm, {"n": 0, "net": 0.0, "n_gated": 0, "net_gated": 0.0})
            c["n"] += 1
            c["net"] += r["net_" + arm]
            if r["ratio_prior"] < bar:
                c["n_gated"] += 1
                c["net_gated"] += r["net_" + arm]
    out["per_year"] = {
        y: {a: {k: round(v) if isinstance(v, float) else v for k, v in c.items()} for a, c in arms.items()}
        for y, arms in sorted(per_year.items())
    }
    both = [r for r in rows if r["ratio_same_day"] is not None]
    out["same_day_agreement"] = round(
        sum((r["ratio_prior"] < bar) == (r["ratio_same_day"] < bar) for r in both) / len(both), 3
    )
    oos = window(rows, *periods["out_of_sample"])
    out["thresholds_out_of_sample"] = {}
    for t in THRESHOLDS:
        kept = [r for r in oos if r["ratio_prior"] < t]
        out["thresholds_out_of_sample"][str(t)] = {
            "kept": len(kept),
            **{a: round(sum(r["net_" + a] for r in kept) / len(kept), 1) if kept else None for a in bt.ARMS},
            **{a + "_skipped": _per_trade([r for r in oos if r["ratio_prior"] >= t], a) for a in bt.ARMS},
        }
    tue = [r for r in rows if date.fromisoformat(r["entry"]).weekday() == 1]
    out["live_pilot_shape"] = {
        "note": "bounce, one lot, Tuesdays only, gated (the 2026-10-09 pilot plan)",
        "all_tuesdays": block(tue, "bounce"),
        "gated_tuesdays": block([r for r in tue if r["ratio_prior"] < bar], "bounce"),
        "gated_tuesdays_out_of_sample": block(
            [r for r in window(tue, *periods["out_of_sample"]) if r["ratio_prior"] < bar], "bounce"
        ),
    }
    return out


def _per_trade(rows, arm):
    return round(sum(r["net_" + arm] for r in rows) / len(rows), 1) if rows else None


def print_study(res: dict) -> None:
    agree = res["same_day_agreement"]
    print(f"\nVIX9D/VIX < {res['bar']} at the prior close; prior-vs-same-day agreement {agree:.1%}")
    for name, cell in res["periods"].items():
        lo, hi = cell["window"]
        print(
            f"\n{name} {lo}..{hi}: {cell['positions']} positions,"
            f" kept {cell['kept']}, skipped {cell['skipped']}"
        )
        for arm, c in cell["arms"].items():
            u, g, s = c["ungated"], c["gated"], c["skipped"]
            print(
                f"  {arm:<8} ungated ${u['net']:>9,} ({u['per_trade']:>6}/tr, dd ${u['max_drawdown']:,})"
                f" | gated ${g.get('net', 0):>9,} ({g.get('per_trade', 0):>6}/tr,"
                f" dd ${g.get('max_drawdown', 0):,}) | skipped {s.get('per_trade', '-')}/tr"
            )
        for k in ("bounce_minus_control", "delta_minus_control"):
            u, g = cell[k]["ungated"], cell[k]["gated"]
            print(
                f"  {k}: ungated ${u.get('total', 0):,} (t {u.get('t')}),"
                f" gated ${g.get('total', 0):,} (t {g.get('t')})"
            )
    print("\nper year (settlement year): net ungated / gated (n gated of n)")
    for y, arms in res["per_year"].items():
        print(
            f"  {y} "
            + "  ".join(
                f"{a} {c['net']:>8,}/{c['net_gated']:>8,} ({c['n_gated']}/{c['n']})" for a, c in arms.items()
            )
        )
    print("\nthresholds, out of sample, net per trade kept (skipped):")
    for t, c in res["thresholds_out_of_sample"].items():
        print(
            f"  < {t}: kept {c['kept']}  " + "  ".join(f"{a} {c[a]} ({c[a + '_skipped']})" for a in bt.ARMS)
        )
    p = res["live_pilot_shape"]
    print(f"\n{p['note']}:")
    for k in ("all_tuesdays", "gated_tuesdays", "gated_tuesdays_out_of_sample"):
        c = p[k]
        print(
            f"  {k}: n {c['n']}, net ${c['net']:,} ({c['per_trade']}/tr), max drawdown ${c['max_drawdown']:,}"
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cboe-dir")
    ap.add_argument("--ledger")
    ap.add_argument("--params")
    ap.add_argument("--scale", type=float, default=7000.0)
    ap.add_argument("--bar", type=float, default=1.0)
    ap.add_argument("--start", default="2011-01-03")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    if args.skip_validate:
        print("UNVALIDATED: the model was not checked against the paper ledger on this run")
    else:
        bt.require_validation(bt.context("spx", args.cboe_dir, args.params), args.ledger)
    ctx = bt.context("auto", args.cboe_dir, args.params)
    rows = bt.backtest(ctx, args.start, args.end, args.scale)
    tag_regime(rows, bt.cboe("VIX9D", args.cboe_dir), bt.cboe("VIX", args.cboe_dir))
    res = study(rows, args.bar)
    res["scale"] = args.scale
    res["validated"] = not args.skip_validate
    print_study(res)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
