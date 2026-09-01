"""Read-only query layer over the paper database.

The dashboard, the suite section card, and the EOD writer all read through here. That is the point:
MEIC grew three call sites that disagree with each other about what "net" means — its Today grid uses
raw `pnl` while its profile comparison uses `pnl - fees` — and the cure is one layer that answers each
question exactly once.

Nothing here writes, trades, or reaches the network. Every function takes an open connection.

**Win rate is per position, net of fees.** MEIC counts wins per spread LEG, because an iron condor can
finish with one side a winner and the other a loser and no single verdict is honest. A butterfly and a
vertical each resolve to one number, so the simple definition is the correct one here. Do not "fix"
this into MEIC's leg-counting — it would be wrong for this instrument.
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.flies import (
    clock,  # noqa: E402
    fly,  # noqa: E402
)

GRANULARITIES = ("daily", "weekly", "monthly")

# Every P&L query filters to settled positions. An open credit spread is not a result yet, and
# counting it as one would flatter whichever arm happens to be holding something at the time.
_SETTLED = "status = 'settled'"


def _round(value, digits=2):
    return None if value is None else round(value, digits)


def _rate(numerator, denominator, digits=4):
    return round(numerator / denominator, digits) if denominator else None


# --------------------------------------------------------------------------- period stats
def _period_clause(start=None, end=None, arm=None, symbol=None, include_void=False):
    """The shared WHERE every read surface builds on.

    Void rows are excluded by default. `void_reason` marks rows whose DECISIONS were made on
    numbers a later fix proved wrong — not rows that merely lost money — so leaving them in a
    P&L or completion table states a result the ledger cannot support. Callers that genuinely
    want them (a raw dump, or `voided` below, which exists to account for what was held back)
    pass `include_void=True`.
    """
    clause, params = [_SETTLED], []
    if not include_void:
        clause.append("void_reason IS NULL")
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    return " AND ".join(clause), params


def _summarize(rows) -> dict:
    gross = sum((r["gross_pnl"] or 0.0) for r in rows)
    fees = sum((r["fees"] or 0.0) for r in rows)
    nets = [(r["pnl"] or 0.0) for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    total_win, total_loss = sum(wins), abs(sum(losses))
    return {
        "trades": len(rows),
        "gross_pnl": _round(gross),
        "fees": _round(fees),
        "net_pnl": _round(sum(nets)),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _rate(len(wins), len(wins) + len(losses)),
        "avg_pnl": _round(sum(nets) / len(nets)) if nets else None,
        "avg_win": _round(total_win / len(wins)) if wins else None,
        "avg_loss": _round(-total_loss / len(losses)) if losses else None,
        # Fees as a share of gross credit taken in — the number that turned up a trade collecting
        # $4.00 against $4.96 of fees elsewhere in this suite.
        "fee_drag_pct": _round(fees / gross * 100) if gross > 0 else None,
        "profit_factor": _round(total_win / total_loss) if total_loss > 0 else None,
    }


def stats_for_period(conn, start=None, end=None, arm=None, symbol=None) -> dict:
    where, params = _period_clause(start, end, arm, symbol)
    rows = conn.execute(f"SELECT gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params).fetchall()
    return _summarize(rows)


def _week_start(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def _bucket_key(trade_date: str, granularity: str) -> str:
    if granularity == "monthly":
        return trade_date[:7]
    if granularity == "weekly":
        # Computed in Python: SQLite's %W starts weeks on Sunday, which would split a Monday-anchored
        # trading week across two buckets.
        return _week_start(date.fromisoformat(trade_date))
    return trade_date


def pnl_series(conn, granularity: str = "daily", arm=None, symbol=None) -> list[dict]:
    """P&L bucketed by day / week / month.

    Shares `_period_clause` with `stats_for_period` deliberately, so summing this series over a range
    equals that function's `net_pnl` for the same range. That consistency is a guarantee the dashboard
    relies on and a test asserts.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}")
    where, params = _period_clause(arm=arm, symbol=symbol)
    rows = conn.execute(
        f"SELECT trade_date, gross_pnl, fees, pnl FROM fly_positions WHERE {where} "
        "AND trade_date IS NOT NULL ORDER BY trade_date",
        params,
    ).fetchall()

    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_bucket_key(r["trade_date"], granularity), []).append(r)

    out, cumulative = [], 0.0
    for key in sorted(buckets):
        summary = _summarize(buckets[key])
        cumulative += summary["net_pnl"] or 0.0
        out.append({"bucket": key, **summary, "cumulative_pnl": _round(cumulative)})
    return out


# --------------------------------------------------------------------------- breakdowns
# The arms differ by CENTRING, TIMING and WIDTH — never by entry mode. So an arm that also took
# outright flies is not being compared like for like: `gex` was the only arm ever to take one (5 of
# them, every one a loser, -$199.45 total), which charged its column with a cost no other arm could
# incur and made "gex vs control" partly a legged-vs-outright comparison instead of a centring one.
# The arm comparison therefore reads legged only. This is a READ-LAYER filter on purpose — the rows
# stay in the ledger, `by_entry_mode` still reports both, and the book totals in `stats_for_period` /
# `pnl_series` remain whole, because the book really did pay for those flies and rule 6 says a
# negative result is the finding rather than something to remove.
COMPARISON_ENTRY_MODES = ("legged",)


def by_arm(conn, start=None, end=None, entry_modes=COMPARISON_ENTRY_MODES, symbol=None) -> list[dict]:
    """Per-arm comparison — the module's headline output. The arms exist to be compared; a blended
    total would hide the only contrast the experiment is designed to draw.

    Scoped to `entry_modes` (legged only by default — see COMPARISON_ENTRY_MODES) so a mode only one
    arm ever traded cannot distort the ranking. Pass `entry_modes=None` for the unfiltered view; the
    amount held back is reported by `arm_comparison_exclusions`, so it is never silently dropped.

    `symbol` narrows to one underlying (e.g. isolating XSP sessions from the retired SPX ones) — every
    arm's book otherwise blends both, which is exactly the kind of silent cross-book mixing the module's
    honesty rules exist to prevent (fee schedules and wing scale both differ by symbol).
    """
    where, params = _period_clause(start, end, symbol=symbol)
    if entry_modes:
        where += f" AND entry_mode IN ({','.join('?' * len(entry_modes))})"
        params = [*params, *entry_modes]
    rows = conn.execute(
        f"SELECT arm, gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["arm"] or "unassigned", []).append(r)
    out = [{"arm": arm, **_summarize(rs)} for arm, rs in grouped.items()]
    return sorted(out, key=lambda x: x["net_pnl"] or 0, reverse=True)


def break_even(conn, start=None, end=None, symbol=None, entry_modes=COMPARISON_ENTRY_MODES) -> list[dict]:
    """Each arm's completion rate against the completion rate it would need to break even.

    Rule 4 says completion rate is the number that decides this strategy; that is only true against
    a bar, and the bar is set by the two branches' own averages. A miss costs `|avg stranded|` and a
    completion earns `avg completed`, so the rate at which they cancel is
    `|avg stranded| / (|avg stranded| + avg completed)`.

    **Per arm, never blended, and that is the whole point.** On the 2026-08-01.. SPX era the blended
    figure was 66.0% observed against 78.3% needed — a book comfortably under water, and the reading
    that shaped three issues. Split by arm, `control` sits at 78.6% against 75.3% and is PROFITABLE,
    while `gex` (44.4% against 91.8%) and `time_window` (52.2% against 72.2%) carry the entire loss.
    The blended number was not a summary of the arms, it was an average across a working one and two
    broken ones, and it pointed at the construction when the evidence pointed at the centring.

    `margin_pts` is observed minus needed: positive clears its own bar. Read `trades` first — an arm
    with a handful of positions produces a bar as noisy as the rate it is compared against.
    """
    where, params = _period_clause(start, end, symbol=symbol)
    if entry_modes:
        where += f" AND entry_mode IN ({','.join('?' * len(entry_modes))})"
        params = [*params, *entry_modes]
    rows = conn.execute(f"SELECT arm, kind, pnl FROM fly_positions WHERE {where}", params).fetchall()

    by_arm_: dict[str, list] = {}
    for r in rows:
        by_arm_.setdefault(r["arm"] or "unassigned", []).append(r)

    out = []
    for arm, rs in by_arm_.items():
        completed = [(r["pnl"] or 0.0) for r in rs if r["kind"] == "fly"]
        stranded = [(r["pnl"] or 0.0) for r in rs if r["kind"] != "fly"]
        avg_c = sum(completed) / len(completed) if completed else None
        avg_s = sum(stranded) / len(stranded) if stranded else None
        rate = _rate(len(completed), len(rs))
        # Undefined unless both branches have occurred AND a completion actually pays: with no
        # stranding yet, or a completed average that is itself negative, there is no rate at which
        # the two cancel and reporting one would invent a bar out of a single branch.
        needed = None
        if avg_c is not None and avg_s is not None and avg_s < 0 < avg_c:
            needed = _rate(-avg_s, -avg_s + avg_c)
        out.append(
            {
                "arm": arm,
                "trades": len(rs),
                "completed": len(completed),
                "completion_rate": rate,
                "avg_completed": _round(avg_c) if avg_c is not None else None,
                "avg_stranded": _round(avg_s) if avg_s is not None else None,
                "break_even_rate": needed,
                "margin_pts": _round((rate - needed) * 100, 1) if rate is not None and needed else None,
                "net_pnl": _round(sum(completed) + sum(stranded)),
            }
        )
    return sorted(out, key=lambda x: (x["margin_pts"] is None, -(x["margin_pts"] or 0)))


def voided(conn, start=None, end=None, symbol=None) -> dict:
    """Rows held back as void, grouped by reason — so the exclusion is stated rather than inferred
    from a gap, the same principle `arm_comparison_exclusions` exists for.

    A void row is one whose decisions rest on a defect a later fix proved wrong. It is NOT a losing
    row, and not one a caller filtered out: those stay in every table. The distinction matters
    because "we excluded these because they lost" is exactly the claim this module refuses to make.
    """
    where, params = _period_clause(start, end, symbol=symbol, include_void=True)
    rows = conn.execute(
        f"SELECT arm, entry_mode, void_reason, gross_pnl, fees, pnl FROM fly_positions "
        f"WHERE {where} AND void_reason IS NOT NULL",
        params,
    ).fetchall()
    by_reason: dict[str, list] = {}
    for r in rows:
        by_reason.setdefault(r["void_reason"], []).append(r)
    return {
        "trades": len(rows),
        "net_pnl": _round(sum((r["pnl"] or 0.0) for r in rows)),
        "by_reason": [
            {
                "void_reason": reason,
                "arms": sorted({r["arm"] for r in rs if r["arm"]}),
                "entry_modes": sorted({r["entry_mode"] for r in rs if r["entry_mode"]}),
                **_summarize(rs),
            }
            for reason, rs in sorted(by_reason.items())
        ],
    }


def arm_comparison_exclusions(
    conn, start=None, end=None, entry_modes=COMPARISON_ENTRY_MODES, symbol=None
) -> dict:
    """What `by_arm` held back, so the exclusion is stated rather than inferred from a gap.

    Without this the arm table would sum to less than the book's own P&L with nothing on the page to
    explain the difference — which is the failure mode the filter is supposed to avoid, not create.
    """
    where, params = _period_clause(start, end, symbol=symbol)
    if not entry_modes:
        return {"excluded_modes": [], "trades": 0, "net_pnl": 0.0, "by_mode": [], "by_arm": []}
    clause = f"{where} AND entry_mode NOT IN ({','.join('?' * len(entry_modes))})"
    rows = conn.execute(
        f"SELECT arm, entry_mode, gross_pnl, fees, pnl FROM fly_positions WHERE {clause}",
        [*params, *entry_modes],
    ).fetchall()
    by_mode: dict[str, list] = {}
    by_arm_: dict[str, list] = {}
    for r in rows:
        by_mode.setdefault(r["entry_mode"] or "unknown", []).append(r)
        by_arm_.setdefault(r["arm"] or "unassigned", []).append(r)
    return {
        "excluded_modes": sorted(by_mode),
        "trades": len(rows),
        "net_pnl": _round(sum((r["pnl"] or 0.0) for r in rows)),
        "by_mode": [{"entry_mode": m, **_summarize(rs)} for m, rs in sorted(by_mode.items())],
        "by_arm": [{"arm": a, **_summarize(rs)} for a, rs in sorted(by_arm_.items())],
    }


# --------------------------------------------------------------------------- regime conditioning
# The dimensions `engine.classify_regime` tags, and the continuous measure recorded beside each
# (2026-08-01). Bucketing on the stored string is the quick read; bucketing the float at analysis
# time is what lets a threshold be re-derived from history instead of re-guessed and re-run.
#
# `center_offset` (2026-08-04) is the odd one out and worth flagging when reading a table of these
# side by side: the other four describe the MARKET we entered into, while this one describes OUR OWN
# choice of centre relative to spot. A market regime is something to condition on; this is something
# to change. See engine._classify_center_offset and docs/centre-lag.md.
REGIME_DIMENSIONS = {
    "vol": ("entry_vol_bucket", "entry_vol_value"),
    "gex": ("entry_gex_bucket", "entry_gex_concentration"),
    "time": ("entry_time_bucket", "entry_time_value"),
    "skew": ("entry_skew_bucket", "entry_skew_value"),
    "center_offset": ("entry_center_offset_bucket", "entry_center_offset_value"),
    "trend": ("entry_trend_bucket", "entry_trend_value"),
}

# `trend` and `center_offset` describe the same event from opposite sides -- a centre left behind by
# a moving market -- so a report showing both should say so rather than presenting them as two
# independent confirmations. They are kept apart because they imply OPPOSITE remedies: `trend` is a
# property of the market and argues for skipping the trade, `center_offset` is a property of our own
# centring rule and argues for fixing it. Muting the gex arm and repairing it are not the same
# decision, and only the second one keeps the arm worth running.
#
# **`center_offset` was put on a retirement condition on 2026-08-04 and cleared it on 2026-08-05.**
# The condition was: if it never fires outside `trend`, it is redundant and should go. On 08-04's
# cross-tab it never had (0 trades) -- but that rested on 2 qualifying rows and settled nothing. One
# session later the cell is populated, and the two rules caught DIFFERENT entries on the same day:
# `center_offset` flagged the 10:01 gex miss (centre +14.7 above spot) that `trend` waved through as
# 'flat', and `trend` flagged the 11:50 and 12:54 misses whose centres sat inside one strike. Kept,
# and the condition is considered answered rather than still pending.
#
#   trend ok, offset ok      n=50  comp 88%  avg  +$40
#   trend ok, offset FAILS   n= 5  comp 80%  avg  -$29
#   trend FAILS, offset ok   n=19  comp 37%  avg -$133
#   both fail                n= 2  comp  0%  avg -$222
#
# (SPX only, the 3 sessions with day_open coverage, n=76. Earlier versions of this table blended the
# XSP era, which the module's own symbol rules say not to do.)


# The session count below which a dimension cannot support a threshold re-cut. Matches MEIC's
# analytics.MIN_EFFECTIVE_N, which in turn matches its experiment.MIN_SESSIONS_FOR_INTERVAL: all
# three answer "how many sessions before a book may draw a conclusion", and a different number here
# would make the same question resolve differently per module. It exists so "not enough sessions
# yet" is a reported state rather than something a reader must infer from a row count that looks
# large. Raise it, don't lower it, if a re-cut made on a sample this size later fails to hold.
MIN_EFFECTIVE_N = 14

# How small within-session movement must be, relative to movement BETWEEN sessions, before a
# dimension counts as daily-scale. Not a constancy test: a daily-scale input still wobbles within a
# day because it is normalized by spot, so strict equality would never fire. Mirrors the same
# constant in packages/meic's analytics.
DAILY_SCALE_RATIO = 0.2


def _session_scale(conn, where: str, params: list, bucket_col: str, value_col: str):
    """(sessions, daily_scale) for one regime dimension -- how many distinct sessions its tagged
    rows came from, and whether its float essentially only moves BETWEEN them, in which case the row
    count overstates the independent draws and `regime_coverage` reports sessions as the effective
    n. Measured from the data (mean within-session range against the range of session means) rather
    than declared per dimension. Degrades to False on a single session or a flat across-session
    range: with nothing to compare against, claiming daily-scale would be reading a zero denominator
    as evidence."""
    rows = conn.execute(
        f"SELECT trade_date, MAX({value_col}) - MIN({value_col}) AS spread, AVG({value_col}) AS mean "
        f"FROM fly_positions WHERE {where} AND {bucket_col} IS NOT NULL AND {value_col} IS NOT NULL "
        f"GROUP BY trade_date",
        params,
    ).fetchall()
    sessions = (
        conn.execute(
            f"SELECT COUNT(DISTINCT trade_date) FROM fly_positions WHERE {where} AND {bucket_col} IS NOT NULL",
            params,
        ).fetchone()[0]
        or 0
    )
    if len(rows) < 2:
        return sessions, False
    means = [r["mean"] for r in rows if r["mean"] is not None]
    spreads = [r["spread"] for r in rows if r["spread"] is not None]
    across = (max(means) - min(means)) if means else 0
    if not spreads or across <= 0:
        return sessions, False
    within = sum(spreads) / len(spreads)
    return sessions, (within / across) < DAILY_SCALE_RATIO


def _edge_label(edges: list[float], value: float | None) -> str:
    if value is None:
        return "unknown"
    for i, edge in enumerate(edges):
        if value < edge:
            return f"<{edge:g}" if i == 0 else f"{edges[i - 1]:g}..{edge:g}"
    return f">={edges[-1]:g}"


def by_regime(
    conn,
    dimension: str,
    start=None,
    end=None,
    entry_modes=COMPARISON_ENTRY_MODES,
    symbol=None,
    bucket_edges: list[float] | None = None,
    phase: str = "entry",
) -> list[dict]:
    """Outcomes grouped by the regime the position was ENTERED into (or completed into, via
    `phase`). Same stat bundle as `by_arm`, deliberately — a regime slice and an arm slice have to be
    read against each other, and two different summary shapes would make that a translation exercise.

    `dimension` is one of REGIME_DIMENSIONS. By default it groups on the stored bucket string; pass
    `bucket_edges` to re-bucket the recorded float instead, which is the whole reason that float is
    stored. Example: `bucket_edges=[0.4, 0.6, 0.8]` re-cuts GEX concentration without re-running a
    single session.

    Scoped to `entry_modes` (legged only by default) for the same reason `by_arm` is: a mode only one
    arm ever traded would otherwise distort the comparison.

    **Read the `trades` count before the P&L.** Regime tagging only started 2026-07-31 and cannot be
    backfilled -- `paper_replay` has no historical gamma source -- so early results are thin, and a
    dimension whose rows all land in one bucket is reporting no contrast rather than no effect. The
    `unknown` bucket is carried rather than dropped: a regime we could not read is itself a fact
    about the session, and hiding it would make coverage look better than it was.
    """
    if dimension not in REGIME_DIMENSIONS:
        raise ValueError(f"by_regime: unknown dimension {dimension!r} (have {sorted(REGIME_DIMENSIONS)})")
    if phase not in ("entry", "completion"):
        raise ValueError(f"by_regime: phase must be 'entry' or 'completion', got {phase!r}")
    bucket_col, value_col = (c.replace("entry_", f"{phase}_", 1) for c in REGIME_DIMENSIONS[dimension])

    where, params = _period_clause(start, end, symbol=symbol)
    if entry_modes:
        where += f" AND entry_mode IN ({','.join('?' * len(entry_modes))})"
        params = [*params, *entry_modes]
    rows = conn.execute(
        f"SELECT {bucket_col} AS bucket, {value_col} AS value, gross_pnl, fees, pnl, trade_date "
        f"FROM fly_positions WHERE {where}",
        params,
    ).fetchall()

    grouped: dict[str, list] = {}
    for r in rows:
        key = _edge_label(bucket_edges, r["value"]) if bucket_edges else (r["bucket"] or "untagged")
        grouped.setdefault(key, []).append(r)
    out = []
    for bucket, rs in grouped.items():
        values = [r["value"] for r in rs if r["value"] is not None]
        out.append(
            {
                "dimension": dimension,
                "phase": phase,
                "bucket": bucket,
                # The measured range behind the bucket, so a threshold can be judged against what
                # actually occurred rather than against what it was guessed to be.
                "value_min": _round(min(values), 4) if values else None,
                "value_max": _round(max(values), 4) if values else None,
                # Distinct sessions behind this bucket. A bucket's trade count and its independent
                # -draw count are different numbers, and only the second bounds what can be read off
                # it -- see regime_coverage's effective_n.
                "sessions": len({r["trade_date"] for r in rs if r["trade_date"]}),
                **_summarize(rs),
            }
        )
    return sorted(out, key=lambda x: x["net_pnl"] or 0, reverse=True)


def regime_coverage(conn, start=None, end=None, symbol=None) -> dict:
    """How much of the settled book is regime-tagged at all, per dimension, and whether any
    dimension is degenerate (every tagged row in one bucket).

    This is the honesty guard on `by_regime`: a single-bucket dimension produces a table that looks
    like a result and contains no contrast. `entry_gex_bucket` was exactly that -- 'thin' 60 times
    out of 60 -- until the classifier was windowed on 2026-08-01, and nothing in the read layer
    would have said so.

    **`effective_n` counts SESSIONS, not rows, for any dimension whose input only moves daily.** Rows
    are not draws: a dimension fed by a daily-scale input holds one value repeated across every row
    of a day, so its row count says nothing about how many independent observations are behind it.
    `daily_scale` is detected from whether the recorded float ever varies WITHIN a session rather
    than declared per dimension, so it tracks what the data did. `underpowered` is the flag to act
    on, and it is deliberately distinct from `degenerate`: the two look identical in a bucket table
    and call for opposite responses -- re-cut the float, versus collect more sessions. `time_bucket`
    was re-cut on 97 rows, and this is what makes the sessions behind such a decision visible before
    it is taken rather than after.
    """
    where, params = _period_clause(start, end, symbol=symbol)
    total = conn.execute(f"SELECT COUNT(*) FROM fly_positions WHERE {where}", params).fetchone()[0]
    out = {"settled_trades": total, "dimensions": {}}
    for dim, (bucket_col, value_col) in REGIME_DIMENSIONS.items():
        rows = conn.execute(
            f"SELECT {bucket_col} AS bucket, COUNT(*) AS n FROM fly_positions WHERE {where} "
            f"AND {bucket_col} IS NOT NULL GROUP BY 1",
            params,
        ).fetchall()
        counts = {r["bucket"]: r["n"] for r in rows}
        tagged = sum(counts.values())

        sessions, daily_scale = _session_scale(conn, where, params, bucket_col, value_col)
        effective_n = sessions if daily_scale else tagged

        out["dimensions"][dim] = {
            "tagged": tagged,
            "untagged": total - tagged,
            "coverage_pct": _round(tagged / total * 100) if total else None,
            "buckets": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "sessions": sessions,
            "daily_scale": daily_scale,
            "effective_n": effective_n,
            # One populated bucket means the tag cannot discriminate — no contrast, not no effect.
            "degenerate": tagged > 0 and len(counts) == 1,
            # Keyed on SESSIONS rather than effective_n: rows inside one session share that
            # session's market, so a cut made on a few days of intraday-varying data is still a cut
            # made on a few days. `daily_scale` says how severe the collapse is, not whether the
            # session count binds -- it always does.
            "underpowered": tagged > 0 and sessions < MIN_EFFECTIVE_N,
        }
    return out


def by_entry_mode(conn, start=None, end=None, symbol=None) -> list[dict]:
    """legged vs outright. They perform differently enough that averaging them together would hide
    the finding — legged manufactures its own floor, outright spends one."""
    where, params = _period_clause(start, end, symbol=symbol)
    rows = conn.execute(
        f"SELECT entry_mode, gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["entry_mode"] or "unknown", []).append(r)
    return [{"entry_mode": m, **_summarize(rs)} for m, rs in sorted(grouped.items())]


# The drift past which a session's direction is treated as committed, as a FRACTION of spot rather
# than in points. 0.0026 is the 20 points that were measured on SPX (~7710), and expressing it
# relatively is not cosmetic: the EOD writer reports per day across every book, so an absolute band
# silently called all 158 XSP rows "flat" — a 20-point move is impossible on a ~750 underlying. That
# is the same failure MEIC's ATR and intraday-range thresholds were migrated away from, and the same
# shape as `min_floor_dollars` carrying XSP scaling into an SPX era. Chosen on the rows that measure
# it (see CLAUDE.md's trend-band note): a current best estimate, not a calibrated constant.
DRIFT_BAND_PCT = 0.0026


def by_drift_alignment(
    conn,
    start=None,
    end=None,
    symbol=None,
    arm=None,
    band=DRIFT_BAND_PCT,
    entry_modes=COMPARISON_ENTRY_MODES,
) -> list[dict]:
    """Outcomes split by whether the COMPLETING direction agreed with the session's committed drift.

    A legged entry only completes once spot moves a particular way — `fly.completing_side_direction`,
    which inverts by side and is the easiest thing here to code backwards. `entry_trend_value`
    records `spot − day_open` at entry. So an entry whose completion needs spot to go UP, taken on a
    day already committed DOWN, is betting on a reversal in the hours it has left.

    Buckets are `with` / `flat` / `against`, where `flat` is any |drift| inside `band` — a
    fraction of spot at entry, so the split means the same thing on a 750 underlying as a 7710 one.

    **Measured 2026-08-07, 97 SPX positions over 4 sessions: `with` completed 82%, `against` 7%.**
    The 15 `against` entries lost $3,129 — more than the era's entire −$2,973 — and the split holds
    in both trend directions (12 opposing on up days, 3 on down days), so it is not an up-trend
    artefact. Concentrated by arm: `control` takes 4% opposing, `gex` 28%, `time_window` 35%, and
    removing them flips `time_window` from −$1,154 to +$210 while `gex` still loses.

    **But the XSP era INVERTS it, and that is why this is reported per symbol.** There `against`
    completed **94%** (16 of 17) for −$15. Blended, the two read 53% — a real signal presented as a
    weak one, and the reason the EOD section never blends them. The likeliest explanation is scale
    rather than contradiction: XSP ran 1-wide wings on a ~750 underlying, so a completion needs a
    point or two of drift and arrives almost regardless of direction, while 5-wide on ~7710 needs
    proportionally far more. If that is right, this signal is entangled with wing width and is a
    statement about the SPX structure, not about drift as such — which is one more reason it is a
    reported prior and not a gate.

    **Reported, not gated.** `choose_side` is what generates these — on a trending day spot moves
    away from the centre, so it selects the side needing a reversal — and a fix belongs there rather
    than in a bolt-on filter. But `band` and the opposition rule were both chosen on the same rows
    that measure them, the down-day cell is n=3, and `docs/centre-lag.md` sets the bar for a gate at
    a second clearly down-trending session. This surface exists so the next one is read against a
    stated prior instead of rediscovered.

    Rows without a recorded `entry_trend_value` are omitted rather than bucketed — a session whose
    open was never captured is not a `flat` day, and calling it one would invent coverage.
    """
    where, params = _period_clause(start, end, arm=arm, symbol=symbol)
    if entry_modes:
        where += f" AND entry_mode IN ({','.join('?' * len(entry_modes))})"
        params = [*params, *entry_modes]
    rows = conn.execute(
        f"SELECT side, kind, entry_trend_value, underlying_at_entry, gross_pnl, fees, pnl "
        f"FROM fly_positions WHERE {where} AND entry_trend_value IS NOT NULL "
        "AND underlying_at_entry IS NOT NULL AND underlying_at_entry > 0",
        params,
    ).fetchall()

    grouped: dict[str, list] = {}
    for r in rows:
        drift = r["entry_trend_value"]
        if abs(drift) / r["underlying_at_entry"] < band:
            key = "flat"
        else:
            needs_up = fly.completing_side_direction(r["side"]) == "up"
            key = "with" if needs_up == (drift > 0) else "against"
        grouped.setdefault(key, []).append(r)

    out = []
    for key in ("with", "flat", "against"):
        rs = grouped.get(key)
        if not rs:
            continue
        completed = [r for r in rs if r["kind"] == "fly"]
        out.append(
            {
                "alignment": key,
                "completed": len(completed),
                "completion_rate": _rate(len(completed), len(rs)),
                "band_pct": band,
                **_summarize(rs),
            }
        )
    return out


def by_entry_window(conn, start=None, end=None, symbol=None) -> list[dict]:
    """Per time-of-day window.

    The windows are deliberately unranked in config — we had no intraday history to rank them with, so
    every trade is tagged and the ranking is meant to emerge here, from our own sessions.
    """
    where, params = _period_clause(start, end, symbol=symbol)
    rows = conn.execute(
        f"SELECT entry_window, arm, gross_pnl, fees, pnl FROM fly_positions WHERE {where}", params
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["entry_window"] or "unwindowed", []).append(r)
    return [{"window": w, **_summarize(rs)} for w, rs in sorted(grouped.items())]


def fee_drag(conn, start=None, end=None, symbol=None) -> list[dict]:
    """Fee drag per arm. Broken out because a legged fly pays two fee stacks against a credit that may
    be $35-105 — costs are not a rounding error for this strategy, they are the experiment."""
    return [
        {
            "arm": r["arm"],
            "gross_pnl": r["gross_pnl"],
            "fees": r["fees"],
            "net_pnl": r["net_pnl"],
            "fee_drag_pct": r["fee_drag_pct"],
            "trades": r["trades"],
        }
        for r in by_arm(conn, start, end, symbol=symbol)
    ]


def daily_pnl(conn, arm=None, symbol=None) -> list[dict]:
    """Per-day totals for the calendar heatmap."""
    return [
        {
            "date": b["bucket"],
            "trades": b["trades"],
            "gross_pnl": b["gross_pnl"],
            "fees": b["fees"],
            "net_pnl": b["net_pnl"],
        }
        for b in pnl_series(conn, "daily", arm=arm, symbol=symbol)
    ]


# --------------------------------------------------------------------------- completion & counterfactual
def completion_stats(conn, start=None, end=None, symbol=None, arm=None, entry_mode="legged") -> dict:
    """Completion rate, latency, and the counterfactual split — the numbers that decide whether this
    strategy is real.

    A legged entry that never completes leaves an ordinary short vertical carrying full defined risk.
    If that branch dominates, the strategy is short verticals wearing a costume, and no amount of P&L
    on the completed ones changes that. The counterfactual then says whether the misses were the
    market's fault or our gate's:

      never_offered   the best debit ever seen was still above the credit — no buffer would have helped
      buffer_blocked  the debit beat the credit but not `fee_buffer` — our threshold cost us the fly
      floor_blocked   it cleared the buffer but the post-fee floor missed `min_floor_dollars`

    The last two used to be reported together as `buffer_too_tight`, which was actively misleading:
    completion is gated by `D < C - fee_buffer` AND `floor >= min_floor_dollars`, so a miss lumped under
    that name usually had nothing to do with the buffer. On the first five sessions the split was 1
    buffer vs 5 floor — and the single buffer case had a post-fee floor of -$1.89, i.e. the buffer
    correctly refused a money-losing fly. Reading that as "loosen the buffer" is the exact mistake this
    counterfactual exists to prevent, and the two have opposite remedies.

    Which gate bound is read from the `fly_decisions` journal rather than recomputed here: the engine
    already recorded its reason against the config in force at the time, so this cannot drift from the
    gate as configured, and this layer needs no access to config.

    `entry_mode="debit_first"` runs the same shape of report on the mirror-image mode: the roles of
    credit/debit and the counterfactual direction flip (a debit_first miss means the best completing
    CREDIT ever offered never beat the DEBIT paid), but the meaning of each bucket is the same.

    `entry_mode="bwb_roll"` reports the roll the same way: "completed" means rolled (kind -> fly),
    the counterfactual compares `best_roll_debit` against the entry credit (the roll gate is
    `roll_debit < credit - fee_buffer`), and the journal mode read for the floor split is `"roll"`.
    """
    clause, params = [], []
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    where = (" WHERE " + " AND ".join(clause)) if clause else ""
    rows = conn.execute(
        f"SELECT position_id, kind, entry_mode, credit, debit, best_completing_debit, "
        f"best_completing_credit, best_roll_debit, completion_latency_min, underlying_at_entry, "
        f"spot_at_completion FROM fly_positions{where}",
        params,
    ).fetchall()

    mode_rows = [r for r in rows if r["entry_mode"] == entry_mode]
    completed = [r for r in mode_rows if r["kind"] == "fly"]
    missed = [r for r in mode_rows if r["kind"] != "fly"]
    completion_mode = {"legged": "completion", "debit_first": "debit_completion", "bwb_roll": "roll"}.get(
        entry_mode, "completion"
    )

    # Positions the floor gate ever turned down. Reaching that gate at all means the price side had
    # already cleared `fee_buffer`, so a position appearing here was blocked by the floor, not the
    # buffer — even though it will also carry the price-side-too-tight rows from other moments.
    floor_gated = {
        r["position_id"]
        for r in conn.execute(
            "SELECT DISTINCT position_id FROM fly_decisions "
            "WHERE mode = ? AND reason = 'floor_below_minimum_after_fees'",
            (completion_mode,),
        )
        if r["position_id"] is not None
    }

    never_offered, buffer_blocked, floor_blocked, unknown = 0, 0, 0, 0
    for r in missed:
        if entry_mode == "legged":
            best, target = r["best_completing_debit"], r["credit"]
            offered = best is not None and target is not None and best < target
        elif entry_mode == "bwb_roll":
            best, target = r["best_roll_debit"], r["credit"]
            offered = best is not None and target is not None and best < target
        else:
            best, target = r["best_completing_credit"], r["debit"]
            offered = best is not None and target is not None and best > target
        if best is None or target is None:
            unknown += 1
        elif not offered:
            never_offered += 1
        elif r["position_id"] in floor_gated:
            floor_blocked += 1
        else:
            buffer_blocked += 1

    latencies = [r["completion_latency_min"] for r in completed if r["completion_latency_min"] is not None]
    moves = [
        abs((r["spot_at_completion"] or 0) - (r["underlying_at_entry"] or 0))
        for r in completed
        if r["spot_at_completion"] is not None and r["underlying_at_entry"] is not None
    ]
    return {
        "legged_entries": len(mode_rows),
        "completed": len(completed),
        "completion_rate": _rate(len(completed), len(mode_rows)),
        "never_offered": never_offered,
        "buffer_blocked": buffer_blocked,
        "floor_blocked": floor_blocked,
        "counterfactual_unknown": unknown,
        "median_latency_min": _round(_median(latencies), 1),
        "min_latency_min": _round(min(latencies), 1) if latencies else None,
        "max_latency_min": _round(max(latencies), 1) if latencies else None,
        "median_spot_move": _round(_median(moves)),
    }


def left_on_table(conn, start=None, end=None, symbol=None, arm=None, entry_mode="debit_first") -> dict:
    """How much better the completing price got AFTER the first qualifying tick was taken —
    the counterfactual behind any wait-for-better completion rule, measured from the
    post_best_completing_* columns book.py's step-1d telemetry keeps updating until settlement.

    For `debit_first` the improvement is `post_best_completing_credit - credit` (how much richer
    the completing sale would have paid); for `legged` it is `debit - post_best_completing_debit`
    (how much cheaper the completing purchase would have gotten). Both are floored at 0 per
    position — the completion tick itself seeds the tracker, so a negative difference just means
    the price never improved, which is recorded as 0 improvement, not a loss.

    Split by `completion_gex_bucket` because that is the drift-regime hypothesis under test: dealer
    pinning (positive-gamma pull toward the centre) is the regime where waiting should have paid
    for debit_first, and thin/negative gamma the regime where first-tick should already be best.
    If the split shows no conditional difference, first-tick stays and that is the finding (rule 6).

    Improvements are in PRICE POINTS; `*_dollars` figures multiply by the contract multiplier and
    quantity. NULL-tracker rows (pre-2026-08-03 completions, iron/bwb completions) are excluded and
    counted in `untracked`.
    """
    clause, params = ["entry_mode = ?", "kind = 'fly'"], [entry_mode]
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    rows = conn.execute(
        f"SELECT credit, debit, quantity, completion_gex_bucket, "
        f"post_best_completing_debit, post_best_completing_credit "
        f"FROM fly_positions WHERE {' AND '.join(clause)}",
        params,
    ).fetchall()

    def improvement(r):
        if entry_mode == "debit_first":
            if r["post_best_completing_credit"] is None or r["credit"] is None:
                return None
            return max(0.0, r["post_best_completing_credit"] - r["credit"])
        if r["post_best_completing_debit"] is None or r["debit"] is None:
            return None
        return max(0.0, r["debit"] - r["post_best_completing_debit"])

    tracked, by_bucket = [], {}
    untracked = 0
    for r in rows:
        imp = improvement(r)
        if imp is None:
            untracked += 1
            continue
        dollars = imp * 100 * (r["quantity"] or 1)
        tracked.append((imp, dollars))
        bucket = r["completion_gex_bucket"] or "untagged"
        by_bucket.setdefault(bucket, []).append((imp, dollars))

    def summarize(pairs):
        pts = [p[0] for p in pairs]
        dollars = [p[1] for p in pairs]
        return {
            "n": len(pairs),
            "improved": sum(1 for p in pts if p > 0),
            "median_improvement_pts": _round(_median(pts), 4),
            "max_improvement_pts": _round(max(pts), 4) if pts else None,
            "median_improvement_dollars": _round(_median(dollars)),
            "total_improvement_dollars": _round(sum(dollars)),
        }

    return {
        "entry_mode": entry_mode,
        "untracked": untracked,
        **summarize(tracked),
        "by_gex_bucket": {bucket: summarize(pairs) for bucket, pairs in sorted(by_bucket.items())},
    }


def completion_trend(conn, start=None, end=None, symbol=None, entry_mode="legged") -> list[dict]:
    """completion_stats' headline number on a date axis: one row per session with entries of
    `entry_mode` — how many, how many became flies, and the rate. Rule 4 says completion rate is
    the number that decides whether this strategy is real; a single blended rate can drift slowly
    while looking stable, so the trend is what makes a deterioration (or a config change's effect)
    visible. Defaults to legged so the section card keeps drawing what it always has."""
    clause, params = ["entry_mode = ?"], [entry_mode]
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    rows = conn.execute(
        f"SELECT trade_date, COUNT(*) AS legged, "
        f"SUM(CASE WHEN kind = 'fly' THEN 1 ELSE 0 END) AS completed "
        f"FROM fly_positions WHERE {' AND '.join(clause)} "
        f"GROUP BY trade_date ORDER BY trade_date",
        params,
    ).fetchall()
    return [
        {
            "day": r["trade_date"],
            "legged_entries": r["legged"],
            "completed": r["completed"],
            "completion_rate": _rate(r["completed"], r["legged"]),
        }
        for r in rows
    ]


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# --------------------------------------------------------------------------- live vs paper
# The abort rule from docs/live-trading-plan.md, instrumented: paper completion is an UPPER
# BOUND on live completion (paper's gate is a clean inequality; live's step 2 is a working
# limit that may sit unfilled), and the strategy's edge IS the completion rate — so once a
# real sample exists, a live rate far enough below contemporaneous paper means the bound is
# not achievable and the pilot should halt.
ABORT_MIN_LIVE_ENTRIES = 30
ABORT_COMPLETION_GAP = 0.15


def live_vs_paper(live_conn, paper_conn, arm: str = "gex") -> dict:
    """Live completion/latency/pricing vs CONTEMPORANEOUS paper for the same arm.

    "Contemporaneous" is load-bearing: paper is restricted to exactly the sessions the live
    arm traded, so a quiet week can't dilute either side. Live entries count ESTABLISHED
    spreads only (an entry order that cancelled unfilled never held risk); paper's fill model
    is instantaneous, so its entries are all accepted rows — that asymmetry is inherent to
    what the two ledgers record, not a bug here.

    **There is one deliberate, labelled duplicate of this shape.** `packages/advisor`'s fact pack
    computes a live-vs-paper summary with its own read-only SQL, tagged
    `"shape": "flies.analytics.live_vs_paper/v1"`. It cannot call this function: that package
    depends on `cherrypick.core` alone and spawns no subprocess, which is what keeps it unable to
    reach anything but a paper advice artifact. Its copy is facts-for-context handed to a model,
    never a report surface — this remains the one place a live-vs-paper number is *reported* from.
    If the shape here changes, bump that tag."""
    days = [
        r[0]
        for r in live_conn.execute(
            "SELECT DISTINCT trade_date FROM fly_positions WHERE arm = ? AND entry_mode = 'legged' "
            "AND status != 'cancelled' ORDER BY trade_date",
            (arm,),
        )
    ]

    def _side(conn) -> dict:
        empty = {
            "sessions": 0,
            "entries": 0,
            "completed": 0,
            "completion_rate": None,
            "median_latency_min": None,
            "avg_credit": None,
            "avg_completion_debit": None,
        }
        if not days:
            return empty
        marks = ",".join("?" * len(days))
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM fly_positions WHERE arm = ? AND entry_mode = 'legged' "
                f"AND status != 'cancelled' AND trade_date IN ({marks})",
                (arm, *days),
            )
        ]
        completed = [r for r in rows if r["kind"] == "fly"]
        latencies = [r["completion_latency_min"] for r in completed if r["completion_latency_min"]]
        credits = [r["credit"] for r in rows if r["credit"] is not None]
        debits = [r["debit"] for r in completed if r["debit"] is not None]
        return {
            "sessions": len(days),
            "entries": len(rows),
            "completed": len(completed),
            "completion_rate": _rate(len(completed), len(rows)),
            "median_latency_min": _median(latencies),
            "avg_credit": _round(sum(credits) / len(credits), 4) if credits else None,
            "avg_completion_debit": _round(sum(debits) / len(debits), 4) if debits else None,
        }

    live = _side(live_conn)
    paper = _side(paper_conn)
    gap = None
    if live["completion_rate"] is not None and paper["completion_rate"] is not None:
        gap = _round(paper["completion_rate"] - live["completion_rate"], 4)
    armed = live["entries"] >= ABORT_MIN_LIVE_ENTRIES
    return {
        "arm": arm,
        "sessions": days,
        "live": live,
        "paper": paper,
        "completion_gap": gap,
        "abort_rule": {
            "min_live_entries": ABORT_MIN_LIVE_ENTRIES,
            "gap_limit": ABORT_COMPLETION_GAP,
            "armed": armed,
            "triggered": bool(armed and gap is not None and gap > ABORT_COMPLETION_GAP),
        },
    }


# --------------------------------------------------------------------------- arm divergence
def arm_divergence(conn, day: str | None = None) -> dict:
    """How often the arms actually picked DIFFERENT centres.

    The experiment can only separate two arms to the extent they disagree. If gex and control choose
    the same strike most of the time, the sample needed to distinguish them grows enormously, and the
    honest conclusion may be that the comparison cannot answer the question as framed. Far better to
    discover that in week one than after a month of collecting data that was never going to separate.
    """
    where, params = "", []
    if day:
        where, params = " WHERE trade_date = ?", [day]
    rows = conn.execute(
        f"SELECT iteration_ts, symbol, arm, center FROM fly_iterations{where} ORDER BY iteration_ts", params
    ).fetchall()

    iterations: dict[tuple, dict] = {}
    for r in rows:
        iterations.setdefault((r["iteration_ts"], r["symbol"]), {})[r["arm"]] = r["center"]

    pairs: dict[tuple, list] = {}
    all_agree = 0
    considered = 0
    for centers in iterations.values():
        named = {a: c for a, c in centers.items() if c is not None}
        if len(named) < 2:
            continue
        considered += 1
        if len(set(named.values())) == 1:
            all_agree += 1
        arms = sorted(named)
        for i, a in enumerate(arms):
            for b in arms[i + 1 :]:
                pairs.setdefault((a, b), []).append(named[a] == named[b])

    return {
        "iterations": considered,
        "all_agree_rate": _rate(all_agree, considered),
        "pairs": [
            {
                "arms": f"{a} vs {b}",
                "iterations": len(matches),
                "agreement_rate": _rate(sum(matches), len(matches)),
            }
            for (a, b), matches in sorted(pairs.items())
        ],
    }


# --------------------------------------------------------------------------- journal & positions
def decision_journal(conn, day: str, arm: str | None = None) -> list[dict]:
    """The day's decisions, newest run first. Already collapsed at write time, so this is a plain read."""
    clause, params = ["trade_date = ?"], [day]
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    rows = conn.execute(
        f"SELECT * FROM fly_decisions WHERE {' AND '.join(clause)} ORDER BY id DESC", params
    ).fetchall()
    return [dict(r) for r in rows]


def positions_for_day(conn, day: str, arm: str | None = None, symbol: str | None = None) -> list[dict]:
    clause, params = ["trade_date = ?"], [day]
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    rows = conn.execute(
        f"SELECT * FROM fly_positions WHERE {' AND '.join(clause)} ORDER BY entry_time", params
    ).fetchall()
    return [dict(r) for r in rows]


def trade_log(conn, limit: int = 1000, arm=None, symbol=None) -> list[dict]:
    where, params = _period_clause(arm=arm, symbol=symbol)
    rows = conn.execute(
        f"SELECT * FROM fly_positions WHERE {where} ORDER BY trade_date DESC, entry_time DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def books_for_day(conn, day: str, arm: str | None = None, symbol: str | None = None) -> list[dict]:
    clause, params = ["trade_date = ?"], [day]
    if arm and arm != "ALL":
        clause.append("arm = ?")
        params.append(arm)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    rows = conn.execute(
        f"SELECT * FROM fly_books WHERE {' AND '.join(clause)} ORDER BY arm", params
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- the risk graph
def payoff_curve(conn, day: str, arm: str, step: float = 1.0, points: int = 120) -> dict:
    """The profit forest itself: book P&L across a price grid, plus the floor and the band it holds over.

    This is the visual the strategy is named for — green across a band with a peak at each centre — and
    it is the one view that shows at a glance whether a book is genuinely safe or merely safe-looking.
    Returns empty (not an error) for a book with no positions; an empty day is ordinary.
    """
    positions = [
        {
            "kind": r["kind"],
            "side": r["side"],
            "center": r["center"],
            "wing_width": r["wing_width"],
            "far_width": r["far_width"],
            "net": r["net"],
            "quantity": r["quantity"] or 1,
            "fees": r["fees"] or 0.0,
        }
        for r in positions_for_day(conn, day, arm)
    ]
    if not positions:
        return {"ok": True, "empty": True, "prices": [], "pnl": [], "positions": 0}

    centers = [p["center"] for p in positions]
    # A bwb's far wing sits outside +/-wing_width, and its negative tail is exactly what this
    # chart must not clip -- a truncated tail would read as "safe" when it isn't.
    width = max(p["far_width"] or p["wing_width"] for p in positions)
    lo, hi = min(centers) - 3 * width, max(centers) + 3 * width
    span = hi - lo
    grid_step = max(step, span / points) if span else step

    prices, pnls = [], []
    x = lo
    while x <= hi + 1e-9:
        prices.append(round(x, 2))
        pnls.append(round(fly.book_pnl(positions, x), 2))
        x += grid_step

    floor = fly.book_floor(positions, step=grid_step)
    cash = fly.book_cash(positions)
    return {
        "ok": True,
        "empty": False,
        "positions": len(positions),
        "prices": prices,
        "pnl": pnls,
        "centers": sorted(set(centers)),
        "floor": floor,
        "cash": cash,
    }


# --------------------------------------------------------------------------- the session timeline
def _state_at(row: dict, when: str) -> dict | None:
    """This position as it stood at `when`, or None if it was not on the book yet.

    A legged entry is a SHORT VERTICAL until it completes and a butterfly afterwards, and the stored
    row only ever holds the latest state. Replaying an intraday book straight from those rows would
    draw the morning as though every fly existed from the moment its credit spread was sold — which
    asserts exactly the per-position floor the module refuses to claim loosely (honesty rule 3).

    The rewind is exact rather than approximate. The completing purchase is a 2-leg vertical, so the
    pre-completion fee is `vertical_open_fee` for the same symbol and size, and the pre-completion net
    is the recorded `credit` — both of which `book.py` wrote and neither of which is inferred.
    """
    entry = row.get("entry_time")
    if not entry or when < entry:
        return None
    state = {
        "kind": row["kind"],
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        # Carried unconditionally, not just on the pre-roll branch below: an un-rolled bwb keeps
        # kind='bwb' on its own row, so a state built without this reaches `fly.position_pnl`'s bwb
        # branch missing the width it reads — and since one KeyError fails the whole /api/data
        # payload, a single open bwb blanks every panel on the page.
        "far_width": row.get("far_width"),
        "net": row["net"],
        "quantity": row["quantity"] or 1,
        "fees": row["fees"] or 0.0,
    }
    completed = row.get("completed_at")
    if completed and when < completed:
        entry_mode = row.get("entry_mode")
        if entry_mode == "legged" and row.get("credit") is not None:
            state["kind"] = "short_vertical"
            state["net"] = row["credit"]
            state["fees"] = fly.vertical_open_fee(row["symbol"], state["quantity"])
        elif entry_mode == "debit_first" and row.get("debit") is not None:
            state["kind"] = "long_vertical"
            state["net"] = -row["debit"]
            state["fees"] = fly.vertical_open_fee(row["symbol"], state["quantity"])
        elif entry_mode == "bwb_roll" and row.get("far_width") is not None:
            state["kind"] = "bwb"
            state["net"] = row["credit"]
            state["far_width"] = row["far_width"]
            state["fees"] = fly.fly_open_fee(row["symbol"], state["quantity"])
    return state


def _entry_structure_label(entry_mode: str, side: str) -> str:
    """What a position's entry event should be labelled as, by construction. Explicit map, not a
    ternary that quietly mislabels any mode it wasn't written for — an unrecognized entry_mode
    falls back to the raw string rather than being guessed at as "short {side}"."""
    labels = {
        "outright": "fly",
        "legged": f"short {side}",
        "debit_first": f"debit {side}",
        "bwb_roll": f"bwb {side}",
    }
    return labels.get(entry_mode, entry_mode)


def session_timeline(conn, day: str | None = None) -> dict:
    """The session along a TIME axis — the one axis every other view here lacks.

    `payoff_curve` plots price at expiry, so nothing in it moves during a session. But the module
    already records an intraday history and then spends it on scalars: `fly_iterations` holds spot and
    each arm's wanted centre on every iteration, and it exists only to produce one agreement
    percentage. This assembles that record instead:

      ticks     spot and each arm's wanted centre, every iteration
      events    entries and completions, placed on the same axis
      spans     leg-in to completion, so completion latency is a LENGTH beside the spot drift that
                bought it — the 2026-07-20 finding (completions arrived only after 10-21 points of
                drift) is a shape over time and has had no axis to appear on

    `settle_now` is the book replayed at each tick: what it would have been worth had the session
    ended at that moment and that price. It is an expiry payoff evaluated at a live spot, NOT a mark
    — the positions are not quoted intraday and nothing here pretends otherwise. Read-only, and
    computed from rows already written, so nothing on the decision path changes to produce it.
    """
    day = day or today()
    rows = positions_for_day(conn, day)
    iterations = conn.execute(
        "SELECT iteration_ts, arm, center, center_reason, underlying_price FROM fly_iterations "
        "WHERE trade_date = ? ORDER BY iteration_ts",
        (day,),
    ).fetchall()
    feed, feed_summary = data_quality(conn, day)

    arms = sorted({r["arm"] for r in rows if r["arm"]} | {r["arm"] for r in iterations if r["arm"]})
    by_arm: dict[str, list] = {a: [] for a in arms}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)

    grouped: dict[str, list] = {}
    for r in iterations:
        grouped.setdefault(r["iteration_ts"], []).append(r)

    ticks = []
    for ts in sorted(grouped):
        entries = grouped[ts]
        spots = [e["underlying_price"] for e in entries if e["underlying_price"] is not None]
        spot = spots[0] if spots else None
        settle_now = {}
        if spot is not None:
            for arm in arms:
                states = [s for s in (_state_at(dict(p), ts) for p in by_arm.get(arm, [])) if s]
                if states:
                    settle_now[arm] = _round(fly.book_pnl(states, spot))
        ticks.append(
            {
                "ts": ts,
                "spot": _round(spot),
                "centers": {e["arm"]: e["center"] for e in entries if e["center"] is not None},
                "reasons": {e["arm"]: e["center_reason"] for e in entries if e["center_reason"]},
                "settle_now": settle_now,
            }
        )

    events, spans = [], []
    for r in rows:
        if r.get("entry_time"):
            events.append(
                {
                    "kind": "entry",
                    "ts": r["entry_time"],
                    "arm": r["arm"],
                    "entry_mode": r["entry_mode"],
                    "position_id": r["position_id"],
                    "center": r["center"],
                    "spot": r["underlying_at_entry"],
                    "structure": _entry_structure_label(r["entry_mode"], r["side"]),
                }
            )
        if r.get("completed_at"):
            events.append(
                {
                    "kind": "completion",
                    "ts": r["completed_at"],
                    "arm": r["arm"],
                    "entry_mode": r["entry_mode"],
                    "position_id": r["position_id"],
                    "center": r["center"],
                    "spot": r["spot_at_completion"],
                    "structure": "iron fly" if r["kind"] == "iron_fly" else "fly",
                }
            )
            drift = (
                None
                if r["spot_at_completion"] is None or r["underlying_at_entry"] is None
                else abs(r["spot_at_completion"] - r["underlying_at_entry"])
            )
            spans.append(
                {
                    "position_id": r["position_id"],
                    "arm": r["arm"],
                    "center": r["center"],
                    "from": r["entry_time"],
                    "to": r["completed_at"],
                    "latency_min": r["completion_latency_min"],
                    "drift": _round(drift, 1),
                }
            )
    events.sort(key=lambda e: e["ts"])

    # Credit spreads still waiting: the branch that carries full defined risk until it completes, and
    # the one a time axis makes visible while it is still happening rather than at settlement.
    waiting = [
        {
            "position_id": r["position_id"],
            "arm": r["arm"],
            "center": r["center"],
            "from": r["entry_time"],
            "best_debit": r["best_completing_debit"],
            "credit": r["credit"],
        }
        for r in rows
        if r["entry_mode"] == "legged" and not r.get("completed_at")
    ]

    return {
        "date": day,
        "arms": arms,
        "ticks": ticks,
        "events": events,
        "spans": spans,
        "waiting": waiting,
        "feed": feed,
        "feed_summary": feed_summary,
    }


def data_quality(conn, day: str | None = None) -> tuple[list[dict], dict]:
    """What the feed gave us this session — the record that separates 'the data was thin' from 'the
    strategy found nothing', which is a distinction this module is built to keep.

    Returns the per-tick series (every snapshot attempt, whether it built or was refused) and a
    summary counting refusals by reason. A stretch of the day with refused rows is a feed problem; a
    stretch with NO rows at all is the loop not running — two different findings, and only separable
    because the refused tick is now recorded rather than logged and forgotten.
    """
    day = day or today()
    rows = conn.execute(
        "SELECT iteration_ts, symbol, status, quotes_fresh, quotes_rejected, underlying_price "
        "FROM fly_snapshots WHERE trade_date = ? ORDER BY iteration_ts",
        (day,),
    ).fetchall()

    feed = [
        {
            "ts": r["iteration_ts"],
            "symbol": r["symbol"],
            "status": r["status"],
            "fresh": r["quotes_fresh"],
            "rejected": r["quotes_rejected"],
            "spot": _round(r["underlying_price"]),
        }
        for r in rows
    ]

    by_reason: dict[str, int] = {}
    ok = 0
    for r in rows:
        if r["status"] == "ok":
            ok += 1
        else:
            by_reason[r["status"]] = by_reason.get(r["status"], 0) + 1
    summary = {
        "ticks": len(rows),
        "ok": ok,
        "refused": len(rows) - ok,
        "by_reason": by_reason,
        "ok_rate": _rate(ok, len(rows)),
    }
    return feed, summary


# --------------------------------------------------------------------------- rollup
def today() -> str:
    """The ET session date, not the machine's local date. West of Eastern the local calendar day is
    still yesterday well after the ET date has rolled, so a local date would ask for the wrong
    session's rows on any evening read."""
    return clock.today_iso()


def session_overview(conn, day: str | None = None, arm: str | None = None, symbol: str | None = None) -> dict:
    """Everything the Today view and the section card need, in one call.

    `arm`/`symbol` narrow every figure here (books, positions, and the derived counts/stats/
    completion) to the selected scope — the same filter the payoff curve and the History/
    Performance views already apply, so switching either selector can't leave one card telling a
    different story than the rest of the page.
    """
    day = day or today()
    books = books_for_day(conn, day, arm, symbol)
    positions = positions_for_day(conn, day, arm, symbol)
    open_positions = [p for p in positions if p["status"] == "open"]
    flies = [p for p in positions if p["kind"] == "fly"]
    return {
        "date": day,
        "books": books,
        "positions": positions,
        "open_count": len(open_positions),
        "fly_count": len(flies),
        "risk_free_count": len([p for p in flies if p["risk_free"]]),
        # The worst realistic dollar loss across every still-open position, net of trading fees
        # AND the worst-case $5/contract exercise-assignment fee (as if every leg finished ITM --
        # see fly.position_floor). A position whose own floor is already non-negative contributes
        # nothing here (it cannot become a loss), so this is genuinely "how much could this book
        # still lose," not a raw sum of every floor regardless of sign.
        "max_possible_loss": round(sum(min(0.0, p.get("floor_dollars") or 0.0) for p in open_positions), 2),
        "stats": stats_for_period(conn, day, day, arm=arm, symbol=symbol),
        "completion": completion_stats(conn, day, day, symbol=symbol, arm=arm),
        "divergence": arm_divergence(conn, day),
        "journal": decision_journal(conn, day, arm),
    }


# --------------------------------------------------------------------------- band placement
# Requested by the advisor on 2026-08-18 (#28), repeated 08-19 and 08-20, and sharpened on 08-21
# (#54) after its own classifier failed. The short version of four proposals: a butterfly's floor
# holding is not primarily a property of the structure, it is the joint event of a band placement
# and a realized range — so scoring arms on floor_holds alone credits a wide band on a quiet day and
# blames a tight band on a fast one. Every established arm wins 64-77% of the time and loses money
# lifetime, which is what you get when the thing being optimised is not the thing that decides the
# tail.
#
# The 08-21 sharpening is what makes it correct, and it came from the wing experiment contradicting
# the original metric. `band_low - session_low` classified held-versus-failed perfectly for four
# sessions; then advised:control (wing_width_strikes=2) placed its lower edge 45.06 points below the
# session low — more than twice control's margin — and failed anyway, because the narrow wing had
# pulled the UPPER edge to 7697 against a 7697.11 high. Price traded 0.11 points through it. A rule
# reading only the lower edge predicts that book to hold.
#
# So the metric is the MINIMUM of the two edge margins, and the label is which edge bound.

# Trading days per year, for turning an annualised vol quote into a one-session expected move.
_SESSIONS_PER_YEAR = 252


def session_ranges_from_cache(cache_conn, sessions, symbols=("SPX",), vol_symbol="VIX1D") -> dict:
    """Session high/low and the ex-ante vol quote, read from the shared stream cache.

    Keyed by **(trade_date, symbol)**, not by date alone, and that is not defensive — this module
    has traded both SPX and XSP, which are the same index at a tenth the notional. Keying on the
    date alone scored the 2026-07-29..07-31 XSP books (bands around 731) against SPX's range (around
    7690) and produced binding margins of ~6,700 points, which read as six confident classifier
    failures until the numbers were looked at.

    Separated from `band_placement` so that function stays a pure read over the ledger it is given,
    and so a test can supply ranges without a cache fixture. This is the only part needing a second
    store: the realized range is genuinely not in the flies ledger.

    **The cache's `day_high`/`day_low`, not this module's own tick record**, and the difference is
    not academic. `fly_iterations` samples the underlying each tick, so it observed 7697.01 as the
    2026-08-21 high where the feed's session high was 7697.11 — and the breach that day was 0.11
    points. Scoring the binding edge off sampled ticks would have read that book as HELD, the
    opposite of what happened. A sampled extreme cannot measure a margin finer than its own
    sampling error.
    """
    sessions, symbols = list(sessions), list(symbols)
    if not sessions or not symbols:
        return {}
    smarks, ymarks = ",".join("?" * len(sessions)), ",".join("?" * len(symbols))
    out: dict = {}
    for row in cache_conn.execute(
        "SELECT trade_date, symbol, day_high, day_low FROM stream_summary"
        f" WHERE symbol IN ({ymarks}) AND trade_date IN ({smarks})",
        [*symbols, *sessions],
    ):
        out[(row[0], row[1])] = {"session_high": row[2], "session_low": row[3], "vix1d": None}
    # One vol quote per session, shared by every symbol: SPX and XSP are the same index, so the same
    # one-day implied move applies to both once it is scaled by each band's own centre.
    vol = {
        row[0]: row[1]
        for row in cache_conn.execute(
            f"SELECT trade_date, day_close FROM stream_summary WHERE symbol = ? AND trade_date IN ({smarks})",
            [vol_symbol, *sessions],
        )
    }
    for (session, _symbol), entry in out.items():
        entry["vix1d"] = vol.get(session)
    return out


def band_placement(conn, session_ranges: dict, start=None, end=None, arm=None, symbol=None) -> list[dict]:
    """Per book: where its band sat relative to the range the session actually printed.

    `session_ranges` maps **(trade_date, symbol)** -> {"session_high", "session_low", "vix1d"} (see
    `session_ranges_from_cache`). A book whose (session, symbol) is absent yields a row with null
    margins rather than being dropped: "the range was not recorded" and "the band was badly placed"
    are different facts, and the second must not absorb the first.

    Margins are signed so POSITIVE MEANS SAFE on both edges:
      low_margin  = session_low - band_low     (the lower edge sat this far below the day's low)
      high_margin = band_high - session_high   (the upper edge sat this far above the day's high)
    `binding_margin` is the smaller of the two and `binding_edge` names it. Negative means price
    traded through that edge.

    Two normalisations, answering different questions. Against the session's realized range, which
    is ex-post and says how close this band came on this tape. Against the expected move implied by
    the session's VIX1D, which was knowable at entry and is the only one that can separate "this arm
    places wider bands" from "this arm got quieter days" — the question the original proposal was
    actually asking, and the one floor_holds cannot answer either way.

    `floor_holds` is carried through unchanged so the classifier can be SCORED against the outcome
    rather than assumed to predict it.
    """
    clauses, params = ["1=1"], []
    if start:
        clauses.append("trade_date >= ?")
        params.append(start)
    if end:
        clauses.append("trade_date <= ?")
        params.append(end)
    if arm and arm != "ALL":
        clauses.append("arm = ?")
        params.append(arm)
    if symbol and symbol != "ALL":
        clauses.append("symbol = ?")
        params.append(symbol)
    # fly_books, not fly_positions: a band is a property of the book, and so is floor_holds. Its
    # status vocabulary is its own, so `_period_clause`'s settled filter does not apply here.
    rows = conn.execute(
        "SELECT trade_date, arm, symbol, book_id, band_low, band_high, floor_holds,"
        " unbounded_below, worst, settlement_price, status FROM fly_books"
        f" WHERE {' AND '.join(clauses)} ORDER BY trade_date, arm",
        params,
    ).fetchall()

    out = []
    for r in rows:
        row = dict(r)
        rng = session_ranges.get((row["trade_date"], row["symbol"])) or {}
        high, low, vix1d = rng.get("session_high"), rng.get("session_low"), rng.get("vix1d")
        band_low, band_high = row["band_low"], row["band_high"]

        entry = {
            "session": row["trade_date"],
            "arm": row["arm"],
            "symbol": row["symbol"],
            "book_id": row["book_id"],
            "band_low": band_low,
            "band_high": band_high,
            "session_low": low,
            "session_high": high,
            "session_range": _round(high - low) if (high is not None and low is not None) else None,
            "floor_holds": row["floor_holds"],
            "unbounded_below": row["unbounded_below"],
            "worst": row["worst"],
            "settlement_price": row["settlement_price"],
            "settled": row["status"] == "settled",
            # Price crossed an edge intraday and settled back inside the band. Recorded because a
            # band margin is a TOUCH measure — it says whether price reached an edge, not where the
            # session ended — and the two come apart exactly here.
            "touched_but_recovered": None,
            "low_margin": None,
            "high_margin": None,
            "binding_margin": None,
            "binding_edge": None,
            "binding_margin_range_frac": None,
            "expected_move": None,
            "binding_margin_expected_moves": None,
        }
        if band_low is None or band_high is None or high is None or low is None:
            out.append(entry)
            continue

        low_margin = low - band_low
        high_margin = band_high - high
        binding = min(low_margin, high_margin)
        entry["low_margin"] = _round(low_margin)
        entry["high_margin"] = _round(high_margin)
        entry["binding_margin"] = _round(binding)
        entry["binding_edge"] = "low" if low_margin <= high_margin else "high"

        settle = row["settlement_price"]
        if settle is not None:
            entry["touched_but_recovered"] = bool(binding < 0 and band_low <= settle <= band_high)

        session_range = high - low
        if session_range > 0:
            entry["binding_margin_range_frac"] = _round(binding / session_range, 4)
        if vix1d:
            # A one-session move implied by an annualised vol quote, taken at the band's own centre
            # so both edges are measured against the same yardstick.
            centre = (band_low + band_high) / 2
            expected = centre * (vix1d / 100.0) / (_SESSIONS_PER_YEAR**0.5)
            entry["expected_move"] = _round(expected)
            if expected > 0:
                entry["binding_margin_expected_moves"] = _round(binding / expected, 4)
        out.append(entry)
    return out


def band_placement_classifier(placements: list[dict]) -> dict:
    """How often the binding margin agrees with `floor_holds`, for both rules, on the same rows.

    This exists rather than leaving the caller to eyeball the table because of how the request
    arrived: the original classifier (`band_low - session_low`) fit two sessions perfectly and was
    reported on four before the fifth broke it. A rule fit to a handful of days is a hypothesis, and
    the honest way to carry one is with its agreement rate AND its disagreements listed, so the
    session that breaks it is visible rather than absorbed.

    **Read "agreement", not "prediction", and the distinction is not pedantic.** `floor_holds` is
    `worst >= 0` over the payoff scan grid (`fly.book_floor`) — a property of the STRUCTURE, true or
    false before the session prints a single tick. The binding margin is a property of the structure
    AND the tape. So part of any agreement here is mechanical: a book that is non-negative
    everywhere has a band spanning its whole scan grid, which will usually contain the day's range.
    The overlap is real but not total — on the live ledger, holding books span 30-315 points of band
    and failing ones 4-205 — so the number is informative and it is NOT an out-of-sample hit rate.

    What the comparison DOES establish cleanly is the 08-21 proposal's own claim: that reading both
    edges classifies books the one-edge rule gets wrong. Both rules are scored over identical rows,
    so their difference is the rule and nothing else. Asserting that without measuring it would
    repeat the error that produced the original.
    """
    # Settled books only. An open book's `floor_holds` is a default rather than a result, and
    # scoring against it counts a session that has not happened yet as a classifier miss — which is
    # what the 2026-08-26 advised:control row did before this filter.
    scored = [
        p
        for p in placements
        if p["binding_margin"] is not None and p["floor_holds"] is not None and p["settled"]
    ]
    if not scored:
        return {"scored": 0, "two_edge": None, "low_edge_only": None, "disagreements": []}

    def agreement(key):
        hits = sum(1 for p in scored if (p[key] > 0) == bool(p["floor_holds"]))
        return {"agree": hits, "of": len(scored), "rate": _rate(hits, len(scored))}

    return {
        "scored": len(scored),
        # The 08-21 rule: the smaller of the two edge margins.
        "two_edge": agreement("binding_margin"),
        # The 08-18 rule it replaces, on the same rows, so the improvement is measured not asserted.
        "low_edge_only": agreement("low_margin"),
        # Books where price crossed an edge and settled back inside. Counted separately because it
        # is the most common way the two measures come apart honestly — and on the live ledger all
        # four residual disagreements are of this kind. Note it is NOT confined to disagreements: a
        # book can be touched, recover, and still fail floor_holds, which is negative-somewhere
        # rather than negative-at-settlement.
        "touched_but_recovered": sum(1 for p in scored if p["touched_but_recovered"]),
        "disagreements": [
            {
                "session": p["session"],
                "arm": p["arm"],
                "floor_holds": p["floor_holds"],
                "binding_margin": p["binding_margin"],
                "binding_edge": p["binding_edge"],
                "low_margin": p["low_margin"],
                "high_margin": p["high_margin"],
                "settlement_price": p["settlement_price"],
                "touched_but_recovered": p["touched_but_recovered"],
            }
            for p in scored
            if (p["binding_margin"] > 0) != bool(p["floor_holds"])
        ],
        "binding_edge_counts": {
            edge: sum(1 for p in scored if p["binding_edge"] == edge) for edge in ("low", "high")
        },
    }
