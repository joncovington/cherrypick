"""The strategy verdict: is there an edge after costs in each strat_test arm, and how sure are we?

One derivation, two renderers: `strategy_report --json` prints this model and the console's
strategies page draws it, so the console never re-derives a number (the screen_report pattern).
Pure functions over closed `trades` rows; the CLI does the reading.

What it answers, per control arm (`strat_test:<strategy>`):

- **net and its uncertainty.** net = pnl - entry_cost - exit_cost (core.ledgers' earnings rule;
  settlement is already inside exit_cost, so `settlement_fees` is never subtracted again). The
  confidence interval is a fixed-seed bootstrap that resamples EARNINGS EVENTS (symbol + open
  session), not trades: every strategy opens on the same name the same night, so trades on one
  event are one draw, and resampling trades would overstate confidence. A trade count is not
  significance -- a 66-trade arm can sit at t = 0.2.
- **net / capital at risk**, so a $300 butterfly and a $3,000 iron fly read on one axis.
- **where the money goes**: gross, costs, cost drag (costs / premium), net by exit reason, the
  share that the worst three events carry, and stop exits that were in the green first.
- **covariates**: net / capital at risk by tercile of each recorded entry covariate.

Advised twins are never pooled with their control; each is reported as twin minus control over
pairs matched on symbol, strategy and open session.

Rows known to record a defect, not a decision, are FLAGGED by a dated rule list (`FLAG_RULES`)
and excluded by default; `include_flagged` keeps them. Each rule names the break or incident
that explains it (docs/operating-history.md).
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CONTROL_PREFIX = "strat_test:"
ADVISED_PREFIX = "advised:"
BOOTSTRAP_SEED = 20261023
BOOTSTRAP_DRAWS = 2000
CI_LEVEL = 0.90
COVARIATES = ("term_structure", "iv_rv_ratio", "winrate", "expected_move_pct", "implied_vs_avg_actual")
STOP_REASONS = ("stop_loss", "leg_stop_delta")

# Dated, documented rules for rows that record a defect rather than a decision. Opened dates are
# inclusive ET sessions. Add a rule here, never a hand-kept list of order ids.
FLAG_RULES = (
    {
        "key": "entry_tick_close",
        "opened_from": "2026-09-16",
        "opened_to": "2026-10-15",
        "note": "closed on the tick that opened it (hold_days 0) -- the entry-tick close bug, fixed 2026-10-17",
    },
    {
        "key": "stranded_expiry",
        "opened_from": "2026-08-25",
        "opened_to": "2026-08-27",
        "note": "rode to expiry with no exit evaluated -- the stranded cohort behind the 2026-08-31 expiry_settlement break",
    },
)


def session_of(epoch) -> str | None:
    """The ET calendar date of an epoch stamp (the session a trade opened in)."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), ET).date().isoformat()


def net_of(t: dict) -> float:
    return float(t.get("pnl") or 0.0) - float(t.get("entry_cost") or 0.0) - float(t.get("exit_cost") or 0.0)


def flag_of(t: dict) -> str | None:
    """The FLAG_RULES key a closed trade falls under, or None."""
    opened = session_of(t.get("opened_at"))
    if opened is None:
        return None
    for rule in FLAG_RULES:
        if not rule["opened_from"] <= opened <= rule["opened_to"]:
            continue
        if rule["key"] == "entry_tick_close" and t.get("hold_days") == 0:
            return rule["key"]
        if rule["key"] == "stranded_expiry" and t.get("exit_reason") in ("expired", "front_expiry"):
            return rule["key"]
    return None


def event_of(t: dict) -> tuple[str, str | None]:
    return (t.get("symbol") or "", session_of(t.get("opened_at")))


def _quantile(sorted_vals: list[float], q: float) -> float:
    i = q * (len(sorted_vals) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def clustered_ci(trades: list[dict], seed: int = BOOTSTRAP_SEED, draws: int = BOOTSTRAP_DRAWS) -> dict:
    """90% intervals for expectancy ($/trade) and net / capital at risk (%), resampling events.

    Deterministic: the same rows and seed give the same interval every run. Fewer than two
    events gives no interval -- one event cannot be resampled into a spread.
    """
    events: dict = {}
    for t in trades:
        events.setdefault(event_of(t), []).append(t)
    keys = sorted(events, key=lambda k: (k[0], k[1] or ""))
    if len(keys) < 2:
        return {"events": len(keys), "expectancy": None, "ror_pct": None}
    sums = [
        (
            sum(net_of(t) for t in events[k]),
            sum(float(t.get("capital_at_risk") or 0.0) for t in events[k]),
            len(events[k]),
        )
        for k in keys
    ]
    rng = random.Random(seed)
    exp, ror = [], []
    for _ in range(draws):
        net = cap = n = 0.0
        for _k in keys:
            s = sums[rng.randrange(len(keys))]
            net += s[0]
            cap += s[1]
            n += s[2]
        exp.append(net / n)
        if cap > 0:
            ror.append(net / cap * 100)
    tail = (1 - CI_LEVEL) / 2
    exp.sort()
    ror.sort()
    return {
        "events": len(keys),
        "expectancy": [round(_quantile(exp, tail), 2), round(_quantile(exp, 1 - tail), 2)],
        "ror_pct": [round(_quantile(ror, tail), 2), round(_quantile(ror, 1 - tail), 2)] if ror else None,
    }


def terciles(trades: list[dict], key: str) -> dict:
    """net / capital at risk by tercile of an entry covariate (rows lacking it are left out)."""
    rows = []
    for t in trades:
        v = (t.get("entry_context") or {}).get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rows.append((float(v), t))
    rows.sort(key=lambda r: r[0])
    n = len(rows)
    if n < 6:
        return {"n": n, "buckets": []}
    buckets = []
    for i in range(3):
        part = rows[i * n // 3 : (i + 1) * n // 3]
        net = sum(net_of(t) for _, t in part)
        cap = sum(float(t.get("capital_at_risk") or 0.0) for _, t in part)
        buckets.append(
            {
                "lo": round(part[0][0], 4),
                "hi": round(part[-1][0], 4),
                "n": len(part),
                "net": round(net, 2),
                "ror_pct": round(net / cap * 100, 2) if cap > 0 else None,
            }
        )
    return {"n": n, "buckets": buckets}


def arm_verdict(arm: str, trades: list[dict], retired: str | None = None, started: str | None = None) -> dict:
    """The verdict for one arm's closed trades, ordered by close."""
    trades = sorted(trades, key=lambda t: float(t.get("closed_at") or 0.0))
    nets = [net_of(t) for t in trades]
    gross = sum(float(t.get("pnl") or 0.0) for t in trades)
    costs = sum(float(t.get("entry_cost") or 0.0) + float(t.get("exit_cost") or 0.0) for t in trades)
    capital = sum(float(t.get("capital_at_risk") or 0.0) for t in trades)
    premium = sum(abs(float(t.get("entry_credit") or 0.0)) * 100 for t in trades)
    net = sum(nets)

    by_reason: dict = {}
    for t, v in zip(trades, nets, strict=True):
        r = by_reason.setdefault(
            t.get("exit_reason") or "n/r", {"reason": t.get("exit_reason") or "n/r", "n": 0, "net": 0.0}
        )
        r["n"] += 1
        r["net"] += v

    by_event: dict = {}
    for t, v in zip(trades, nets, strict=True):
        by_event[event_of(t)] = by_event.get(event_of(t), 0.0) + v
    worst = sorted(by_event.items(), key=lambda kv: kv[1])[:3]

    stops = [(t, v) for t, v in zip(trades, nets, strict=True) if t.get("exit_reason") in STOP_REASONS]
    green = [(t, v) for t, v in stops if (t.get("max_unrealized_pnl") or 0) > 0]

    cum, run = [], 0.0
    for i, (t, v) in enumerate(zip(trades, nets, strict=True), start=1):
        run += v
        cum.append(
            {
                "i": i,
                "opened": session_of(t.get("opened_at")),
                "net": round(v, 2),
                "cumulative": round(run, 2),
            }
        )

    return {
        "arm": arm,
        "strategy": arm.split(":")[-1],
        "retired": retired,
        "started": started,
        "n": len(trades),
        "wins": sum(1 for v in nets if v > 0),
        "win_pct": round(sum(1 for v in nets if v > 0) / len(nets) * 100, 1) if nets else None,
        "gross": round(gross, 2),
        "costs": round(costs, 2),
        "net": round(net, 2),
        "capital_at_risk": round(capital, 2),
        "ror_pct": round(net / capital * 100, 2) if capital > 0 else None,
        "expectancy": round(net / len(nets), 2) if nets else None,
        "ci": clustered_ci(trades),
        "cost_drag_pct": round(costs / premium * 100, 1) if premium > 0 else None,
        "by_exit_reason": sorted(
            ({**r, "net": round(r["net"], 2)} for r in by_reason.values()), key=lambda r: r["net"]
        ),
        "concentration": {
            "worst_events": [{"symbol": k[0], "opened": k[1], "net": round(v, 2)} for k, v in worst],
            "net_ex_worst3": round(net - sum(v for _, v in worst), 2),
        },
        "stops": {
            "n": len(stops),
            "net": round(sum(v for _, v in stops), 2),
            "green_first": len(green),
            "green_first_net": round(sum(v for _, v in green), 2),
        },
        "covariates": {k: terciles(trades, k) for k in COVARIATES},
        "trades": [
            {
                "order_id": t.get("order_id"),
                "symbol": t.get("symbol"),
                "opened": session_of(t.get("opened_at")),
                "exit_reason": t.get("exit_reason"),
                "net": round(v, 2),
                "capital_at_risk": t.get("capital_at_risk"),
                "max_unrealized_pnl": t.get("max_unrealized_pnl"),
            }
            for t, v in zip(trades, nets, strict=True)
        ],
        "cumulative": cum,
    }


def twin_pairs(twins: list[dict], controls: list[dict]) -> list[dict]:
    """Each advised twin arm against its control, over pairs matched on symbol, strategy and session."""
    index = {(t.get("symbol"), t.get("strategy"), session_of(t.get("opened_at"))): t for t in controls}
    by_arm: dict = {}
    for tw in twins:
        c = index.get((tw.get("symbol"), tw.get("strategy"), session_of(tw.get("opened_at"))))
        if c is None:
            continue
        by_arm.setdefault(tw.get("arm"), []).append((tw, c))
    out = []
    for arm, pairs in sorted(by_arm.items()):
        diffs = [net_of(tw) - net_of(c) for tw, c in pairs]
        out.append(
            {
                "arm": arm,
                "control_arm": CONTROL_PREFIX + pairs[0][0].get("strategy", ""),
                "strategy": pairs[0][0].get("strategy"),
                "pairs": len(pairs),
                "twin_net": round(sum(net_of(tw) for tw, _ in pairs), 2),
                "control_net": round(sum(net_of(c) for _, c in pairs), 2),
                "diff": round(sum(diffs), 2),
                "twin_better": sum(1 for d in diffs if d > 0),
            }
        )
    return out


def build(
    trades: list[dict],
    since: str | None = None,
    include_flagged: bool = False,
    breaks: list[dict] | None = None,
    retired: dict | None = None,
    started: dict | None = None,
) -> dict:
    """The whole verdict model from closed trades (any arm, entry_context already parsed)."""
    retired = retired or {}
    started = started or {}
    # `flagged` lists every flagged row in the ledger, whatever the scope, so the history table can
    # mark any row it shows; `excluded` counts only those inside the scope.
    flagged = [
        {"order_id": t.get("order_id"), "arm": t.get("arm"), "rule": f, "net": round(net_of(t), 2)}
        for t in trades
        if (f := flag_of(t)) is not None
    ]
    kept = []
    in_scope_flagged = []
    for t in trades:
        if since is not None and (session_of(t.get("opened_at")) or "") < since:
            continue
        f = flag_of(t)
        if f is not None:
            in_scope_flagged.append({"rule": f, "net": round(net_of(t), 2)})
            if not include_flagged:
                continue
        kept.append(t)

    controls: dict = {}
    twins = []
    for t in kept:
        arm = t.get("arm") or ""
        if arm.startswith(CONTROL_PREFIX):
            controls.setdefault(arm, []).append(t)
        elif arm.startswith(ADVISED_PREFIX):
            twins.append(t)

    arms = [
        arm_verdict(arm, rows, retired.get(arm.split(":")[-1]), started.get(arm.split(":")[-1]))
        for arm, rows in controls.items()
    ]
    arms.sort(key=lambda a: (a["retired"] is not None, -(a["ror_pct"] if a["ror_pct"] is not None else -1e9)))

    excluded = {}
    for f in in_scope_flagged:
        e = excluded.setdefault(f["rule"], {"rule": f["rule"], "n": 0, "net": 0.0})
        e["n"] += 1
        e["net"] = round(e["net"] + f["net"], 2)

    return {
        "since": since,
        "include_flagged": include_flagged,
        "ci_level": CI_LEVEL,
        "bootstrap": {"seed": BOOTSTRAP_SEED, "draws": BOOTSTRAP_DRAWS, "cluster": "symbol + open session"},
        "flag_rules": [dict(r) for r in FLAG_RULES],
        "excluded": list(excluded.values()),
        "flagged": flagged,
        "breaks": breaks or [],
        "arms": arms,
        "twins": twin_pairs(twins, [t for rows in controls.values() for t in rows]),
    }
