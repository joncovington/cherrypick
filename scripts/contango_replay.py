"""Replay the contango switch over stored history, through the module's own rule.

    python scripts/contango_replay.py [--start 2018-03-01] [--cost-bps 2]

Reads SVXY/SHV total-return closes from the technicals store and VIX/VIX3M closes from Cboe's
files (`scripts/fetch_market_files.py` keeps both indexes), then runs `cherrypick.contango.replay`
for each arm the module's config declares, beside buy-and-hold of the risk fund. A script rather
than a module verb because it reads another package's store.

The default start is 2018-03-01: SVXY was -1x short VIX futures until 2018-02-27 and -0.5x since, so
the history before that is a different fund, and pooling the two is how the 2018 drawdown gets
either hidden or double-counted.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pmcc_shield_replay as _shield  # noqa: E402  (its split- and dividend-aware close reader)
from cherrypick.contango import cli as _cli  # noqa: E402
from cherrypick.contango import paper_loop as _loop  # noqa: E402
from cherrypick.contango import replay as _replay  # noqa: E402
from cherrypick.overview import files as _files  # noqa: E402
from cherrypick.technicals import paths as _paths  # noqa: E402


def cboe(symbol: str) -> dict[str, float]:
    text = _files.cboe_path(symbol).read_text(encoding="utf-8")
    return {d.isoformat(): v for d, v in _files.parse_cboe(text)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2018-03-01")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--cost-bps", type=float, default=2.0)
    ap.add_argument("--config")
    args = ap.parse_args(argv)

    config = _cli.load_config(args.config)
    conn = sqlite3.connect(f"file:{_paths.history_db()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    vix, vix3m = cboe("VIX"), cboe("VIX3M")
    ratio = {d: vix[d] / vix3m[d] for d in vix if d in vix3m and vix3m[d] > 0}
    series: dict[str, dict[str, float]] = {}
    rows = []
    for arm in _loop.enabled_arms(config):
        params = _loop.merged_params(config, arm)
        risk_sym, cash_sym = _loop._symbols(params)
        for sym in (risk_sym, cash_sym):
            if sym not in series:
                series[sym] = _shield.closes(conn, sym, total_return=True)[0]
        risk, cash = series[risk_sym], series[cash_sym]
        days = [d for d in sorted(risk) if args.start <= d <= args.end]
        out = _replay.run(days, ratio, risk, cash, params, cost_bps=args.cost_bps)
        rows.append((f"{arm} ({risk_sym}/{cash_sym})", out["stats"], out["switches"], out["days_in_risk"]))
        hold = {**params, "enter_below": 99.0, "exit_at_or_above": 99.0}
        if not any(r[0].startswith(f"buy-and-hold {risk_sym}") for r in rows):
            bh = _replay.run(days, ratio, risk, cash, hold, cost_bps=args.cost_bps)
            rows.append((f"buy-and-hold {risk_sym}", bh["stats"], bh["switches"], bh["days_in_risk"]))
    for label, s, switches, in_risk in rows:
        print(
            f"{label:28} {s['start']}..{s['end']}  CAGR {s['cagr'] * 100:6.1f}%  vol {s['vol'] * 100:5.1f}%  "
            f"max drawdown {s['max_drawdown'] * 100:6.1f}%  worst day {s['worst_day'] * 100:6.1f}%  "
            f"switches {switches:4}  days in risk {in_risk}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
