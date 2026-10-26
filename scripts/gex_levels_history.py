"""Do SPX's GEX levels pin, repel or set the day's range? Hypothesis 12, as declared.

    python scripts/gex_levels_history.py [--history-db PATH] [--start D --end D] [--allow-holdout]
        [--json FILE]

Item 12 in docs/edge-hypotheses-plan.md, its pass tests declared 2026-10-18 before any look. The
provisional look (2026-10-19) ran without a committed script; this is that test written down so it
can be re-run unchanged (B14). Read-only. Signal only: no option is priced here.

**Sessions** are `box_touch_history.load_sessions`' (the open, the close, the 1-minute spot path and
the 0DTE straddle's expected move), from the backtest store's SPX file by default. The GEX profile
at a time is the store's latest `gex_profile_history` snapshot at or before it (at most 15 minutes
stale; never a later one, whose gamma is computed at a later spot), built into per-strike exposure
by `core.quotehistory.profile_surface` (`core.gex`, the one engine). Two level types are read:
**gamma** (net exposure by strike; the walls are `compute_gex`'s own rule, the largest call_gex
above spot and the largest put_gex below it) and **open interest** (call and put OI).

**The tests (unchanged from the declaration):**
- *(a) Pin*, at 10:00, 12:00, 14:00: does the close move from spot toward the largest-exposure strike
  above spot, and toward the one below, taken separately? Passes if the Wilson lower bound on the
  hit rate is above 50% in both halves, for both sides, at a time. **As written it cannot pass**:
  toward the strike above is "the close is above spot", toward the one below is "the close is
  below", so the two rates sum to at most 1 (2026-10-25, recorded in the plan). Its fail says
  nothing about pinning.
- *(b) Barrier*, at 10:00: touches of the largest call wall above spot and put wall below, against
  the touches expected at the same distance in straddles from the same half's days. Passes if
  observed / expected is <= 0.85 with the bootstrap upper bound below 1, for both walls of one level
  type, in both halves.
- *(c) Regime*, at 10:00: the range from 10:00 to the close in straddles, on negative against positive
  net GEX days. Passes if the bootstrap interval of the difference (negative - positive) is above zero
  in both halves.

**Halves.** The declared H1 2022-05-16..2023-12-31 and H2 2024-01-01..2025-06-30. Given `--start`/
`--end` (a yearly re-test), the window's own first and second halves by session count.

**The holdout** (2025-07-01..2026-10-12) is never read unless `--allow-holdout` says so: a re-test
of a closed item may read it, a provisional look may not. A window with an open `--end` reads to the
store's last session, so it counts as reaching the holdout unless its `--start` is after it.

**A result is licensed vendor data**: with the default store it is written only under the backtest
data dir, and never into the repo, a PR or an artifact.
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import box_touch_history as _box  # noqa: E402
from cherrypick.core import home as _home  # noqa: E402
from cherrypick.core.quotehistory import profile_surface  # noqa: E402

ET = ZoneInfo("America/New_York")
H1 = ("2022-05-16", "2023-12-31")
H2 = ("2024-01-01", "2025-06-30")
HOLDOUT = ("2025-07-01", "2026-10-12")
PIN_TIMES = (600, 720, 840)  # 10:00, 12:00, 14:00 ET, minutes of the day
BARRIER_TIME = 600
SNAPSHOT_WINDOW = 15
RESAMPLES = 2000
SEED = 20261018
LEVELS = ("gamma", "oi")


# --------------------------------------------------------------------------- statistics
def wilson_lower(k: int, n: int, z: float = 1.959964) -> float | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5
    return (centre - margin) / d


def bootstrap(
    days: list, stat, *, resamples: int = RESAMPLES, seed: int = SEED
) -> tuple[float, float] | None:
    """Day-level percentile interval of `stat(days)`."""
    if len(days) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        v = stat([days[rng.randrange(len(days))] for _ in days])
        if v is not None:
            draws.append(v)
    if not draws:
        return None
    draws.sort()
    return draws[int(0.025 * len(draws))], draws[min(len(draws) - 1, int(0.975 * len(draws)))]


# --------------------------------------------------------------------------- levels
def levels(rows: list[dict], spot: float) -> dict:
    """The strikes each test reads, by level type: the largest-exposure strike above and below spot
    (pin) and the largest call wall above and put wall below (barrier), plus net GEX (regime)."""
    out = {"net_gex": None}
    surf = profile_surface(rows, spot)
    if surf.get("ok"):
        ps = surf["per_strike"]
        out["net_gex"] = surf["net_gex"]
        above = [s for s in ps if s["strike"] > spot]
        below = [s for s in ps if s["strike"] < spot]
        out["gamma"] = {
            "pin_above": max(above, key=lambda s: abs(s["net_gex"]), default={}).get("strike"),
            "pin_below": max(below, key=lambda s: abs(s["net_gex"]), default={}).get("strike"),
            # The walls are core's own (`compute_gex`: the largest call_gex, the largest put_gex;
            # its put_gex is positive), read over the strikes on the wall's side of spot only. A
            # strike's exposure is its own (`strike_gamma`), so splitting the rows changes no value.
            "call_wall": _side_wall([r for r in rows if float(r["strike"]) > spot], spot, "call_wall"),
            "put_wall": _side_wall([r for r in rows if float(r["strike"]) < spot], spot, "put_wall"),
        }
    oi_above = [r for r in rows if float(r["strike"]) > spot]
    oi_below = [r for r in rows if float(r["strike"]) < spot]

    def oi(r, side):
        return float(r.get(f"{side}_oi") or 0)

    out["oi"] = {
        "pin_above": _strike(max(oi_above, key=lambda r: oi(r, "call") + oi(r, "put"), default=None)),
        "pin_below": _strike(max(oi_below, key=lambda r: oi(r, "call") + oi(r, "put"), default=None)),
        "call_wall": _strike(max(oi_above, key=lambda r: oi(r, "call"), default=None)),
        "put_wall": _strike(max(oi_below, key=lambda r: oi(r, "put"), default=None)),
    }
    return out


def _side_wall(rows: list[dict], spot: float, wall: str) -> float | None:
    """`core.gex.compute_gex`'s `wall` ("call_wall" / "put_wall") over these rows, or None."""
    surf = profile_surface(rows, spot) if rows else {}
    return surf.get(wall) if surf.get("ok") else None


def _strike(r) -> float | None:
    return None if r is None else float(r["strike"])


def spot_at(path: list[tuple[int, float]], minute: int) -> tuple[int, float] | None:
    i = bisect.bisect_left([m for m, _ in path], minute)
    return (i, path[i][1]) if i < len(path) and path[i][0] - minute <= SNAPSHOT_WINDOW else None


def snapshot_at(profiles: list[tuple[int, list[dict]]], minute: int) -> list[dict] | None:
    """The latest profile at or before `minute`, no more than SNAPSHOT_WINDOW minutes stale. Never
    a later one: its gamma is computed at a later spot, which is the future at the decision."""
    best = None
    for m, rows in profiles:
        if minute - SNAPSHOT_WINDOW <= m <= minute:
            best = rows
    return best


def load_profiles(db: Path, symbol: str, day: str) -> list[tuple[int, list[dict]]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        grouped: dict[float, list[dict]] = {}
        for r in conn.execute(
            "SELECT ts, strike, call_oi, put_oi, call_gamma, put_gamma FROM gex_profile_history "
            "WHERE symbol = ? AND trade_date = ? ORDER BY ts",
            (symbol.upper(), day),
        ):
            grouped.setdefault(float(r["ts"]), []).append(dict(r))
    finally:
        conn.close()
    out = []
    for ts, rows in sorted(grouped.items()):
        t = datetime.fromtimestamp(ts, ET)
        out.append((t.hour * 60 + t.minute, rows))
    return out


# --------------------------------------------------------------------------- one session
def day_record(s: dict, profiles: list[tuple[int, list[dict]]]) -> dict | None:
    """Everything the three tests read from one session, or None when it cannot be measured."""
    path, close, em = s["path"], s["close"], s["em"]
    if not path or not em:
        return None
    rec = {"date": str(s["date"])[:10], "pin": {}, "barrier": None, "regime": None}
    for minute in PIN_TIMES:
        at, rows = spot_at(path, minute), snapshot_at(profiles, minute)
        if at is None or rows is None:
            continue
        spot = at[1]
        lv = levels(rows, spot)
        for lt in LEVELS:
            for side in ("above", "below"):
                k = (lv.get(lt) or {}).get(f"pin_{side}")
                if k is not None and close != spot:
                    # As declared. The strike above is always above spot and the one below always
                    # below, so these two flags are complements whenever both are set: the "above"
                    # and "below" rates sum to at most 1, and the declared pass (both above 50%)
                    # cannot fire. Recorded in the plan; kept unchanged, since a redefined pin test
                    # is a new hypothesis with its own declaration.
                    rec["pin"][f"{minute}:{lt}:{side}"] = (close - spot) * (k - spot) > 0
    at, rows = spot_at(path, BARRIER_TIME), snapshot_at(profiles, BARRIER_TIME)
    if at is None or rows is None:
        return rec
    i, spot = at
    rest = [p for _, p in path[i:]]
    hi, lo = max(rest), min(rest)
    lv = levels(rows, spot)
    rec["up_reach"] = (hi - spot) / em
    rec["down_reach"] = (spot - lo) / em
    rec["barrier"] = {}
    for lt in LEVELS:
        cw, pw = (lv.get(lt) or {}).get("call_wall"), (lv.get(lt) or {}).get("put_wall")
        if cw is not None:
            rec["barrier"][f"{lt}:call"] = {"dist": (cw - spot) / em, "touched": hi >= cw}
        if pw is not None:
            rec["barrier"][f"{lt}:put"] = {"dist": (spot - pw) / em, "touched": lo <= pw}
    if lv.get("net_gex") is not None:
        rec["regime"] = {"negative": lv["net_gex"] < 0, "range": (hi - lo) / em}
    return rec


# --------------------------------------------------------------------------- the tests
def pin_test(half: list[dict]) -> dict:
    out = {}
    keys = sorted({k for d in half for k in d["pin"]})
    for k in keys:
        hits = [d["pin"][k] for d in half if k in d["pin"]]
        out[k] = {
            "n": len(hits),
            "rate": sum(hits) / len(hits) if hits else None,
            "lower": wilson_lower(sum(hits), len(hits)),
        }
    return out


def barrier_test(half: list[dict]) -> dict:
    out = {}
    measured = [d for d in half if d.get("barrier") is not None]
    for key in sorted({k for d in measured for k in d["barrier"]}):
        reach = "up_reach" if key.endswith(":call") else "down_reach"
        pool = [d[reach] for d in measured]
        cases = [(d, d["barrier"][key]) for d in measured if key in d["barrier"]]

        def ratio(sample, pool=pool):
            obs = sum(1 for _, b in sample if b["touched"])
            exp = sum(sum(1 for r in pool if r >= b["dist"]) / len(pool) for _, b in sample)
            return obs / exp if exp else None

        out[key] = {"n": len(cases), "ratio": ratio(cases), "ci": bootstrap(cases, ratio)}
    return out


def regime_test(half: list[dict]) -> dict:
    days = [d["regime"] for d in half if d.get("regime")]

    def diff(sample):
        neg = [r["range"] for r in sample if r["negative"]]
        pos = [r["range"] for r in sample if not r["negative"]]
        return (sum(neg) / len(neg) - sum(pos) / len(pos)) if neg and pos else None

    return {
        "n_negative": sum(1 for r in days if r["negative"]),
        "n_positive": sum(1 for r in days if not r["negative"]),
        "diff": diff(days),
        "ci": bootstrap(days, diff),
    }


def verdict(halves: dict[str, dict]) -> dict:
    hs = list(halves.values())
    pin_pass = {}
    for minute in PIN_TIMES:
        for lt in LEVELS:
            ok = all(
                ((h["pin"].get(f"{minute}:{lt}:{side}") or {}).get("lower") or 0) > 0.5
                for h in hs
                for side in ("above", "below")
            )
            pin_pass[f"{minute}:{lt}"] = ok
    barrier_pass = {}
    for lt in LEVELS:

        def wall_ok(h, w, lt=lt):
            b = h["barrier"].get(f"{lt}:{w}") or {}
            return (
                b.get("ratio") is not None
                and b["ratio"] <= 0.85
                and b.get("ci") is not None
                and b["ci"][1] < 1
            )

        barrier_pass[lt] = all(wall_ok(h, "call") and wall_ok(h, "put") for h in hs)
    regime_pass = all(h["regime"].get("ci") is not None and h["regime"]["ci"][0] > 0 for h in hs)
    return {
        "pin": {"pass": any(pin_pass.values()), "checks": pin_pass},
        "barrier": {"pass": any(barrier_pass.values()), "checks": barrier_pass},
        "regime": {"pass": regime_pass},
    }


def split_halves(days: list[dict], start: str | None, end: str | None) -> dict[str, list[dict]]:
    if start is None and end is None:
        return {
            "H1": [d for d in days if H1[0] <= d["date"] <= H1[1]],
            "H2": [d for d in days if H2[0] <= d["date"] <= H2[1]],
        }
    days = sorted(days, key=lambda d: d["date"])
    mid = len(days) // 2
    return {"first": days[:mid], "second": days[mid:]}


def touches_holdout(start: str | None, end: str | None) -> bool:
    """Whether the sessions `main` would read reach the holdout. With neither bound it reads the
    declared halves, which end before it. With a window, an open bound is open: `in_window` keeps
    every stored session past it, so an open end reaches the store's last session (a store holding
    the holdout reads it) and an open start its first."""
    if start is None and end is None:
        return False
    lo, hi = start or "", end or "9999-12-31"
    return lo <= HOLDOUT[1] and hi >= HOLDOUT[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--history-db", type=Path, default=_box.DEFAULT_DB)
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument(
        "--allow-holdout", action="store_true", help="a re-test of a closed item may read the holdout"
    )
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    if touches_holdout(args.start, args.end) and not args.allow_holdout:
        print(
            f"refused: the window touches the holdout {HOLDOUT[0]}..{HOLDOUT[1]} (an open --end reads "
            "to the store's last session); pass --allow-holdout",
            file=sys.stderr,
        )
        return 2
    vendor = "backtest" in args.history_db.as_posix()
    if vendor and args.json and not args.json.resolve().is_relative_to(_home.data_dir("backtest").resolve()):
        print(
            "refused: a result from the licensed store is written only under the backtest data dir",
            file=sys.stderr,
        )
        return 2
    sessions, drops = _box.load_sessions(args.history_db, args.symbol, 0.85)
    sessions = _box.in_window(sessions, args.start, args.end)
    if args.start is None and args.end is None:
        sessions = [s for s in sessions if str(s["date"])[:10] <= H2[1]]
    days, unmeasured = [], 0
    for s in sessions:
        rec = day_record(s, load_profiles(args.history_db, args.symbol, str(s["date"])[:10]))
        if rec is None:
            unmeasured += 1
        else:
            days.append(rec)
    halves = {name: {"n": len(h), "pin": pin_test(h), "barrier": barrier_test(h), "regime": regime_test(h)}
              for name, h in split_halves(days, args.start, args.end).items()}  # fmt: skip
    rep = {
        "args": {
            "start": args.start,
            "end": args.end,
            "allow_holdout": args.allow_holdout,
            "history_db": str(args.history_db),
        },
        "sessions": len(days),
        "unmeasured": unmeasured,
        "drops": drops,
        "halves": halves,
        "verdict": verdict(halves),
    }
    print(json.dumps(rep["verdict"]))
    print(f"{len(days)} sessions measured, {unmeasured} not", file=sys.stderr)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
