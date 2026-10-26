"""Does SPX revert after touching the edge of the day's expected-move box? And two intraday direction reads.

    python scripts/box_touch_history.py [--history-db PATH] [--em-factor 0.85] [--start D] [--end D]
        [--allow-holdout] [--json FILE]

Step 2 of hypothesis 8 in docs/edge-hypotheses-plan.md (docs/pulver-daily-profit-play.md), with the
direction tests of hypothesis 4 alongside, because they read the same sessions. Read-only.

**Why this question.** The reconstructed Daily Profit Play is a condor order resting far above the
market: it fills only when price runs toward the edge of a gap-adjusted expected-move box. Its edge,
if any, is that price closes back inside after such a touch more often than chance. Under a
martingale a price sitting at a level has about an even chance of closing on either side of it, so
"back inside after a touch" should run near 50% unless the box edge is special. A placebo makes that
concrete: the same statistic at levels inside the box (open +/- 0.5 and 0.75 expected moves), which
nobody trades against.

**Sessions** come from any history db in the recorder's schema (`gex_spot_history`,
`gex_quote_samples` / `gex_quotes`, `daily_closes`): by default the backtest store's 1-minute SPX
file. **A result computed from that file is licensed vendor data: it is written only under
`~/.cherrypick/data/backtest/results/` and never into the repo, a PR or an artifact.** The
recorder's own `gex_history.db` works too (`--history-db`), and its results are ours.

**The holdout** (the backtest settings' `windows.holdout`) is never read unless `--allow-holdout`
says so: a re-test of a closed item may read it, a provisional look may not. The check is on the
sessions the run would actually read (every stored session up to `--end`, else the store's last,
from the one before `--start`), not on the arguments, so an open end cannot slip past it.

**Per session:** the open is the first spot at or after 09:30 ET; the close is `daily_closes` for the
day, else the last spot; the prior close is the previous session's. The expected move is the suite's
`core.structures.expected_move` on the 0DTE ATM straddle mid at the first quote sample at or after the
open (within 10 minutes), the strike nearest spot quoted on both sides. Sessions without one are
counted and skipped.

**Boxes.** `widest` (his indicator's "merged widest"): upper = max(open, prior close) + EM, lower =
min(open, prior close) - EM, so the gap side is measured from the open and the other side from the
prior close. `open`: open +/- EM.

**Touch statistics,** per box: the share of sessions closing inside (the containment rate he quotes);
the share touching an edge before `--cutoff` (15:00 ET); after the first such touch, whether the close
is back inside; the overshoot beyond the edge, in expected moves. Placebo levels get the same "back on
the open side after first touch" rate.

**Direction reads (hypothesis 4).**
- `fade`: after the first touch of a `widest` edge before 13:00 ET, did price move back toward the
  inside over the next 30, 60 and 120 minutes? The fade video's structure (risk 1x the credit to make
  0.5x) needs about 67% to break even.
- `trend`: at 12:30, 13:00, 13:30, 14:00, 14:30 and 14:45 ET, the 1-minute 20-period EMA against the
  40-period. Did the close land on the EMA's side of the price at that minute? A credit spread sold on
  that side wins when it does, so above 50% is the bar for a signal.

**Statistics.** Rates carry a 95% interval from resampling sessions (2,000 draws, fixed seed), because
a day's touches and minutes are not independent. Years are shown separately.

**Declared pass tests (2026-10-17, before any run).**
- *Box reversion:* on the `widest` box with the 0.85 factor, P(back inside | first touch before 15:00)
  must exceed the placebo rate (the two interior levels pooled) by at least 10 points, with the
  resampled 95% interval of the difference above zero, and the difference positive in at least
  two-thirds of the calendar years holding 20 or more touches. Pass means building the resting-order
  fill model (step 3); fail means hypothesis 8 stops.
- *Fade:* the 60-minute fade hit rate's 95% interval must lie above 50%, and its point estimate must
  reach 67%, to justify pricing the fade.
- *Trend:* the pooled EMA-side hit rate's 95% interval must lie above 50%.

Deterministic: the same file and arguments give the same tables.
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core.structures import expected_move

ET = ZoneInfo("America/New_York")
PUT, CALL = 0, 1
PLACEBO_K = (0.5, 0.75)
FADE_WINDOWS = (30, 60, 120)
TREND_TIMES = ("12:30", "13:00", "13:30", "14:00", "14:30", "14:45")
BOOT = 2000
SEED = 20261017
DEFAULT_DB = Path.home() / ".cherrypick" / "data" / "backtest" / "vendor" / "thetadata" / "SPX-60s.db"


# --------------------------------------------------------------------------- pure pieces


def minute_of(ts: float) -> int:
    """Minutes after midnight, Eastern, for an epoch timestamp."""
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def boxes(opn: float, prior: float, em: float) -> dict[str, tuple[float, float]]:
    return {
        "widest": (min(opn, prior) - em, max(opn, prior) + em),
        "open": (opn - em, opn + em),
    }


def first_cross(path: list[tuple[int, float]], level: float, up: bool, before: int) -> int | None:
    """Index of the first sample at or beyond `level` (above when `up`), at a minute before `before`."""
    for i, (m, s) in enumerate(path):
        if m >= before:
            return None
        if (s >= level) if up else (s <= level):
            return i
    return None


def touch_record(path: list[tuple[int, float]], close: float, lo: float, hi: float, em: float, cutoff: int):
    """The first edge touched before `cutoff`: which side, when, the overshoot (EM units, at the most
    extreme beyond that edge for the rest of the day) and whether the close is back inside."""
    iu = first_cross(path, hi, True, cutoff)
    il = first_cross(path, lo, False, cutoff)
    if iu is None and il is None:
        return None
    up = il is None or (iu is not None and iu < il)
    i = iu if up else il
    rest = [s for _, s in path[i:]]
    over = (max(rest) - hi) / em if up else (lo - min(rest)) / em
    return {
        "side": "up" if up else "down",
        "minute": path[i][0],
        "index": i,
        "overshoot": over,
        "back_inside": close < hi if up else close > lo,
    }


def placebo_record(path, close: float, opn: float, em: float, k: float, cutoff: int) -> list[bool]:
    """For the interior levels open +/- k x EM: after each one's first touch before `cutoff`, is the
    close back on the open side of it?"""
    out = []
    for up in (True, False):
        level = opn + k * em if up else opn - k * em
        i = first_cross(path, level, up, cutoff)
        if i is not None:
            out.append(close < level if up else close > level)
    return out


def fade_record(path, touch: dict, lo: float, hi: float, windows=FADE_WINDOWS) -> dict[int, bool | None]:
    """After a touch, did price sit back toward the inside `w` minutes later?"""
    i, m0 = touch["index"], touch["minute"]
    level = hi if touch["side"] == "up" else lo
    out = {}
    for w in windows:
        j = bisect.bisect_left([m for m, _ in path], m0 + w, lo=i)
        if j >= len(path):
            out[w] = None
            continue
        s = path[j][1]
        out[w] = s < level if touch["side"] == "up" else s > level
    return out


def ema(xs: list[float], n: int) -> list[float]:
    a = 2.0 / (n + 1)
    out, e = [], None
    for x in xs:
        e = x if e is None else a * x + (1 - a) * e
        out.append(e)
    return out


def trend_records(path, close: float, times=TREND_TIMES) -> list[bool]:
    """At each time, did the close land on the 20-vs-40 EMA side of that minute's price?"""
    mins = [m for m, _ in path]
    spots = [s for _, s in path]
    e20, e40 = ema(spots, 20), ema(spots, 40)
    out = []
    for t in times:
        j = bisect.bisect_left(mins, hhmm(t))
        if j >= len(path) or j < 40 or mins[j] - hhmm(t) > 2:
            continue
        if e20[j] == e40[j] or close == spots[j]:
            continue
        out.append((close > spots[j]) == (e20[j] > e40[j]))
    return out


def boot_rate(groups: list[list[bool]], seed: int = SEED, n: int = BOOT) -> dict:
    """A rate over per-session lists of outcomes, with a 95% interval from resampling sessions."""
    flat = [x for g in groups for x in g]
    if not flat:
        return {"n": 0}
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        pick = [groups[rng.randrange(len(groups))] for _ in groups]
        xs = [x for g in pick for x in g]
        if xs:
            stats.append(sum(xs) / len(xs))
    stats.sort()
    return {
        "n": len(flat),
        "sessions": sum(1 for g in groups if g),
        "rate": sum(flat) / len(flat),
        "lo": stats[int(0.025 * len(stats))],
        "hi": stats[int(0.975 * len(stats)) - 1],
    }


def boot_diff(a: list[list[bool]], b: list[list[bool]], seed: int = SEED, n: int = BOOT) -> dict:
    """rate(a) - rate(b) over the same sessions (lists aligned by session), resampling sessions."""

    def rate(gs):
        xs = [x for g in gs for x in g]
        return sum(xs) / len(xs) if xs else None

    base_a, base_b = rate(a), rate(b)
    if base_a is None or base_b is None:
        return {"diff": None}
    rng = random.Random(seed)
    idx = range(len(a))
    stats = []
    for _ in range(n):
        pick = [rng.choice(idx) for _ in idx]
        ra, rb = rate([a[i] for i in pick]), rate([b[i] for i in pick])
        if ra is not None and rb is not None:
            stats.append(ra - rb)
    stats.sort()
    return {
        "diff": base_a - base_b,
        "lo": stats[int(0.025 * len(stats))],
        "hi": stats[int(0.975 * len(stats)) - 1],
    }


# --------------------------------------------------------------------------- data


def days_to_read(stored: list[str], start: str | None, end: str | None) -> list[str]:
    """The stored sessions a run over [start, end] reads: every one up to `end` (else the store's
    last), from the one before `start` (its close is the first session's prior close)."""
    days = [d for d in stored if end is None or str(d)[:10] <= end]
    if start is not None:
        first = bisect.bisect_left([str(d)[:10] for d in days], start)
        days = days[max(first - 1, 0) :]
    return days


def session_days(path: Path, symbol: str, start: str | None = None, end: str | None = None) -> list[str]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    stored = [
        d
        for (d,) in conn.execute(
            "SELECT DISTINCT trade_date FROM gex_spot_history WHERE symbol = ? ORDER BY trade_date", (symbol,)
        )
    ]
    conn.close()
    return days_to_read(stored, start, end)


def holdout_window() -> tuple[str, str]:
    """The backtest settings' holdout, [start, end] (imported here: a script, not core, may)."""
    from cherrypick.backtest import settings

    lo, hi = settings.load()["windows"]["holdout"]
    return lo, hi


def holdout_days(days: list[str], holdout: tuple[str, str]) -> list[str]:
    """The sessions of `days` inside the holdout."""
    return [d for d in days if holdout[0] <= str(d)[:10] <= holdout[1]]


def load_sessions(
    path: Path, symbol: str, em_factor: float, start: str | None = None, end: str | None = None
) -> tuple[list[dict], dict]:
    """The sessions dated in [start, end] (either open), and the drops among them. Sessions after `end`
    are never read; the one before `start` is read for its close only, so it is never counted."""
    days = session_days(path, symbol, start, end)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    closes = dict(
        conn.execute("SELECT trade_date, close FROM daily_closes WHERE symbol = ? AND close > 0", (symbol,))
    )
    out, drops, prev_close = [], defaultdict(int), None
    for d in days:
        rows = conn.execute(
            "SELECT ts, spot FROM gex_spot_history "
            "WHERE symbol = ? AND trade_date = ? AND spot > 0 ORDER BY ts",
            (symbol, d),
        ).fetchall()
        path = [(minute_of(ts), s) for ts, s in rows]
        path = [(m, s) for m, s in path if 9 * 60 + 30 <= m < 16 * 60]
        if start is not None and str(d)[:10] < start:  # the session before the window: its close only
            prev_close = closes.get(d, path[-1][1] if path else prev_close)
            continue
        if len(path) < 60:
            drops["short_path"] += 1
            prev_close = closes.get(d, path[-1][1] if path else prev_close)
            continue
        close = closes.get(d, path[-1][1])
        if prev_close is None:
            prev_close = close
            drops["no_prior_close"] += 1
            continue
        opn = path[0][1]
        em = open_em(conn, symbol, d, rows[0][0], opn, em_factor)
        if em is None or em <= 0:
            drops["no_em"] += 1
        else:
            out.append({"date": d, "open": opn, "prior": prev_close, "close": close, "em": em, "path": path})
        prev_close = close
    conn.close()
    return out, dict(drops)


def open_em(conn, symbol: str, d: str, open_ts: float, spot: float, factor: float) -> float | None:
    """0DTE ATM straddle at the first quote sample within 10 minutes of the open."""
    row = conn.execute(
        "SELECT id FROM gex_quote_samples WHERE symbol = ? AND trade_date = ? AND expiration = ? "
        "AND ts >= ? AND ts <= ? ORDER BY ts LIMIT 1",
        (symbol, d, d, open_ts - 1, open_ts + 600),
    ).fetchone()
    if row is None:
        return None
    q = conn.execute(
        "SELECT side, strike, mid FROM gex_quotes WHERE sample_id = ? AND mid > 0", (row[0],)
    ).fetchall()
    by: dict[float, dict] = defaultdict(dict)
    for side, k, m in q:
        by[k][side] = m
    both = [k for k, v in by.items() if PUT in v and CALL in v]
    if not both:
        return None
    k = min(both, key=lambda x: abs(x - spot))
    return expected_move(by[k][CALL], by[k][PUT], factor=factor)


# --------------------------------------------------------------------------- analysis


def analyse(sessions: list[dict], cutoff: int) -> dict:
    rep: dict = {"sessions": len(sessions)}
    for name in ("widest", "open"):
        inside, back, plac, over, by_year_b, by_year_p = [], [], [], [], defaultdict(list), defaultdict(list)
        fades = {w: [] for w in FADE_WINDOWS}
        for s in sessions:
            lo, hi = boxes(s["open"], s["prior"], s["em"])[name]
            inside.append([lo < s["close"] < hi])
            t = touch_record(s["path"], s["close"], lo, hi, s["em"], cutoff)
            back.append([t["back_inside"]] if t else [])
            p = [
                x
                for k in PLACEBO_K
                for x in placebo_record(s["path"], s["close"], s["open"], s["em"], k, cutoff)
            ]
            plac.append(p)
            y = s["date"][:4]
            by_year_b[y].append(back[-1])
            by_year_p[y].append(p)
            if t:
                over.append(t["overshoot"])
            if name == "widest":
                ft = touch_record(s["path"], s["close"], lo, hi, s["em"], hhmm("13:00"))
                fr = fade_record(s["path"], ft, lo, hi) if ft else {}
                for w in FADE_WINDOWS:
                    fades[w].append([fr[w]] if fr.get(w) is not None else [])
        box = {
            "inside_close": boot_rate(inside),
            "back_inside_after_touch": boot_rate(back),
            "placebo": boot_rate(plac),
            "edge_minus_placebo": boot_diff(back, plac),
            "overshoot_median_em": statistics.median(over) if over else None,
            "by_year": {
                y: {
                    "touches": sum(len(g) for g in by_year_b[y]),
                    "back_inside": boot_rate(by_year_b[y])["rate"] if any(by_year_b[y]) else None,
                    "placebo": boot_rate(by_year_p[y])["rate"] if any(by_year_p[y]) else None,
                }
                for y in sorted(by_year_b)
            },
        }
        if name == "widest":
            box["fade"] = {w: boot_rate(fades[w]) for w in FADE_WINDOWS}
        rep[name] = box
    rep["trend"] = boot_rate([trend_records(s["path"], s["close"]) for s in sessions])
    rep["verdict"] = verdict(rep)
    return rep


def verdict(rep: dict) -> dict:
    w = rep["widest"]
    d = w["edge_minus_placebo"]
    years = [v for v in w["by_year"].values() if v["touches"] >= 20 and v["placebo"] is not None]
    pos = sum(1 for v in years if v["back_inside"] - v["placebo"] > 0)
    box_checks = {
        "diff_at_least_10pts": d.get("diff") is not None and d["diff"] >= 0.10,
        "diff_interval_above_zero": d.get("lo") is not None and d["lo"] > 0,
        "two_thirds_of_years": bool(years) and pos * 3 >= 2 * len(years),
    }
    f60 = w["fade"][60]
    t = rep["trend"]
    return {
        "box": {"checks": box_checks, "pass": all(box_checks.values())},
        "fade": {"pass": f60.get("n", 0) > 0 and f60["lo"] > 0.5 and f60["rate"] >= 0.67},
        "trend": {"pass": t.get("n", 0) > 0 and t["lo"] > 0.5},
    }


def _r(s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return "n/a"
    return f"{s['rate'] * 100:5.1f}% [{s['lo'] * 100:4.1f}, {s['hi'] * 100:4.1f}]  n={s['n']}"


def print_report(rep: dict) -> None:
    print(f"\nsessions: {rep['sessions']}")
    for name in ("widest", "open"):
        b = rep[name]
        d = b["edge_minus_placebo"]
        print(f"\n[{name} box]")
        print(f"  close inside the box         {_r(b['inside_close'])}")
        print(f"  back inside after a touch    {_r(b['back_inside_after_touch'])}")
        print(f"  placebo (interior levels)    {_r(b['placebo'])}")
        if d.get("diff") is not None:
            band = f"[{d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}]"
            print(f"  edge minus placebo           {d['diff'] * 100:+5.1f} pts {band}")
        print(
            f"  median overshoot             {b['overshoot_median_em']:.2f} EM"
            if b["overshoot_median_em"]
            else ""
        )
        for y, v in b["by_year"].items():
            bi = "  -  " if v["back_inside"] is None else f"{v['back_inside'] * 100:5.1f}%"
            pl = "  -  " if v["placebo"] is None else f"{v['placebo'] * 100:5.1f}%"
            print(f"    {y}  touches {v['touches']:>4}  back inside {bi}  placebo {pl}")
        if "fade" in b:
            for w, s in b["fade"].items():
                print(f"  fade, {w:>3} min after touch     {_r(s)}")
    print(f"\n[trend] EMA-side close           {_r(rep['trend'])}")
    print("\nDeclared pass tests:", json.dumps({k: v["pass"] for k, v in rep["verdict"].items()}))
    print("box checks:", json.dumps(rep["verdict"]["box"]["checks"]))


def in_window(sessions: list[dict], start: str | None, end: str | None) -> list[dict]:
    """The sessions dated within [start, end] (either open)."""
    return [
        x
        for x in sessions
        if (start is None or str(x["date"])[:10] >= start) and (end is None or str(x["date"])[:10] <= end)
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--history-db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--em-factor", type=float, default=0.85)
    ap.add_argument("--cutoff", default="15:00")
    # A window over the sessions, each keeping its true prior close (the session before the window is
    # read for its close): a yearly re-test (B14) re-runs the declared test on one year.
    ap.add_argument("--start", default=None, help="first session to keep, YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="last session to keep, YYYY-MM-DD")
    ap.add_argument(
        "--allow-holdout", action="store_true", help="a re-test of a closed item may read the holdout"
    )
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    vendor = "backtest" in args.history_db.as_posix()
    if vendor and args.json and not args.json.resolve().is_relative_to(_home.data_dir("backtest").resolve()):
        print(
            "refused: a result from the licensed store is written only under the backtest data dir",
            file=sys.stderr,
        )
        return 2
    if not args.allow_holdout:
        holdout = holdout_window()
        seen = holdout_days(session_days(args.history_db, args.symbol, args.start, args.end), holdout)
        if seen:
            print(
                f"refused: the run would read {len(seen)} session(s) of the holdout "
                f"{holdout[0]}..{holdout[1]} ({seen[0]}..{seen[-1]}); pass --allow-holdout",
                file=sys.stderr,
            )
            return 2
    sessions, drops = load_sessions(args.history_db, args.symbol, args.em_factor, args.start, args.end)
    sessions = in_window(sessions, args.start, args.end)
    print(f"loaded {len(sessions)} sessions; dropped {drops}", file=sys.stderr)
    rep = analyse(sessions, hhmm(args.cutoff))
    rep["drops"] = drops
    rep["args"] = {
        "history_db": str(args.history_db),
        "em_factor": args.em_factor,
        "cutoff": args.cutoff,
        "start": args.start,
        "end": args.end,
        "allow_holdout": args.allow_holdout,
    }
    print_report(rep)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
