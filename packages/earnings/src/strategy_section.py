#!/usr/bin/env python3
"""Section adapter — the compact card the suite dashboard renders for this module.

`python src/strategy_section.py --json` emits a `cherrypick.core.viz` section payload: KPI tiles
(net P&L, expectancy, sample progress) plus the portfolio equity curve as a date-axis timeseries.
The orchestrator subprocesses this and renders it with no earnings-specific code
(`dashboard.sections` in its config), exactly like the gex and flies cards. The rich per-strategy
view stays in this module's own `strategy_dashboard.py`.

Every number comes from `strategy_metrics.py` — the same module `strategy_report.py` and
`strategy_dashboard.py` read — so the card can never disagree with the full surfaces.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
for _p in (str(_SRC), str(_SRC / "_core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cherrypick.core import viz  # noqa: E402

import strategy_metrics as sm  # noqa: E402

_NOTE = (
    "net of modeled tastytrade fees + slippage haircut · forced-sampling paper book · "
    "<100 trades isn't significant, <30 isn't directional"
)


def build_section(
    mode: str = "paper", profile: str | None = None, since: str | None = None, db_override: str | None = None
) -> dict:
    """Return a cherrypick.core.viz section payload, or {ok: False, error}."""
    try:
        db_path = sm.db_path_for_mode(mode, db_override)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not Path(db_path).exists():
        # Before the harness's first entry pass there is genuinely no ledger yet — say so plainly
        # rather than letting sqlite create an empty stray file just to fail on the first query.
        return {"ok": False, "error": f"no {mode} trades DB yet ({Path(db_path).name})"}
    sm.DB_PATH = db_path
    profile = profile or ("strat_test" if mode == "paper" else "default")

    try:
        closed = sm.load_closed_trades(profile=profile, since=since)
        open_trades = sm.load_open_trades(profile=profile)
    except Exception as exc:
        return {"ok": False, "error": f"could not read trades DB: {exc}"}

    # The card must never let a live-money view pass for a simulated one — mode leads the subtitle,
    # same rule as the dashboard's PAPER/LIVE badge.
    subtitle = f"{mode} · profile {profile}"
    if since:
        subtitle += f" · since {since}"

    if not closed and not open_trades:
        return {
            "ok": True,
            "title": "Earnings — no trades yet",
            "subtitle": subtitle,
            "metrics": [{"label": "Closed trades", "value": "0"}],
            "note": _NOTE,
        }

    net_total = sum(sm.net_pnl(t) for t in closed)
    exp = sm.expectancy(closed)
    sample = sm.sample_progress(closed)
    labels, values = sm.daily_equity_series(closed)

    metrics = [
        {"label": "Net P&L", "value": viz.fmt_money(net_total), "tone": "pos" if net_total >= 0 else "neg"},
        {
            "label": "Expectancy / trade",
            "value": viz.fmt_money(exp),
            "tone": None if exp is None else ("pos" if exp >= 0 else "neg"),
        },
        {"label": "Closed trades", "value": str(len(closed))},
        {"label": "Open overnight", "value": str(len(open_trades)), "tone": "accent"},
        {
            "label": "Sample",
            "value": f"{sample['count']}/{sample['significant_target']}",
            "tone": "pos" if sample["significant_met"] else ("accent" if sample["directional_met"] else None),
        },
    ]

    payload: dict = {
        "ok": True,
        "title": "Earnings — strategy test",
        "subtitle": subtitle,
        "metrics": metrics,
        "note": _NOTE,
    }
    if labels:
        payload["timeseries"] = {
            "labels": labels,
            "series": [{"name": "cum net P&L", "values": values, "tone": "accent"}],
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "paper"], default="paper")
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Book to report on. Defaults to 'strat_test' (paper) / 'default' (live).",
    )
    parser.add_argument("--since", default=None)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the only output format; the flag matches the suite's section argv shape).",
    )
    args = parser.parse_args()
    print(json.dumps(build_section(args.mode, args.profile, args.since, args.db)))


if __name__ == "__main__":
    main()
