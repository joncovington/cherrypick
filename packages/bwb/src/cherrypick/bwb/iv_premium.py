"""The volatility each position sold: implied at entry against realised to expiry (2026-10-09).

READ-SIDE ONLY, over the ledger. `entry_iv.py` records the expiration's model-free implied variance
at entry (annualised sigma^2 on calendar time, Cboe's clock). This measures the variance SPX then
delivered over the same window, from the position's own mark path, and states the gap in vol
points: implied minus realised, positive when the market paid more for the wings than it moved.

The window is (entry session, expiration). Every arm enters the identical structure on the same
tick, so arms share one window and one reading; the summary counts a window once, never once per
arm (four arms would count one market move four times).

The path is the spot recorded on every mark tick (`bwb_marks`, one row per leg per tick, so
deduplicated by `marked_at`), subsampled to `sample_seconds` (five minutes by default: one-minute
index returns carry microstructure noise that inflates realised variance), ending on the row's
`settlement_spot` -- the print the trade was settled on. Overnight and weekend moves are in it as
the first return of the next session. Realised variance is the sum of squared log returns, so a
gap in the path loses no variance, only resolution; but a path that starts late, ends early or has
an in-session hole is refused as a measure (`coverage` says why) and stays out of the summary,
never estimated.
"""

from __future__ import annotations

import itertools
import math
import statistics
from datetime import datetime, time
from zoneinfo import ZoneInfo

from cherrypick.core.impliedvar import MINUTES_PER_YEAR

ET = ZoneInfo("America/New_York")
SAMPLE_SECONDS = 300
EDGE_TOLERANCE_S = 600  # the path must start within this of entry and end within this of the close
MAX_SESSION_GAP_S = 900  # an in-session hole longer than this refuses the window


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def close_ts(expiration: str) -> float:
    """16:00 ET on the expiration date: SPXW's PM settlement, the end of the realised window."""
    return datetime.combine(datetime.fromisoformat(expiration).date(), time(16, 0), ET).timestamp()


def spot_path(conn, position_id: str) -> list[tuple[float, float]]:
    """One (marked_at, spot) per tick, in time order."""
    rows = conn.execute(
        "SELECT marked_at, MAX(spot) AS spot FROM bwb_marks WHERE position_id = ? AND spot > 0 "
        "GROUP BY marked_at ORDER BY marked_at",
        (position_id,),
    ).fetchall()
    return [(float(r["marked_at"]), float(r["spot"])) for r in rows]


def subsample(path: list[tuple[float, float]], seconds: float) -> list[tuple[float, float]]:
    """The first point, then each point at least `seconds` after the last one kept."""
    out: list[tuple[float, float]] = []
    for t, s in path:
        if not out or t - out[-1][0] >= seconds:
            out.append((t, s))
    return out


def realised(
    path: list[tuple[float, float]],
    *,
    start_ts: float,
    end_ts: float,
    end_spot: float | None,
    sample_seconds: float = SAMPLE_SECONDS,
) -> dict:
    """Realised total variance over [start_ts, end_ts] from `path`, ending on `end_spot` where given.

    Pure. `coverage` is None for a measurable window, else the reason it is not one."""
    raw = [p for p in path if start_ts - EDGE_TOLERANCE_S <= p[0] <= end_ts + 60]
    pts = subsample(raw, sample_seconds)
    if end_spot and end_spot > 0:
        pts.append((end_ts, float(end_spot)))
    out: dict = {"returns": max(0, len(pts) - 1), "total_var": None, "max_gap_min": None, "coverage": None}
    if len(raw) < 2 or len(pts) < 3:
        out["coverage"] = "no_path"
        return out
    out["total_var"] = sum(math.log(b[1] / a[1]) ** 2 for a, b in itertools.pairwise(pts))
    # Coverage is judged on the recorded ticks, never on the subsample (whose spacing is the choice).
    same_day = [
        b[0] - a[0]
        for a, b in itertools.pairwise(raw)
        if datetime.fromtimestamp(a[0], ET).date() == datetime.fromtimestamp(b[0], ET).date()
    ]
    gap = max(same_day, default=0.0)
    out["max_gap_min"] = round(gap / 60, 1)
    if raw[0][0] - start_ts > EDGE_TOLERANCE_S:
        out["coverage"] = "starts_late"
    elif end_ts - raw[-1][0] > EDGE_TOLERANCE_S:
        out["coverage"] = "ends_early"  # a settlement print does not fill the hours before it
    elif gap > MAX_SESSION_GAP_S:
        out["coverage"] = "session_gap"
    return out


def windows(conn, *, sample_seconds: float = SAMPLE_SECONDS) -> list[dict]:
    """One row per settled (entry session, expiration) window, with the implied reading where it
    was recorded and the realised variance where the path measures it."""
    rows = conn.execute(
        "SELECT * FROM bwb_positions WHERE status = 'closed' AND entry_time IS NOT NULL "
        "ORDER BY entry_session, expiration, (arm = 'control') DESC, position_id"
    ).fetchall()
    seen: dict[tuple[str, str], dict] = {}
    for p in rows:
        key = (p["entry_session"], p["expiration"])
        if key in seen:
            seen[key]["arms"] += 1
            continue
        start, end = _ts(p["entry_time"]), close_ts(p["expiration"])
        years = (end - start) / 60.0 / MINUTES_PER_YEAR
        rv = realised(
            spot_path(conn, p["position_id"]),
            start_ts=start,
            end_ts=end,
            end_spot=p["settlement_spot"],
            sample_seconds=sample_seconds,
        )
        rec = {
            "entry_session": p["entry_session"],
            "expiration": p["expiration"],
            "position_id": p["position_id"],
            "arms": 1,
            "years": round(years, 6),
            "returns": rv["returns"],
            "max_gap_min": rv["max_gap_min"],
            "coverage": rv["coverage"],
            "realised_vol": None,
            "implied_vol": p["entry_iv_vol"],
            "implied_complete": p["entry_iv_complete"],
            "implied_reason": p["entry_iv_reason"],
            "premium_vol": None,
            "premium_var": None,
        }
        if rv["coverage"] is None and years > 0:
            rec["realised_vol"] = round(100.0 * math.sqrt(rv["total_var"] / years), 3)
            if p["entry_iv_var"] is not None:
                rec["premium_vol"] = round(p["entry_iv_vol"] - rec["realised_vol"], 3)
                rec["premium_var"] = p["entry_iv_var"] - rv["total_var"] / years
        seen[key] = rec
    return list(seen.values())


def summary(rows: list[dict]) -> dict:
    """Over measured windows with an implied reading only. A cut-off strip is a lower bound on
    implied, so its premium is too; the complete-only figures are stated beside it."""
    both = [r for r in rows if r["premium_vol"] is not None]
    complete = [r for r in both if r["implied_complete"] == 1]

    def stats(rs: list[dict]) -> dict:
        if not rs:
            return {"windows": 0}
        prem = [r["premium_vol"] for r in rs]
        return {
            "windows": len(rs),
            "mean_implied_vol": round(statistics.mean(r["implied_vol"] for r in rs), 2),
            "mean_realised_vol": round(statistics.mean(r["realised_vol"] for r in rs), 2),
            "mean_premium_vol": round(statistics.mean(prem), 2),
            "median_premium_vol": round(statistics.median(prem), 2),
            "implied_above_realised": sum(1 for x in prem if x > 0),
        }

    return {
        "settled_windows": len(rows),
        "realised_measured": sum(1 for r in rows if r["realised_vol"] is not None),
        "refused": {
            c: sum(1 for r in rows if r["coverage"] == c)
            for c in sorted({r["coverage"] for r in rows} - {None})
        },
        "with_implied": stats(both),
        "with_complete_implied": stats(complete),
    }


def run(conn, *, sample_seconds: float = SAMPLE_SECONDS) -> dict:
    rows = windows(conn, sample_seconds=sample_seconds)
    return {"sample_seconds": sample_seconds, "summary": summary(rows), "windows": rows}
