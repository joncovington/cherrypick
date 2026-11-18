"""The Builder's run card: one automation backtest's result read into what the page draws. Pure.

The driver (`packages/backtest/scripts/automation_backtest.py`) writes each result with every closed
trade (its gross, fees, settlement and slippage, and its net per pricing kind) and, per automation, why
each session did or did not enter. This reads those back; its money figures are the run's own:

- **The net is the verdict kind's** (`net["verdict"]`, the suite's slippage model), as paper's P&L is.
  Gross, fees, settlement and the verdict slippage each keep their own column, and on every row
  gross - fees - settlement - slippage = P&L, the years adding to All.
- **Net at mid beside it** (`net["mid"]`, as the run recorded it): the same trades filled at mid, so
  gross - fees - settlement before any slippage. It is P&L plus the slippage column, and is shown as
  context, never as the P&L.
- **Trades are ordered by their exit** (session, then minute), and each falls in its exit session's
  year. The drawdown is the closed trades' cumulative net below its running peak.
- **Why sessions were skipped** is the driver's own count of each session's last reason, `entered`
  left out.
- **Robustness reads** (`robustness_reads`) are the study kit's reads of the same trades: best
  sessions, cost stress, the bootstrap drawdown range and profitable quarters. In-sample, one run.
- **What a run changed** is the difference between this run's stored automation and the previous
  run's (by `generated_at`), key by key.

Description only: no trial, no verdict, nothing to the advisor.
"""

from __future__ import annotations

from typing import Any

from cherrypick.core import robustness

KIND = "verdict"
#: The curve's most points: a long run is thinned evenly, keeping its last trade.
CURVE_POINTS = 400
SPARK_POINTS = 48


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0


def _net(t: dict) -> float:
    net = t.get("net")
    return _num(net.get(KIND) if isinstance(net, dict) else net)


def _net_mid(t: dict) -> float:
    net = t.get("net")
    return _num(net.get("mid")) if isinstance(net, dict) else _net(t)


def _slip(t: dict) -> float:
    s = t.get("slippage")
    return _num(s.get(KIND) if isinstance(s, dict) else s)


def _minute(v: Any) -> int:
    """A minute of the day: the driver's integer, or an "HH:MM" string."""
    if isinstance(v, str) and ":" in v:
        h, _, m = v.partition(":")
        return int(_num(h)) * 60 + int(_num(m))
    return int(_num(v))


def ordered(trades: list[dict]) -> list[dict]:
    """The trades in the order they closed."""
    return sorted(
        trades,
        key=lambda t: (str(t.get("exit_session") or t.get("session") or ""), _minute(t.get("exit_minute"))),
    )


def _thin(rows: list, most: int) -> list:
    if len(rows) <= most:
        return rows
    step = (len(rows) - 1) / (most - 1)
    return [rows[round(i * step)] for i in range(most)]


def curve(trades: list[dict], most: int = CURVE_POINTS) -> list[dict]:
    """Cumulative net and gross by closed trade, with the drawdown below the net's running peak."""
    out, net, gross, peak = [], 0.0, 0.0, 0.0
    for i, t in enumerate(ordered(trades)):
        net += _net(t)
        gross += _num(t.get("gross"))
        peak = max(peak, net)
        out.append({"i": i + 1, "session": t.get("exit_session") or t.get("session"),
                    "net": round(net, 2), "gross": round(gross, 2), "dd": round(net - peak, 2)})  # fmt: skip
    return _thin(out, most)


def max_drawdown(trades: list[dict]) -> float:
    net, peak, worst = 0.0, 0.0, 0.0
    for t in ordered(trades):
        net += _net(t)
        peak = max(peak, net)
        worst = min(worst, net - peak)
    return round(worst, 2)


def _row(label: str, trades: list[dict]) -> dict:
    return {
        "period": label,
        "trades": len(trades),
        "gross": round(sum(_num(t.get("gross")) for t in trades), 2),
        "fees": round(sum(_num(t.get("fees")) for t in trades), 2),
        "settlement": round(sum(_num(t.get("settlement")) for t in trades), 2),
        "slippage": round(sum(_slip(t) for t in trades), 2),
        "net": round(sum(_net(t) for t in trades), 2),
        "net_mid": round(sum(_net_mid(t) for t in trades), 2),
        "max_dd": max_drawdown(trades),
    }


def years(trades: list[dict]) -> list[dict]:
    """One row per exit year, then All."""
    by: dict[str, list[dict]] = {}
    for t in ordered(trades):
        by.setdefault(str(t.get("exit_session") or t.get("session") or "")[:4], []).append(t)
    return [_row(y, ts) for y, ts in sorted(by.items())] + [_row("All", trades)]


def tiles(trades: list[dict], sessions: int | None) -> dict:
    n = len(trades)
    net = round(sum(_net(t) for t in trades), 2)
    dd = max_drawdown(trades)
    return {
        "net": net,
        "net_mid": round(sum(_net_mid(t) for t in trades), 2),
        "gross": round(sum(_num(t.get("gross")) for t in trades), 2),
        "trades": n,
        "sessions": sessions,
        "per_trade": round(net / n, 2) if n else None,
        "win_rate": round(sum(1 for t in trades if _net(t) > 0) / n, 4) if n else None,
        "max_dd": dd,
        "net_over_dd": round(net / -dd, 2) if dd < 0 else None,
    }


#: A quarter counts toward "profitable quarters" only with at least this many sessions traded.
QUARTER_MIN_SESSIONS = 10
#: The drawdown bootstrap's resample count for a card: fewer than a kill-criteria derive's, because the
#: card is read on demand. The work is resamples x the run's length, so a long run gets fewer
#: resamples (never under the floor); the card shows the count used.
CARD_RESAMPLES = 2_000
CARD_RESAMPLES_FLOOR = 400
CARD_BOOTSTRAP_WORK = 1_000_000


def card_resamples(n_trades: int) -> int:
    return max(CARD_RESAMPLES_FLOOR, min(CARD_RESAMPLES, CARD_BOOTSTRAP_WORK // max(1, n_trades)))


def _quarter(t: dict) -> str:
    s = str(t.get("exit_session") or t.get("session") or "")
    return f"{s[:4]}-Q{(int(s[5:7]) - 1) // 3 + 1}" if len(s) >= 7 and s[5:7].isdigit() else "?"


def quarters(trades: list[dict], min_sessions: int = QUARTER_MIN_SESSIONS) -> dict:
    """Each exit quarter's trades, sessions traded and net, and how many quarters with at least
    `min_sessions` sessions were profitable. A thinner quarter is shown, never counted."""
    by: dict[str, list[dict]] = {}
    for t in ordered(trades):
        by.setdefault(_quarter(t), []).append(t)
    rows = []
    for q, ts in sorted(by.items()):
        n_sessions = len({t.get("exit_session") or t.get("session") for t in ts})
        net = round(sum(_net(t) for t in ts), 2)
        rows.append(
            {
                "quarter": q,
                "trades": len(ts),
                "sessions": n_sessions,
                "net": net,
                "counted": n_sessions >= min_sessions,
            }
        )
    counted = [r for r in rows if r["counted"]]
    return {
        "rows": rows,
        "min_sessions": min_sessions,
        "counted": len(counted),
        "profitable": sum(1 for r in counted if r["net"] > 0),
    }


def robustness_reads(trades: list[dict]) -> dict:
    """The study kit's robustness reads, over this one run's trades: description, never a verdict.

    - `best_sessions`: what the 5 best exit sessions carry (`core.robustness.best_sessions`).
    - `cost_stress`: mean net per trade with fees and slippage scaled by 1.5x and 2x. Settlement is
      left out: it is the exercise or assignment charge, which does not move with the fill.
    - `drawdown`: the stationary block bootstrap of the trades in close order, over a horizon of the
      run's own length, beside the run's actual max drawdown (both as losses, >= 0).
    - `quarters`: see `quarters`."""
    ts = ordered(trades)
    by_session: dict[str, float] = {}
    for t in ts:
        s = str(t.get("exit_session") or t.get("session") or "")
        by_session[s] = by_session.get(s, 0.0) + _net(t)
    nets = [_net(t) for t in ts]
    costs = [_num(t.get("fees")) + _slip(t) for t in ts]
    stress = robustness.cost_stress(nets, costs) if ts else None
    if ts:
        boot = robustness.drawdown_range(
            nets, sessions=len(by_session), horizon=len(ts), resamples=card_resamples(len(ts))
        )
    else:
        boot = {"insufficient": True, "evidence_trades": 0}
    return {
        "best_sessions": robustness.best_sessions(by_session),
        "cost_stress": None
        if stress is None
        else {
            "rows": [
                {"multiplier": m, "mean_net": v} for m, v in sorted(stress["mean_by_multiplier"].items())
            ],
            "pass_stress": stress["pass_stress"],
            "survives": stress["survives"],
        },
        "drawdown": {"actual": -max_drawdown(trades), **boot},
        "quarters": quarters(trades),
    }


def skips(reasons: dict | None) -> list[dict]:
    """Why sessions did not enter, most first; `entered` is not a skip."""
    rows = [
        {"reason": str(k), "sessions": int(_num(v))} for k, v in (reasons or {}).items() if k != "entered"
    ]
    return sorted(rows, key=lambda r: (-r["sessions"], r["reason"]))


def _flat(v: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k in sorted(v):
            if str(k).startswith("_"):
                continue
            out.update(_flat(v[k], f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: v}


#: Keys that say nothing about the trades: the run switches the automation on, and notes are prose.
IGNORED = {"enabled", "note", "notes", "register"}


def changes(before: dict | None, after: dict | None) -> list[dict]:
    """Each key whose value differs between two stored automations: {key, before, after}; a key only
    one side has reads None on the other. Empty when there is nothing to compare with."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    a = {k: v for k, v in _flat(before).items() if k.split(".")[0] not in IGNORED}
    b = {k: v for k, v in _flat(after).items() if k.split(".")[0] not in IGNORED}
    return [
        {"key": k, "before": a.get(k), "after": b.get(k)}
        for k in sorted(set(a) | set(b))
        if a.get(k) != b.get(k)
    ]


def automation_of(doc: dict, name: str) -> dict | None:
    arms = doc.get("arms")
    return arms.get(name) if isinstance(arms, dict) else None


def trade_log(trades: list[dict]) -> list[dict]:
    """Each trade in close order, its money columns as the run recorded them."""
    out = []
    for t in ordered(trades):
        out.append(
            {
                "session": t.get("session"),
                "entry_minute": t.get("entry_minute"),
                "exit_session": t.get("exit_session"),
                "exit_minute": t.get("exit_minute"),
                "exit_kind": t.get("exit_kind"),
                "expiry": t.get("expiry"),
                "legs": t.get("legs") or [],
                "quantity": t.get("quantity"),
                "entry_mid": t.get("entry_mid"),
                "exit_mid": t.get("exit_mid"),
                "gross": _num(t.get("gross")),
                "fees": _num(t.get("fees")),
                "settlement": _num(t.get("settlement")),
                "slippage": _slip(t),
                "net": _net(t),
            }
        )
    return out


def card(doc: dict, name: str, previous: dict | None = None) -> dict:
    """The whole run card for automation `name` in result `doc` (`previous`: the run before it)."""
    trades = [t for t in doc.get("trades") or [] if t.get("arm", name) == name]
    summary = (doc.get("summary") or {}).get(name) or {}
    return {
        "run": doc.get("name"),
        "automation": name,
        "symbol": doc.get("symbol"),
        "source": doc.get("source"),
        "store": doc.get("store"),
        "cadence": doc.get("cadence"),
        "sessions": doc.get("sessions"),
        "session_count": doc.get("session_count"),
        "still_open": doc.get("still_open"),
        "generated_at": doc.get("generated_at"),
        "signal_versions": doc.get("signal_versions"),
        "tiles": tiles(trades, summary.get("sessions", doc.get("session_count"))),
        "curve": curve(trades),
        "years": years(trades),
        "robustness": robustness_reads(trades),
        "skips": skips(summary.get("reasons")),
        "previous": previous.get("name") if previous else None,
        "changes": changes(automation_of(previous, name) if previous else None, automation_of(doc, name)),
        "trades": trade_log(trades),
    }


def brief(doc: dict, name: str, previous: dict | None = None) -> dict:
    """A run as the explore-runs list shows it: its net curve thinned for a sparkline, and what it
    changed from the run before."""
    trades = [t for t in doc.get("trades") or [] if t.get("arm", name) == name]
    return {
        "spark": [p["net"] for p in curve(trades, SPARK_POINTS)],
        "net": round(sum(_net(t) for t in trades), 2),
        "changes": changes(automation_of(previous, name) if previous else None, automation_of(doc, name)),
    }
