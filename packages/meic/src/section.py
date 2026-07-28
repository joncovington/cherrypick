#!/usr/bin/env python3
"""Section adapter — the compact card the suite dashboard renders for this module.

`python src/section.py --json` emits a `cherrypick.core.viz` section payload: KPI tiles
(net P&L after fees, today's net, win rate, open ICs) plus the cumulative net P&L curve as a
date-axis timeseries. The orchestrator subprocesses this and renders it with no MEIC-specific
code (`dashboard.sections` in its config), exactly like the gex/flies/earnings cards. The rich
interactive views stay in this module's own `dashboard.py` server.

Reads through `dashboard.py`'s own query helpers (`_stats_for_period`, `_pnl_series`) so the
card and the full dashboard can never disagree on a number. Wins here are the module-wide
definition (a resolved trade with `pnl - fees > 0`); the headline dollars subtract fees, the
suite report's net-of-cost convention.

Defaults to the paper book — the suite dashboard only ever drives paper (`--mode paper` is
forced in its argv anyway); `--mode live` exists for a manual look at the live ledger.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
for _p in (str(_SRC), str(_SRC / "_core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cherrypick.core import viz  # noqa: E402

import dashboard as dash  # noqa: E402

_NOTE = ("net of fees · win = resolved trade with pnl − fees > 0 · "
         "0DTE iron condors, per-side stops, no profit target")


def build_section(mode: str = "paper", symbol: str | None = None, profile: str | None = None,
                  db_override: str | None = None) -> dict:
    """Return a cherrypick.core.viz section payload, or {ok: False, error}."""
    if mode not in ("paper", "live"):
        return {"ok": False, "error": f"unknown mode {mode!r} -- expected 'paper' or 'live'"}
    db_path = db_override or (dash._PAPER_DB_PATH if mode == "paper" else dash._DB_PATH)
    if not Path(db_path).exists():
        # Say so plainly rather than letting sqlite create an empty stray file just to fail
        # on the first query (before the paper loop's first session there is no ledger yet).
        return {"ok": False, "error": f"no {mode} trades DB yet ({Path(db_path).name})"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        totals = dash._stats_for_period(conn, symbol=symbol, profile=profile)
        daily = dash._pnl_series(conn, "daily", symbol=symbol, profile=profile)
        open_where = ["status IN ('open', 'partial')"]
        open_params: list = []
        if symbol and symbol.upper() != "ALL":
            open_where.append("symbol = ?")
            open_params.append(symbol.upper())
        if profile and profile.upper() != "ALL" and dash._has_column(conn, "ic_trades", "risk_profile"):
            open_where.append("risk_profile = ?")
            open_params.append(profile)
        open_count = conn.execute(
            f"SELECT COUNT(*) FROM ic_trades WHERE {' AND '.join(open_where)}", open_params
        ).fetchone()[0]
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"could not read trades DB: {exc}"}
    finally:
        conn.close()

    # Mode leads the subtitle — a live-money view must never pass for a simulated one.
    scope = (symbol or "all symbols") if (symbol or "").upper() != "ALL" else "all symbols"
    subtitle = f"{mode} · {scope}"
    if profile and profile.upper() != "ALL":
        subtitle += f" · profile {profile}"

    if not daily and not open_count:
        return {
            "ok": True,
            "title": "MEIC — no trades yet",
            "subtitle": subtitle,
            "metrics": [{"label": "Trades", "value": "0"}],
            "note": _NOTE,
        }

    # The bucket series carries both the pnl sum and the fee sum, so the card's dollars can be
    # fee-subtracted (the suite report's net-of-cost convention) while still coming from the
    # dashboard's own query path.
    labels: list[str] = []
    values: list[float] = []
    running = 0.0
    for b in daily:
        running += b["net_pnl"] - b["fees"]
        labels.append(b["period"])
        values.append(round(running, 2))
    net_after_fees = values[-1] if values else 0.0
    today_bucket = next((b for b in daily if b["period"] == dash._today()), None)
    today_net = (today_bucket["net_pnl"] - today_bucket["fees"]) if today_bucket else None

    resolved = totals["wins"] + totals["losses"]
    win_rate = f"{totals['wins'] / resolved * 100:.0f}% ({totals['wins']}/{resolved})" if resolved else "–"

    metrics = [
        {"label": "Net P&L", "value": viz.fmt_money(net_after_fees),
         "tone": "pos" if net_after_fees >= 0 else "neg"},
        {"label": "Today", "value": viz.fmt_money(today_net, none="–"),
         "tone": None if today_net is None else ("pos" if today_net >= 0 else "neg")},
        {"label": "Win rate", "value": win_rate},
        {"label": "Open ICs", "value": str(open_count), "tone": "accent"},
        {"label": "Trades", "value": str(totals["total_trades"])},
    ]

    payload: dict = {
        "ok": True,
        "title": "MEIC — iron condors",
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
    parser.add_argument("--mode", choices=["live", "paper"], default="paper",
                        help="'paper' (default) reads paper_trades.db; 'live' reads meic_trades.db. "
                             "The suite dashboard always passes --mode paper.")
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument("--symbol", default=None, help="Filter to one traded symbol (default: all).")
    parser.add_argument("--profile", default=None, help="Filter to one risk profile (default: all).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON (the only output format; the flag matches the suite's section argv shape).")
    args = parser.parse_args()
    print(json.dumps(build_section(args.mode, args.symbol, args.profile, args.db)))


if __name__ == "__main__":
    main()
