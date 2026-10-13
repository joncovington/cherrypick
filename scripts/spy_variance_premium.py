"""Short-dated S&P variance premium from history: implied at the start of a window against realised.

    python scripts/spy_variance_premium.py --cboe-dir .tmp/cboe
    python scripts/spy_variance_premium.py --cboe-dir .tmp/cboe --json .tmp/vp.json

Read-only: Cboe files (`bwb_history_backtest.py fetch DIR`), the technicals store's SPY bars and
dividends, and point SELECTs against the local Dolt `options` clone.

**The question** `bwb iv-premium` is waiting on (its first windows with an implied reading settle on
2026-10-16): does the variance the short-dated S&P options price exceed what the index then realises?
Two readings, both answering now:

1. **VIX9D against SPX**, 2011 on. VIX9D is Cboe's own model-free 9-day SPX implied vol, full wings,
   which is the closest published thing to bwb's 1-7 day expiry. Realised is SPX close to close over
   the following 9 calendar days, annualised on the same calendar basis (sum r^2 x 365 / 9).
2. **Dolt's SPY strip through `cherrypick.core.impliedvar`**, 2019-02 on: the same arithmetic bwb
   records at entry (`entry_iv_vol`), applied to the nearest expiration at least 7 days out (Dolt
   lists nothing shorter, typically 9 to 17 days). Realised is SPY close to close to that
   expiration, dividends added back on the ex-date, on the same calendar basis as the implied.
   **Every Dolt strip is cut off** (11 to 27 strikes, no wing runs dry), so every implied reading
   here is a lower bound and so is the premium: worst on stress days. Each row also carries the
   same day's 30-day reading against VIX; that shortfall is reported as a rough correction, not
   applied silently.

Windows overlap (a 9-day window starts every session), so the t statistics use every seventh
session only. American exercise and SPY dividends make reading 2 an approximation (the module's own
docstring says so).

**Validation.** Before anything is reported, the 30-day readings on the same dates must reproduce
VIX within `impliedvar_validate.TOLERANCE`; the script exits 2 otherwise.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bwb_history_backtest as _bt  # noqa: E402
import impliedvar_validate as _ivv  # noqa: E402

VIX9D_DAYS = 9
STRESS_VIX = 30.0
T_STRIDE = 7


def realised(closes: dict[str, float], start: str, end: str, divs: dict[str, float] | None = None) -> tuple:
    """(sum of squared log returns, sessions) from start's close to the last close on or before end."""
    keys = sorted(k for k in closes if start <= k <= end)
    if len(keys) < 2 or keys[0] != start:
        return None, 0
    ss = 0.0
    for a, b in zip(keys, keys[1:], strict=False):
        ss += math.log((closes[b] + (divs or {}).get(b, 0.0)) / closes[a]) ** 2
    return ss, len(keys) - 1


def t_stat(values: list[float]) -> float | None:
    if len(values) < 3 or statistics.pstdev(values) == 0:
        return None
    return round(statistics.mean(values) / (statistics.stdev(values) / math.sqrt(len(values))), 2)


def describe(rows: list[dict], imp: str = "implied", real: str = "realised", stride: int = T_STRIDE) -> dict:
    """`stride` thins the rows for the t statistic so the windows it keeps do not overlap: every
    7th session for 9-day windows on a daily series, every 2nd for ~14-day windows sampled weekly."""
    if not rows:
        return {"n": 0}
    prem = [r[imp] - r[real] for r in rows]
    var_prem = [r[imp] ** 2 - r[real] ** 2 for r in rows]
    thinned = [p for i, p in enumerate(prem) if i % stride == 0]
    return {
        "n": len(rows),
        "implied_mean": round(statistics.mean(r[imp] for r in rows), 2),
        "realised_mean": round(statistics.mean(r[real] for r in rows), 2),
        "premium_mean_pts": round(statistics.mean(prem), 2),
        "premium_median_pts": round(statistics.median(prem), 2),
        "variance_premium_mean": round(statistics.mean(var_prem), 1),
        "implied_above_share": round(sum(p > 0 for p in prem) / len(prem), 3),
        "worst_pts": round(min(prem), 1),
        "t_thinned": t_stat(thinned),
        "n_thinned": len(thinned),
    }


def by_year(rows: list[dict], **kw) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r["date"][:4]].append(r)
    return {y: describe(v, **kw) for y, v in sorted(out.items())}


def vix9d_study(cboe_dir: str | None) -> dict:
    v9 = _bt.cboe("VIX9D", cboe_dir)
    vix = _bt.cboe("VIX", cboe_dir)
    spx = _bt.cboe("SPX", cboe_dir)
    keys = sorted(spx)
    rows = []
    for d in sorted(v9):
        if d not in spx:
            continue
        end = (date.fromisoformat(d) + timedelta(days=VIX9D_DAYS)).isoformat()
        if bisect.bisect_right(keys, end) >= len(keys):
            break  # the window has not closed yet
        ss, n = realised(spx, d, end)
        if ss is None:
            continue
        rows.append(
            {
                "date": d,
                "implied": v9[d],
                "realised": 100 * math.sqrt(ss * 365 / VIX9D_DAYS),
                "vix": vix.get(d),
                "sessions": n,
            }
        )
    calm = [r for r in rows if r["vix"] is not None and r["vix"] < STRESS_VIX]
    stress = [r for r in rows if r["vix"] is not None and r["vix"] >= STRESS_VIX]
    # bwb's regime split, at the same close: VIX9D below VIX (contango at the front) vs not
    front_calm = [r for r in rows if r["vix"] and r["implied"] < r["vix"]]
    front_inv = [r for r in rows if r["vix"] and r["implied"] >= r["vix"]]
    return {
        "window": [rows[0]["date"], rows[-1]["date"]],
        "all": describe(rows),
        "vix_below_30": describe(calm),
        "vix_30_plus": describe(stress),
        "vix9d_below_vix": describe(front_calm),
        "vix9d_at_or_above_vix": describe(front_inv),
        "by_year": by_year(rows),
    }


def spy_closes() -> tuple[dict[str, float], dict[str, float]]:
    bars = _bt._bars(_bt.technicals_db("history.db"), "SPY")
    bars.update(_bt._bars(_bt.technicals_db("eod.db"), "SPY"))
    closes = {d: b[3] for d, b in bars.items()}
    divs: dict[str, float] = {}
    for path in (_bt.technicals_db("history.db"), _bt.technicals_db("eod.db")):
        if path.exists():
            with _bt._ro(path) as conn:
                for d, amt in conn.execute("SELECT ex_date, amount FROM dividends WHERE symbol = 'SPY'"):
                    divs[d] = float(amt)
    return closes, divs


def dolt_study(cboe_dir: str | None, start: str, weekday: int, rate: float, host: str, port: int) -> dict:
    vix = _bt.cboe("VIX", cboe_dir)
    v9 = _bt.cboe("VIX9D", cboe_dir)
    closes, divs = spy_closes()
    last = max(closes)
    days = [d for d in _ivv.trading_days(start, last) if d.weekday() == weekday]
    conn = _ivv.connect(host, port)
    rows, validation, refused = [], [], defaultdict(int)
    try:
        cur = conn.cursor()
        for d in days:
            by_exp = _ivv.chain(cur, d)
            if not by_exp:
                continue
            iso = d.isoformat()
            cm = _ivv.thirty_day(by_exp, d, rate) if iso in vix else {"ok": False}
            if cm.get("ok"):
                validation.append(
                    {
                        "date": iso,
                        "ok": True,
                        "iv30": cm["vol"],
                        "vix": vix[iso],
                        "diff": cm["vol"] - vix[iso],
                        "complete": cm["complete"],
                    }
                )
            elif iso in vix:
                validation.append({"date": iso, "ok": False})
            near = min((e for e in by_exp if (e - d).days >= 7), default=None)
            if near is None or near.isoformat() > last:
                refused["no_expiry_or_open_window"] += 1
                continue
            t = _ivv.term(by_exp[near], d, near, rate)
            if not t.get("ok"):
                refused[t.get("reason", "refused")] += 1
                continue
            ss, n = realised(closes, iso, near.isoformat(), divs)
            if ss is None:
                refused["no_realised_path"] += 1
                continue
            years = (near - d).days / 365.0
            row = {
                "date": iso,
                "expiration": near.isoformat(),
                "days": (near - d).days,
                "strikes": t["strikes"],
                "complete": t["complete"],
                "implied": t["vol"],
                "realised": 100 * math.sqrt(ss / years),
                "vix": vix.get(iso),
                "vix9d": v9.get(iso),
            }
            if cm.get("ok"):
                row["shortfall_30d"] = vix[iso] - cm["vol"]
                row["implied_corrected"] = row["implied"] + row["shortfall_30d"]
            rows.append(row)
    finally:
        conn.close()
    summary = _ivv.summarize(validation)
    calm = [r for r in rows if r["vix"] is not None and r["vix"] < STRESS_VIX]
    stress = [r for r in rows if r["vix"] is not None and r["vix"] >= STRESS_VIX]
    corrected = [r for r in rows if "implied_corrected" in r]
    both = [r for r in rows if r["vix9d"]]
    return {
        "validation": summary,
        "window": [rows[0]["date"], rows[-1]["date"]] if rows else None,
        "refused": dict(refused),
        "days_to_expiry": {
            "min": min(r["days"] for r in rows),
            "median": statistics.median(r["days"] for r in rows),
            "max": max(r["days"] for r in rows),
        },
        "complete_share": round(sum(r["complete"] for r in rows) / len(rows), 3),
        "all": describe(rows, stride=2),
        "corrected_by_30d_shortfall": describe(corrected, imp="implied_corrected", stride=2),
        "vix_below_30": describe(calm, stride=2),
        "vix_30_plus": describe(stress, stride=2),
        "strip_vs_vix9d": {
            "n": len(both),
            "corr": round(
                statistics.correlation([r["implied"] for r in both], [r["vix9d"] for r in both]), 4
            ),
            "mean_diff": round(statistics.mean(r["implied"] - r["vix9d"] for r in both), 2),
        },
        "by_year": by_year(rows, stride=2),
    }


def show(name: str, d: dict) -> None:
    if not d.get("n"):
        print(f"  {name:<26} n 0")
        return
    print(
        f"  {name:<26} n {d['n']:>4}: implied {d['implied_mean']:>5.2f}, realised {d['realised_mean']:>5.2f},"
        f" premium mean {d['premium_mean_pts']:+.2f} / median {d['premium_median_pts']:+.2f} pts,"
        f" implied above {d['implied_above_share']:.0%}, worst {d['worst_pts']:+.1f},"
        f" t {d['t_thinned']} (n {d['n_thinned']})"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cboe-dir")
    ap.add_argument("--start", default="2019-02-01")
    ap.add_argument("--weekday", type=int, default=2)
    ap.add_argument("--rate", type=float, default=0.0)
    ap.add_argument("--host", default=_ivv.DOLT["host"])
    ap.add_argument("--port", type=int, default=_ivv.DOLT["port"])
    ap.add_argument("--skip-dolt", action="store_true", help="the VIX9D reading only")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    out = {"vix9d": vix9d_study(args.cboe_dir)}
    if not args.skip_dolt:
        dolt = dolt_study(args.cboe_dir, args.start, args.weekday, args.rate, args.host, args.port)
        _ivv.require(dolt["validation"])
        out["dolt_spy"] = dolt
    v = out["vix9d"]
    print(f"VIX9D vs SPX realised over 9 calendar days, {v['window'][0]}..{v['window'][1]}")
    for k in ("all", "vix_below_30", "vix_30_plus", "vix9d_below_vix", "vix9d_at_or_above_vix"):
        show(k, v[k])
    print("  by year:")
    for y, d in v["by_year"].items():
        show(y, d)
    if "dolt_spy" in out:
        d = out["dolt_spy"]
        s = d["validation"]
        print(
            f"\nDolt SPY strip (core.impliedvar), {d['window'][0]}..{d['window'][1]}; 30-day check vs VIX:"
            f" corr {s['corr']}, MAE {s['mean_abs_diff']}, n {s['computed']}"
        )
        print(
            f"  days to expiry {d['days_to_expiry']}; complete strips {d['complete_share']:.0%};"
            f" refused {d['refused']}"
        )
        print(f"  strip vs VIX9D same day: {d['strip_vs_vix9d']}")
        for k in ("all", "corrected_by_30d_shortfall", "vix_below_30", "vix_30_plus"):
            show(k, d[k])
        print("  by year:")
        for y, dd in d["by_year"].items():
            show(y, dd)
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
