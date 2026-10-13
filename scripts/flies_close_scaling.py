"""Walk-forward check: does scaling each past session's move-to-close by relative realised vol
predict the SPX close better than the unscaled empirical distribution `flies close-or-hold` uses?

The 2026-10-09 test (recovered from the session scratchpad's `validate_scaling.py`). Read-only: the
gex recorder's history (`gex_spot_history`, `daily_closes`) is opened `mode=ro`.

At each of six decision times, each target session is predicted from every EARLIER session's move
from that time to the close (walk-forward, at least `MIN_TRAIN` of them). Three variants:

- `unscaled` -- the move as recorded, which is what close-or-hold reports;
- `rv_open` -- scaled by today's realised vol since the open over that session's, clipped;
- `rv_60` -- the same over the last 60 minutes (at 10:30 that window IS the session since the open,
  so the two variants coincide there by construction).

Realised vol is the RMS of 5-minute log returns on a grid (last print at or before each grid point,
at most 5 minutes old). A session with a stuck feed (zero RV) or a gappy grid is dropped for every
variant, so the comparison is paired. Scored by CRPS (lower is better), p10-p90 coverage (target 80%)
and the median's absolute error, with a sign test and a Wilcoxon signed-rank test on the paired
CRPS differences, and the mean difference in each half of the era. The decision rule set before the
2026-10-09 run: adopt a variant only if it beats unscaled in both halves at a majority of times
without losing coverage.

**Validation** (`--validate`): the unscaled training set must be the module's own. For the last
target session at each time, the script's {session: move} is compared with
`close_or_hold.session_moves` on the same trail and closes; every session both hold must agree on
the move to 1e-12, and the sessions only the module holds are the ones this test drops for a
stuck or gappy RV grid (counted).

Usage:
    python scripts/flies_close_scaling.py [--clip 0.333,3] [--until 2026-10-08]
    python scripts/flies_close_scaling.py --validate
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from bisect import bisect_right
from datetime import date, datetime

from cherrypick.core import calendar as _cal
from cherrypick.core.clock import ET
from cherrypick.core.regime import default_history_db

MIN_TRAIN = 20
WINDOW = 5 * 60
OPEN = 9 * 3600 + 30 * 60
TIMES = [(10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)]
STEP = 300
VARIANTS = ["unscaled", "rv_open", "rv_60"]


def load(until: str | None):
    con = sqlite3.connect(f"file:{default_history_db()}?mode=ro", uri=True)
    closes = dict(con.execute("SELECT trade_date, close FROM daily_closes WHERE symbol='SPX'").fetchall())
    raw = con.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol='SPX' ORDER BY ts"
    ).fetchall()
    trail: dict = {}
    for td, ts, spot in raw:
        if not spot or spot <= 0 or (until and td > until):
            continue
        t = datetime.fromtimestamp(ts, ET)
        if t.date().isoformat() != td:
            continue
        sod = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6  # as close_or_hold reads it
        if OPEN - WINDOW <= sod <= 16 * 3600:
            trail.setdefault(td, []).append((sod, float(spot)))
    sessions = [
        td
        for td in sorted(trail)
        if td in closes
        and not _cal.is_early_close(date.fromisoformat(td))
        and date.fromisoformat(td).weekday() < 5
    ]
    return closes, trail, sessions, [r for r in raw if not until or r[0] <= until]


def nearest(rows, target):
    """Reading nearest `target` seconds-of-day within WINDOW, else None."""
    best = None
    for sod, spot in rows:
        g = abs(sod - target)
        if g <= WINDOW and (best is None or g < best[0]):
            best = (g, spot)
    return None if best is None else best[1]


def last_at_or_before(rows, sods, target, max_age=STEP):
    i = bisect_right(sods, target) - 1
    if i < 0 or target - sods[i] > max_age:
        return None
    return rows[i][1]


def realised(rows, start, end):
    sods = [r[0] for r in rows]
    grid = list(range(start, end + 1, STEP))
    px = [last_at_or_before(rows, sods, g) for g in grid]
    rets = [math.log(b / a) for a, b in zip(px, px[1:], strict=False) if a and b]
    if len(rets) < max(2, (len(grid) - 1) / 2):
        return None
    return math.sqrt(sum(x * x for x in rets) / len(rets))


def features(closes, trail, sessions) -> dict:
    feat = {}
    for td in sessions:
        rows = trail[td]
        for h, m in TIMES:
            t = h * 3600 + m * 60
            spot = nearest(rows, t)
            if spot is None:
                continue
            rv_o, rv_6 = realised(rows, OPEN, t), realised(rows, t - 3600, t)
            if not rv_o or not rv_6:
                continue
            feat[(td, t)] = {"spot": spot, "r": closes[td] / spot - 1.0, "rv_open": rv_o, "rv_60": rv_6}
    return feat


def crps(xs, y):
    n = len(xs)
    a = sum(abs(x - y) for x in xs) / n
    s = sorted(xs)
    b = 2 * sum((2 * i - n + 1) * v for i, v in enumerate(s)) / (n * n)
    return a - 0.5 * b


def pct(xs, q):
    s = sorted(xs)
    p = (len(s) - 1) * q
    lo, hi = math.floor(p), math.ceil(p)
    return s[lo] + (s[hi] - s[lo]) * (p - lo)


def sign_test(diffs):
    d = [x for x in diffs if x != 0]
    n, k = len(d), sum(1 for x in d if x > 0)
    k = min(k, n - k)
    return min(1.0, sum(math.comb(n, i) for i in range(k + 1)) / 2**n * 2) if n else 1.0


def wilcoxon(diffs):
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n < 6:
        return float("nan")
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    wplus = sum(r for r, x in zip(ranks, d, strict=True) if x > 0)
    mu, sd = n * (n + 1) / 4, math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    return math.erfc(abs((wplus - mu) / sd) / math.sqrt(2))


def run(closes, sessions, feat, clip: tuple[float, float]) -> None:
    def k_of(variant, today, f):
        if variant == "unscaled":
            return 1.0
        return min(clip[1], max(clip[0], today[variant] / f[variant]))

    print(
        f"sessions with a close: {len(sessions)} ({sessions[0]}..{sessions[-1]}), "
        f"clip {clip[0]:.2f}..{clip[1]:.2f}"
    )
    summary = []
    for h, m in TIMES:
        t = h * 3600 + m * 60
        per = {v: [] for v in VARIANTS}
        for idx, td in enumerate(sessions):
            today = feat.get((td, t))
            if today is None:
                continue
            train = [feat[(s, t)] for s in sessions[:idx] if (s, t) in feat]
            if len(train) < MIN_TRAIN:
                continue
            y = closes[td]
            for v in VARIANTS:
                xs = [today["spot"] * (1 + f["r"] * k_of(v, today, f)) for f in train]
                per[v].append((td, crps(xs, y), pct(xs, 0.1) <= y <= pct(xs, 0.9), abs(pct(xs, 0.5) - y)))
        n = len(per["unscaled"])
        if not n:
            print(f"{h:02d}:{m:02d} no targets")
            continue
        base, half = per["unscaled"], n // 2
        for v in VARIANTS:
            rows = per[v]
            line = (
                f"{h:02d}:{m:02d} {v:9s} n={n:3d} CRPS={sum(r[1] for r in rows) / n:6.2f} "
                f"cov={sum(r[2] for r in rows) / n:5.1%} medMAE={sum(r[3] for r in rows) / n:6.2f}"
            )
            if v != "unscaled":
                diffs = [a[1] - b[1] for a, b in zip(rows, base, strict=True)]
                h1, h2 = sum(diffs[:half]) / half, sum(diffs[half:]) / (n - half)
                line += (
                    f"  dCRPS={sum(diffs) / n:+6.2f} wins={sum(d < 0 for d in diffs)}/{n} "
                    f"sign_p={sign_test(diffs):.3f} wilcoxon_p={wilcoxon(diffs):.3f} "
                    f"half1={h1:+6.2f} half2={h2:+6.2f}"
                )
                cov_gap = sum(r[2] for r in rows) / n - sum(r[2] for r in base) / n
                summary.append((v, sum(diffs) / n, h1, h2, cov_gap))
            print(line)
        print(f"      targets {base[0][0]}..{base[-1][0]}; halves split at {base[half][0]}")
    print()
    for v in ("rv_open", "rv_60"):
        s = [x for x in summary if x[0] == v]
        print(
            f"{v}: better mean CRPS at {sum(x[1] < 0 for x in s)}/{len(s)} times; better in both halves at "
            f"{sum(x[2] < 0 and x[3] < 0 for x in s)}/{len(s)}; "
            f"coverage no worse at {sum(x[4] >= 0 for x in s)}/{len(s)}"
        )


def validate(closes, sessions, feat, raw) -> int:
    from cherrypick.flies import close_or_hold

    bad = 0
    for h, m in TIMES:
        t = h * 3600 + m * 60
        target = next((td for td in reversed(sessions) if (td, t) in feat), None)
        if target is None:
            continue
        ours = {s: feat[(s, t)]["r"] for s in sessions if s < target and (s, t) in feat}
        module = close_or_hold.session_moves(
            [tuple(r) for r in raw if r[0] < target],
            {k: v for k, v in closes.items() if k < target},
            today=target,
            clock_min=h * 60 + m,
        )
        theirs = {mv["trade_date"]: mv["r"] for mv in module["moves"]}
        both = set(ours) & set(theirs)
        worst = max((abs(ours[s] - theirs[s]) for s in both), default=0.0)
        only_ours = sorted(set(ours) - set(theirs))
        ok = worst < 1e-12 and not only_ours
        bad += not ok
        print(
            f"{h:02d}:{m:02d} target {target}: {len(both)} sessions agree to {worst:.1e}; module-only "
            f"{len(set(theirs) - set(ours))} (dropped here for a stuck or gappy RV grid); "
            f"script-only {only_ours}" + ("" if ok else "  MISMATCH")
        )
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--clip", default="0.3333,3.0", help="lo,hi bounds on the vol ratio")
    ap.add_argument("--until", help="last session to use (the 10-09 test ran to 2026-10-08)")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args(argv)
    closes, trail, sessions, raw = load(args.until)
    feat = features(closes, trail, sessions)
    if args.validate:
        return validate(closes, sessions, feat, raw)
    lo, hi = (float(x) for x in args.clip.split(","))
    run(closes, sessions, feat, (lo, hi))
    return 0


if __name__ == "__main__":
    sys.exit(main())
