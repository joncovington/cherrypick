"""Does the fair-value gap say anything about what comes next? A point-in-time history study.

The fair-value file (`technicals valuation`, `cherrypick.core.valuation`) carries no measured edge:
docs/conventions and the Screeners tab say so. This is the test. For the last session of every
month from `START` on, each liquid optionable stock is valued exactly as the nightly build values
it, from what was knowable that day -- Dolt's annual statements published 45 days after the fiscal
year end, the newest weekly consensus snapshot, a three-year weekly beta on SPY, the price that day
-- and its gap (price over value, less one) is set against what the shares then did.

**Declared before the first run (2026-10-28):**

- **Outcomes.** The 63-session forward total return (dividend-adjusted closes), and a short-put
  proxy: whether the shares were still within `PUT_OTM` (8%) of the entry price `PUT_HORIZON` (42)
  sessions on -- roughly a 16-delta put on the 45-60 day cycle finishing out of the money. The proxy
  is crude and says so; the income lists' real outcomes start only in 2026-10.
- **The measure.** Each session, the valued names are cut into terciles by gap; the spread is the
  cheap tercile's mean forward return less the rich tercile's (and the same for the put-proxy win
  rate), plus Spearman's rank correlation of gap and forward return. A session with fewer than
  `MIN_NAMES` valued names is skipped.
- **The pass test.** The study PASSES only if, in BOTH halves of the window (split at the midpoint
  session), the cheap-less-rich return spread is positive in more sessions than not with an exact
  sign test at p < 0.05, and the median spread is positive. Anything else FAILS; fewer than
  `MIN_SESSIONS` sessions in a half is NO VERDICT. A pass means the gap selects; it does not size a
  trade or make the lists a ranking.
- **Known limits, declared.** The universe is today's liquid optionable list, so names that died or
  delisted are missing (survivorship); the risk-free rate is the suite's constant 4% for every
  session (the Treasury file starts 2025); dividend adjustment is as of today, not the session;
  statements before 2016 are thinner in Dolt.

    python scripts/fair_value_history.py [--start 2018-01-01] [--sessions N] [--names N] [--out FILE]

Local files only (the history store, the security list, the sector maps). Writes one JSON to the
technicals study directory and prints the verdict. Public data and our own model: the result may be
quoted.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime

from cherrypick.core import stats
from cherrypick.core import valuation as _val
from cherrypick.core.jsonio import write_json_atomic
from cherrypick.technicals import fundamentals, paths, securities, statements, store
from cherrypick.technicals import valuation as _tv

START = "2018-01-01"
FORWARD = 63  # sessions, ~3 months
PUT_HORIZON = 42  # sessions, ~2 months: the 45-60 day cycle
PUT_OTM = 0.08  # a 16-delta put sits about 8% below the price over that horizon at typical vols
MIN_NAMES = 30
MIN_SESSIONS = 24
ALPHA = 0.05


def month_ends(dates: list[str]) -> list[str]:
    """The last session of each calendar month, from a sorted list of session dates."""
    out = []
    for i, d in enumerate(dates):
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        if nxt is None or nxt[:7] != d[:7]:
            out.append(d)
    return out


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (sxx * syy) ** 0.5


def tercile_spread(rows: list[tuple[float, float, bool]]) -> dict:
    """rows of (gap, forward return, put win). Cheap = lowest-gap third, rich = highest."""
    srt = sorted(rows, key=lambda r: r[0])
    k = len(srt) // 3
    cheap, rich = srt[:k], srt[-k:]
    ret = statistics.mean(r[1] for r in cheap) - statistics.mean(r[1] for r in rich)
    put = statistics.mean(1.0 if r[2] else 0.0 for r in cheap) - statistics.mean(
        1.0 if r[2] else 0.0 for r in rich
    )
    return {
        "names": len(srt),
        "cheap_return": statistics.mean(r[1] for r in cheap),
        "rich_return": statistics.mean(r[1] for r in rich),
        "return_spread": ret,
        "cheap_put_win": statistics.mean(1.0 if r[2] else 0.0 for r in cheap),
        "rich_put_win": statistics.mean(1.0 if r[2] else 0.0 for r in rich),
        "put_spread": put,
        "spearman": spearman([r[0] for r in srt], [r[1] for r in srt]),
        "median_gap": statistics.median(r[0] for r in srt),
    }


def half_verdict(spreads: list[float]) -> dict:
    wins = sum(1 for s in spreads if s > 0)
    losses = sum(1 for s in spreads if s < 0)
    p = stats.sign_test_p(wins, losses, alternative="greater") if wins + losses else None
    med = statistics.median(spreads) if spreads else None
    if len(spreads) < MIN_SESSIONS:
        verdict = "NO_VERDICT"
    elif p is not None and p < ALPHA and med is not None and med > 0:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {
        "sessions": len(spreads),
        "wins": wins,
        "losses": losses,
        "sign_p": p,
        "median_spread": med,
        "verdict": verdict,
    }


def value_name(
    conn, sym: str, sessions: list[str], spy_adj: dict[str, float], snapshots: dict[str, str | None]
):
    """Every session's (gap, forward return, put win) for one name, from one load of its data."""
    bars = store.adjusted_bars(conn, sym)
    if not bars:
        return []
    adj = {b.date: b.close for b in bars}
    raw = {b.date: (b.close / b.price_factor if b.price_factor else b.close) for b in bars}
    dates = [b.date for b in bars]
    idx = {d: i for i, d in enumerate(dates)}
    stmt_rows = [
        tuple(r)
        for r in conn.execute(
            f"SELECT {', '.join(statements.COLUMNS)} FROM statements_annual"
            " WHERE symbol = ? ORDER BY period_end",
            (sym,),
        ).fetchall()
    ]
    if not stmt_rows:
        return []
    est = {
        d: (c, y)
        for d, c, y in conn.execute(
            "SELECT date, consensus, year_ago FROM estimates WHERE symbol = ? AND kind = 'sales'", (sym,)
        )
    }
    out = []
    for s in sessions:
        i = idx.get(s)
        if i is None or i + FORWARD >= len(dates):
            continue
        years = statements.years_from_rows(stmt_rows, s)
        if not years:
            continue
        sr, ir = _tv.aligned_returns({d: adj[d] for d in dates[: i + 1]}, spy_adj, s)
        b = _val.beta(sr, ir) if len(sr) >= _tv.MIN_RETURNS else None
        if b is None:
            continue
        snap = snapshots.get(s)
        g = None
        if snap is not None and snap in est:
            g = fundamentals.growth(est[snap][0], est[snap][1])
        res = _val.value(years, raw[s] * years[-1].shares, b, consensus_growth=g)
        if isinstance(res, _val.Refusal):
            continue
        fwd = adj[dates[i + FORWARD]] / adj[s] - 1.0
        put_ret = adj[dates[i + PUT_HORIZON]] / adj[s] - 1.0
        out.append((s, sym, raw[s] / res.fair_value - 1.0, fwd, put_ret > -PUT_OTM))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default=START)
    ap.add_argument("--sessions", type=int, default=None, help="only the newest N sessions (a dry run)")
    ap.add_argument("--names", type=int, default=None, help="only the first N names (a dry run)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    started = time.monotonic()
    conn = store.connect(paths.history_db())
    conn.executescript(statements.SCHEMA)
    rows_sec, pick = securities.latest()
    kinds = {str(r["symbol"]).replace("/", "."): r.get("type") for r in rows_sec if r.get("symbol")}
    names, source = securities.liquid_optionable()
    sectors = {s: v[0] for s, v in fundamentals.sector_sources().items()}
    names = [n for n in names if kinds.get(n) == "Stock" and sectors.get(n) not in _tv.NOT_DCF_SECTORS]
    if args.names:
        names = names[: args.names]
    spy_bars = store.adjusted_bars(conn, _tv.INDEX)
    spy_adj = {b.date: b.close for b in spy_bars}
    spy_dates = [b.date for b in spy_bars]
    sessions = [
        d for d in month_ends(spy_dates) if d >= args.start and spy_dates.index(d) + FORWARD < len(spy_dates)
    ]
    if args.sessions:
        sessions = sessions[-args.sessions :]
    snapshots = {s: fundamentals.snapshot_on(conn, s) for s in sessions}
    by_session: dict[str, list[tuple[float, float, bool]]] = {s: [] for s in sessions}
    valued_names = 0
    for k, sym in enumerate(names, 1):
        rows = value_name(conn, sym, sessions, spy_adj, snapshots)
        if rows:
            valued_names += 1
        for s, _sym, gap, fwd, put in rows:
            by_session[s].append((gap, fwd, put))
        if k % 50 == 0:
            print(
                f"{k}/{len(names)} names, {round(time.monotonic() - started)} s", file=sys.stderr, flush=True
            )
    conn.close()
    per_session = {s: tercile_spread(r) for s, r in by_session.items() if len(r) >= MIN_NAMES}
    used = sorted(per_session)
    mid = len(used) // 2
    halves = {
        "first": half_verdict([per_session[s]["return_spread"] for s in used[:mid]]),
        "second": half_verdict([per_session[s]["return_spread"] for s in used[mid:]]),
    }
    put_halves = {
        "first": half_verdict([per_session[s]["put_spread"] for s in used[:mid]]),
        "second": half_verdict([per_session[s]["put_spread"] for s in used[mid:]]),
    }
    verdicts = {halves["first"]["verdict"], halves["second"]["verdict"]}
    verdict = "PASS" if verdicts == {"PASS"} else ("NO_VERDICT" if "NO_VERDICT" in verdicts else "FAIL")
    rhos = [per_session[s]["spearman"] for s in used if per_session[s]["spearman"] is not None]
    out = {
        "study": "fair-value-history",
        "declared": "2026-10-28",
        "built_at": datetime.now(UTC).isoformat(),
        "rule": {
            "start": args.start,
            "forward_sessions": FORWARD,
            "put_horizon_sessions": PUT_HORIZON,
            "put_otm": PUT_OTM,
            "min_names": MIN_NAMES,
            "min_sessions": MIN_SESSIONS,
            "alpha": ALPHA,
            "model": {
                "min_wacc": getattr(_val, "MIN_WACC", None),
                "margin_years": getattr(_val, "MARGIN_YEARS", None),
            },
            "universe": source,
            "security_list": pick.get("day"),
            "risk_free": _val.incomerules.RATE,
            "limits": "survivorship (today's universe); constant risk-free; dividends adjusted as of today",
        },
        "names": len(names),
        "names_valued_ever": valued_names,
        "sessions": len(used),
        "sessions_first_half": used[:mid],
        "sessions_second_half": used[mid:],
        "return_spread": halves,
        "put_spread": put_halves,
        "spearman_mean": statistics.mean(rhos) if rhos else None,
        "spearman_negative_share": (sum(1 for r in rhos if r < 0) / len(rhos)) if rhos else None,
        "verdict": verdict,
        "per_session": per_session,
        "seconds": round(time.monotonic() - started),
    }
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = args.out or (paths.study_dir() / f"fair-value-history-{stamp}.json")
    written = write_json_atomic(path, out, indent=1, default=None)
    summary = {
        k: out[k]
        for k in (
            "verdict",
            "sessions",
            "names",
            "names_valued_ever",
            "spearman_mean",
            "spearman_negative_share",
            "seconds",
        )
    }
    summary["return_spread"] = halves
    summary["put_spread"] = put_halves
    summary["file"] = str(written)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
