"""Read-only query layer over the paper database — ported from
packages/flies/src/cherrypick/flies/analytics.py's shape (same `_period_clause` chokepoint,
`by_arm`, `by_regime`, `regime_coverage`), adapted to MEIC's schema.

Nothing here writes, trades, or reaches the network. Every function takes an open connection.

**`ic_trades.pnl` is GROSS, not net** — the one schema difference from flies worth stating up
front. flies stores `gross_pnl` and `pnl` (net) as separate columns; MEIC stores only `pnl`
(gross — see `_apply_exit_decision`'s `delta_pnl`, which never subtracts a fee) and `fees`
separately. Every function here computes net as `pnl - fees` at read time, matching the win/loss
definition `dashboard._stats_for_period` already uses (its own `net_pnl` accumulator does NOT
subtract fees, which is a pre-existing inconsistency in that module, not a convention to copy —
see that file's TODO when it's next touched).

**Win rate is per IC, net of fees** — one resolved trade, one verdict. Matches
`db._range_stats_for_rows` and the orchestrator's calibrate reading.

**Sessions are reported beside trades everywhere here.** Under overlap_scope 'none' a single
session can carry ~100+ entries from one stream, and the measured intraclass correlation (P&L
~0.06, win-indicator ~0.24) means the effective sample size is close to the SESSION count, not the
trade count — see session_bootstrap. A table that reports "n=400" without a session count reads as
far more evidence than it is.
"""

from __future__ import annotations

# Every P&L query filters to a resolved trade. An open position is not a result yet, and counting
# it as one would flatter whichever stream happens to be holding something at read time.
_RESOLVED = "status IN ('stopped', 'expired', 'force_closed')"

# Sampling era (see db.py's `era` column / _migrate). THE ONE PYTHON LITERAL — db.py's save path
# stamps every new row with this constant, and the console's readers/meic.ts hand-syncs it.
# Three eras: 'advisor' (2026-08-21 →) — the advisor designs and runs every experiment, hand-built
# variant arms retired at the cutover; 'sample' (2026-08-07..2026-08-20) — the hand-designed
# arms/uncapped-sampling window; 'book' (pre-2026-08-07) — the profile-ladder era, an order of
# magnitude different selection intensity (max_concurrent_ics/entry spacing bounded each
# portfolio). Pooling across any pair reads as one book when it is really two incomparable ones.
# Pass era="ALL" for an explicit cross-era read; a specific era name for that window alone.
CURRENT_ERA = "advisor"


def _round(value, digits=2):
    return None if value is None else round(value, digits)


def _rate(numerator, denominator, digits=4):
    return round(numerator / denominator, digits) if denominator else None


def _period_clause(start=None, end=None, arm=None, symbol=None, era=CURRENT_ERA):
    """The shared WHERE every read surface in this module builds on.

    `arm` filters `risk_profile` — MEIC has no separate `arm` column (see the Phase 2 design
    note: the stream/arm tag IS `risk_profile`, the same column every existing reader —
    orchestrator report/calibrate, dashboard.py, section.py — already groups on). `era="ALL"`
    disables the era filter for an explicit cross-era read; any other value (including the
    CURRENT_ERA default) filters to exactly that era.
    """
    clause, params = [_RESOLVED], []
    if era and era != "ALL":
        clause.append("era = ?")
        params.append(era)
    if start:
        clause.append("trade_date >= ?")
        params.append(start)
    if end:
        clause.append("trade_date <= ?")
        params.append(end)
    if arm and arm != "ALL":
        clause.append("risk_profile = ?")
        params.append(arm)
    if symbol and symbol != "ALL":
        clause.append("symbol = ?")
        params.append(symbol)
    return " AND ".join(clause), params


def _summarize(rows) -> dict:
    """rows need `pnl` (gross), `fees`, and `trade_date` columns at minimum."""
    gross = sum((r["pnl"] or 0.0) for r in rows)
    fees = sum((r["fees"] or 0.0) for r in rows)
    nets = [(r["pnl"] or 0.0) - (r["fees"] or 0.0) for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    total_win, total_loss = sum(wins), abs(sum(losses))
    sessions = len({r["trade_date"] for r in rows if r["trade_date"]})
    return {
        "trades": len(rows),
        "sessions": sessions,
        "gross_pnl": _round(gross),
        "fees": _round(fees),
        "net_pnl": _round(sum(nets)),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _rate(len(wins), len(wins) + len(losses)),
        "avg_pnl": _round(sum(nets) / len(nets)) if nets else None,
        "avg_win": _round(total_win / len(wins)) if wins else None,
        "avg_loss": _round(-total_loss / len(losses)) if losses else None,
        "fee_drag_pct": _round(fees / gross * 100) if gross > 0 else None,
        "profit_factor": _round(total_win / total_loss) if total_loss > 0 else None,
    }


def stats_for_period(conn, start=None, end=None, arm=None, symbol=None, era=CURRENT_ERA) -> dict:
    where, params = _period_clause(start, end, arm, symbol, era)
    rows = conn.execute(f"SELECT pnl, fees, trade_date FROM ic_trades WHERE {where}", params).fetchall()
    return _summarize(rows)


def by_arm(conn, start=None, end=None, symbol=None, era=CURRENT_ERA) -> list[dict]:
    """Per-stream comparison — the module's headline output. A blended total would hide the
    only contrast the forward test is designed to draw."""
    where, params = _period_clause(start, end, symbol=symbol, era=era)
    rows = conn.execute(
        f"SELECT risk_profile, pnl, fees, trade_date FROM ic_trades WHERE {where}", params
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["risk_profile"] or "unassigned", []).append(r)
    out = [{"arm": arm, **_summarize(rs)} for arm, rs in grouped.items()]
    return sorted(out, key=lambda x: x["net_pnl"] or 0, reverse=True)


# --------------------------------------------------------------------------- regime conditioning
# The dimensions regime.classify_regime tags, and the continuous measure recorded beside each. See
# regime.py's module docstring for why the float is stored: it lets a threshold be re-derived from
# history at analysis time instead of re-guessed and re-run.
REGIME_DIMENSIONS = {
    "vol_implied": ("entry_vol_implied_bucket", "entry_vol_implied_value"),
    "vol_event": ("entry_vol_event_bucket", "entry_vol_event_value"),
    "vol_realized": ("entry_vol_realized_bucket", "entry_vol_realized_value"),
    "vol_intraday": ("entry_vol_intraday_bucket", "entry_vol_intraday_value"),
    "gex": ("entry_gex_bucket", "entry_gex_value"),
    "skew": ("entry_skew_bucket", "entry_skew_value"),
    "center_offset": ("entry_center_offset_bucket", "entry_center_offset_value"),
    "trend": ("entry_trend_bucket", "entry_trend_value"),
}


# The session count below which a dimension cannot support a threshold re-cut. Deliberately the SAME
# number as experiment.MIN_SESSIONS_FOR_INTERVAL (14) rather than a second one invented here: both
# answer "how many sessions before this book may draw a conclusion", and two constants for one
# question is how they start disagreeing. Not imported from experiment.py because that module pulls
# in paths/config and this one is a pure read layer over an open connection — the coupling is stated
# here and pinned by a test instead.
#
# The bar's own provenance is experiment.py's: PROMOTION_RULE.min_days is 14 and this module's docs
# put a regime-level claim at 14-20 sessions. Raise it, don't lower it, if a re-cut made on a sample
# this size later fails to hold.
MIN_EFFECTIVE_N = 14

# How small within-session movement has to be, relative to movement BETWEEN sessions, before a
# dimension is called daily-scale. Not a test for a constant: a daily-scale input still wobbles
# slightly within a day because it is normalized by spot (5-day ATR / spot moves whenever spot does
# — measured at 0.00005 within a session against 0.00154 across them, a real ratio of 0.03), so a
# strict equality test would never fire and would report every dimension as intraday.
DAILY_SCALE_RATIO = 0.2


def _session_scale(conn, table: str, where: str, params: list, bucket_col: str, value_col: str):
    """(sessions, daily_scale) for one regime dimension: how many distinct sessions its tagged rows
    came from, and whether its recorded float essentially only moves BETWEEN them.

    Daily-scale means the row count overstates the independent draws by roughly the number of
    entries per session — this book takes hundreds — so `regime_coverage` reports sessions as the
    effective n instead. Measured as (mean within-session range) / (range of session means) against
    DAILY_SCALE_RATIO, rather than declared per dimension, so it follows what the data actually did:
    a dimension can be daily-scale in one period and intraday in another, and hardcoding a list
    would keep asserting the first after the second became true.

    Degrades to daily_scale=False on a single session or a flat across-session range — with one
    session there is no between-session movement to compare against, and claiming daily-scale from
    that would be reading a denominator of zero as evidence.
    """
    rows = conn.execute(
        f"SELECT trade_date, MAX({value_col}) - MIN({value_col}) AS spread, AVG({value_col}) AS mean "
        f"FROM {table} WHERE {where} AND {bucket_col} IS NOT NULL AND {value_col} IS NOT NULL "
        f"GROUP BY trade_date",
        params,
    ).fetchall()
    sessions = (
        conn.execute(
            f"SELECT COUNT(DISTINCT trade_date) FROM {table} WHERE {where} AND {bucket_col} IS NOT NULL",
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
    symbol=None,
    era=CURRENT_ERA,
    arm=None,
    bucket_edges: list[float] | None = None,
) -> list[dict]:
    """Outcomes grouped by the regime the entry was tagged with. Same stat bundle as `by_arm`,
    deliberately — a regime slice and an arm slice have to be read against each other.

    `dimension` is one of REGIME_DIMENSIONS. By default groups on the stored bucket string; pass
    `bucket_edges` to re-bucket the recorded float instead — the whole reason the float is stored
    alongside the bucket (e.g. `bucket_edges=[0.30, 0.60]` re-cuts vol_implied without re-running
    a single session).

    `arm` optionally scopes to one stream — regime-vs-arm is usually the more informative cut than
    either alone (e.g. "how does `open` do specifically in the vol_realized='high' bucket"). Pass
    None for every stream blended, which mixes populations with different entry gates and should
    be read cautiously.

    **Read `regime_coverage` before trusting this table.** A dimension whose tagged rows all land
    in one bucket is reporting no contrast, not no effect.
    """
    if dimension not in REGIME_DIMENSIONS:
        raise ValueError(f"by_regime: unknown dimension {dimension!r} (have {sorted(REGIME_DIMENSIONS)})")
    bucket_col, value_col = REGIME_DIMENSIONS[dimension]

    where, params = _period_clause(start, end, arm, symbol, era)
    rows = conn.execute(
        f"SELECT {bucket_col} AS bucket, {value_col} AS value, pnl, fees, trade_date "
        f"FROM ic_trades WHERE {where}",
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
                "bucket": bucket,
                "value_min": _round(min(values), 4) if values else None,
                "value_max": _round(max(values), 4) if values else None,
                # `sessions` per bucket comes from _summarize, which already computes it for every
                # read surface in this module — a bucket of 600 rows drawn from one day is one draw
                # dressed as six hundred, and the trades count alone cannot show that. See
                # regime_coverage's effective_n for the same accounting per dimension.
                **_summarize(rs),
            }
        )
    return sorted(out, key=lambda x: x["net_pnl"] or 0, reverse=True)


def regime_coverage(conn, start=None, end=None, symbol=None, era=CURRENT_ERA, arm=None) -> dict:
    """How much of the resolved book is regime-tagged at all, per dimension, and whether any
    dimension is DEGENERATE (every tagged row in one bucket) — the honesty guard on `by_regime`.

    Reports `untagged` (NULL bucket — the column was never written for this row, e.g. it predates
    regime tagging) SEPARATELY from a non-null-but-degenerate bucket distribution. Folding the two
    together would misdiagnose a coverage gap (the gex dimension: 73% of rows untagged because the
    GEX surface was unavailable at entry) as a gate that never fired, when the two call for
    different fixes — the first needs better instrumentation, the second needs a different arm.

    **Reports `effective_n` in SESSIONS, not rows, for any dimension whose input only moves daily.**
    Rows are not draws. Under uncapped independent sampling this book takes hundreds of entries per
    session, so a dimension fed by a daily-scale input (IV rank, trailing ATR) has one value repeated
    across every row of a day: 967 rows of `vol_implied` measured on 2026-08-07 and 08-10 is n=2, and
    reading it as n=967 is how a two-day sample came to look like a month's evidence. Detected rather
    than declared — `daily_scale` is true when the recorded float never varies WITHIN a session, so
    it follows what the data did instead of a hardcoded list of which dimensions are slow.

    `underpowered` is the flag to act on: too few independent draws to cut a threshold against,
    whatever the row count says. A degenerate dimension and an underpowered one look identical in a
    bucket table and call for opposite responses — re-cut the float, versus collect more sessions.
    """
    where, params = _period_clause(start, end, arm, symbol, era)
    total = conn.execute(f"SELECT COUNT(*) FROM ic_trades WHERE {where}", params).fetchone()[0]
    out = {"resolved_trades": total, "dimensions": {}}
    for dim, (bucket_col, value_col) in REGIME_DIMENSIONS.items():
        rows = conn.execute(
            f"SELECT {bucket_col} AS bucket, COUNT(*) AS n FROM ic_trades WHERE {where} "
            f"AND {bucket_col} IS NOT NULL GROUP BY 1",
            params,
        ).fetchall()
        counts = {r["bucket"]: r["n"] for r in rows}
        tagged = sum(counts.values())

        sessions, daily_scale = _session_scale(conn, "ic_trades", where, params, bucket_col, value_col)
        effective_n = sessions if daily_scale else tagged

        out["dimensions"][dim] = {
            "tagged": tagged,
            "untagged": total - tagged,
            "coverage_pct": _round(tagged / total * 100) if total else None,
            "buckets": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "sessions": sessions,
            "daily_scale": daily_scale,
            "effective_n": effective_n,
            # One populated bucket among the TAGGED rows means the tag cannot discriminate — no
            # contrast, not no effect. Computed over tagged rows only, so an untagged majority
            # (a coverage gap) is never mistaken for gate degeneracy.
            "degenerate": tagged > 0 and len(counts) == 1,
            # Keyed on SESSIONS, not effective_n, and deliberately so. Rows inside one session share
            # that session's market, so a threshold cut on two days of intraday-varying data is
            # still a threshold cut on two days — `daily_scale` says how severe the collapse is, not
            # whether it happened. Every dimension in this table is bounded by the session count.
            "underpowered": tagged > 0 and sessions < MIN_EFFECTIVE_N,
        }
    return out


# --------------------------------------------------------------------------- exit detail


def expired_detail(row: dict) -> str:
    """Split the nominal 'expired' outcome into expired_otm / expired_itm using the already-
    recorded settle values — a read-side cut, no new instrumentation. Both the win-rate math and
    the breakeven identity (P(clean OTM expiry) - P(double stop) > fees/credit) depend on
    separating the two: an `(expired, expired)` IC that settled ITM on one side is not the clean
    win the nominal status implies, and pooling it into 'expired' silently drags the clean-win
    average toward zero.
    """
    if row.get("status") != "expired":
        return row.get("status") or "unknown"
    put_settle, call_settle = row.get("put_settle_value"), row.get("call_settle_value")
    if put_settle is None and call_settle is None:
        return "expired_unknown"  # pre-Phase-1e row or settle values never recorded
    itm = (put_settle or 0.0) > 0 or (call_settle or 0.0) > 0
    return "expired_itm" if itm else "expired_otm"


def by_exit_detail(conn, start=None, end=None, symbol=None, era=CURRENT_ERA, arm=None) -> list[dict]:
    """Every resolved trade's outcome, with 'expired' split into expired_otm/expired_itm (see
    expired_detail). stopped/force_closed pass through unchanged — those already carry a real
    per-side mechanism, not a nominal status hiding two different outcomes."""
    where, params = _period_clause(start, end, arm, symbol, era)
    rows = conn.execute(
        f"SELECT status, put_settle_value, call_settle_value, pnl, fees, trade_date "
        f"FROM ic_trades WHERE {where}",
        params,
    ).fetchall()
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(expired_detail(dict(r)), []).append(r)
    out = [{"exit_detail": k, **_summarize(rs)} for k, rs in grouped.items()]
    return sorted(out, key=lambda x: x["net_pnl"] or 0, reverse=True)


def breakeven_scorecard(conn, start=None, end=None, symbol=None, era=CURRENT_ERA, arm=None) -> dict:
    """The breakeven identity per arm: under MEIC's per-side buy-back design, an IC pays exactly
    when `P(both sides expire clean) - P(both sides stop) > fees/credit` — see
    docs/paper-experiments.md's derivation. `margin_pct` reads per session instead of waiting for
    a full bootstrap: positive means the arm is clearing its own fee drag at today's stop policy,
    negative means it is structurally below breakeven, not merely unlucky.

    Reads the put/call leg status PAIR from `ic_spread_legs` (not the IC-level `status` column):
    'stopped' at the IC level also covers a single-side stop (the designed scratch), so only a
    BOTH-stopped pair is the double-stop failure mode this identity is about.

    Returns None fields when there are zero resolved ICs, rather than a divide-by-zero 0.0 that
    would misread as "clears its own bar".
    """
    where, params = _period_clause(start, end, arm, symbol, era)
    rows = conn.execute(
        # `_period_clause`'s bare column names (status, trade_date, ...) are only unambiguous
        # against ic_trades alone, and ic_spread_legs ALSO has a `status` column -- filter to
        # ic_trades FIRST in a subquery, then join, so the WHERE never sees two `status` columns.
        f"""SELECT t.ic_order_id, t.fees, t.net_credit, t.dollar_multiplier,
               MAX(CASE WHEN l.side='put' THEN l.status END) put_status,
               MAX(CASE WHEN l.side='call' THEN l.status END) call_status
            FROM (SELECT * FROM ic_trades WHERE {where}) t
            JOIN ic_spread_legs l ON l.ic_order_id = t.ic_order_id
            GROUP BY t.ic_order_id""",
        params,
    ).fetchall()
    total = len(rows)
    if total == 0:
        return {
            "trades": 0,
            "clean_pct": None,
            "double_stop_pct": None,
            "breakeven_bar_pct": None,
            "margin_pct": None,
        }
    clean = sum(1 for r in rows if r["put_status"] == "expired" and r["call_status"] == "expired")
    double = sum(1 for r in rows if r["put_status"] == "stopped" and r["call_status"] == "stopped")
    avg_fee = sum((r["fees"] or 0.0) for r in rows) / total
    avg_credit_dollars = (
        sum((r["net_credit"] or 0.0) * (r["dollar_multiplier"] or 100.0) for r in rows) / total
    )
    clean_pct = clean / total
    double_pct = double / total
    bar_pct = (avg_fee / avg_credit_dollars) if avg_credit_dollars else None
    margin_pct = (clean_pct - double_pct - bar_pct) if bar_pct is not None else None
    return {
        "trades": total,
        "clean_pct": _round(clean_pct * 100, 1),
        "double_stop_pct": _round(double_pct * 100, 1),
        "breakeven_bar_pct": _round(bar_pct * 100, 1) if bar_pct is not None else None,
        "margin_pct": _round(margin_pct * 100, 1) if margin_pct is not None else None,
    }


# --------------------------------------------------------------------------- stop-policy counterfactual


def stop_counterfactual(
    conn, policy_name: str, start=None, end=None, symbol=None, era=CURRENT_ERA, arm="control"
) -> dict:
    """What `policy_name` (see stop_policies.POLICIES) would have paid across `arm`'s (default
    `open`) recorded rows, vs. what those trades actually realized under their real exit. Every
    row in `arm` runs with per_side_stop_management OFF (see config.risk.json's `open`), so its
    real `pnl` is already the hold-to-settlement/force-close outcome — the derived policies are
    scored against that same real column, not against a THIRD stream's different entries.
    """
    from cherrypick.meic import paper as _paper
    from cherrypick.meic import stop_policies as _sp

    where, params = _period_clause(start, end, arm, symbol, era)
    rows = conn.execute(f"SELECT * FROM ic_trades WHERE {where}", params).fetchall()

    actual_pnl = actual_fees = derived_pnl = derived_fees = 0.0
    put_fired = call_fired = derivable = 0
    for sqlite_row in rows:
        row = dict(sqlite_row)
        out = _sp.derive(
            row, policy_name, fee_one_side=_paper.close_fees_one_side, fee_full_ic=_paper.close_fees_full_ic
        )
        if not out["derivable"]:
            continue
        derivable += 1
        actual_pnl += row.get("pnl") or 0.0
        actual_fees += row.get("fees") or 0.0
        derived_pnl += out["pnl"]
        derived_fees += out["fee"]
        put_fired += int(out["put_fired"])
        call_fired += int(out["call_fired"])

    sessions = len({dict(r)["trade_date"] for r in rows})
    return {
        "policy": policy_name,
        "arm": arm,
        "trades": len(rows),
        "derivable": derivable,
        "sessions": sessions,
        "actual_net_pnl": _round(actual_pnl - actual_fees),
        "derived_net_pnl": _round(derived_pnl - derived_fees),
        "delta": _round((derived_pnl - derived_fees) - (actual_pnl - actual_fees)),
        "put_fired": put_fired,
        "call_fired": call_fired,
    }


def validate_stop_derivation(conn, start=None, end=None, era=CURRENT_ERA, tolerance: float = 0.5) -> dict:
    """The derivation's own validation, wired to the live ledger — see
    stop_policies.validate_against_control's docstring for the reasoning. Run this before trusting
    stop_counterfactual's numbers for a new range; a non-ok result means the derivation (or the
    underlying recorded fields) has drifted from what control's real mechanism produced."""
    from cherrypick.meic import paper as _paper
    from cherrypick.meic import stop_policies as _sp

    where, params = _period_clause(start, end, "control", None, era)
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM ic_trades WHERE {where}", params).fetchall()]
    return _sp.validate_against_control(
        rows,
        fee_one_side=_paper.close_fees_one_side,
        fee_full_ic=_paper.close_fees_full_ic,
        tolerance=tolerance,
    )


# --------------------------------------------------------------------------- the stop curve


def stop_grid(
    conn, start=None, end=None, symbol=None, era=CURRENT_ERA, arm="control", ratios=None
) -> dict:
    """The whole `stop_trigger_ratio` curve, scored over already-recorded paths (proposal #12).

    The bounded stop experiment tests ONE point on this curve and needs 15 sessions to say
    anything. Every point is already answerable from rows the module has: `*_max_cost` says whether
    a threshold would have fired, `*_settle_value` says what the side was worth unstopped, and both
    are recorded for stopped sides too. So this is a read-side derivation — no new instrumentation,
    nothing on the write path, no risk — and one session yields the SHAPE of the curve rather than
    one sampled point.

    Defaults to `open` for the same reason `stop_counterfactual` does, and here it is load-bearing
    rather than conventional: `open` runs with per_side_stop_management off, so its paths run to
    settlement uncensored and every ratio is answerable. On an arm that really stops, ratios above
    where it stopped are `censored` — see `stop_policies.censored_above`. Those are reported, never
    folded into the totals, because a censored point and a point that did not fire look identical
    in a sum and mean opposite things.

    Read `validate_stop_derivation` before trusting these numbers: it re-derives control's REAL
    mechanism from control's own paths and checks it against control's recorded P&L.
    """
    from cherrypick.meic import paper as _paper
    from cherrypick.meic import stop_policies as _sp

    ratios = tuple(ratios or _sp.GRID_RATIOS)
    where, params = _period_clause(start, end, arm, symbol, era)
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM ic_trades WHERE {where}", params).fetchall()]

    points = {
        r: {"ratio": r, "derivable": 0, "censored": 0, "stopped_sides": 0,
            "pnl": 0.0, "fees": 0.0, "capital": 0.0, "capital_known": 0}
        for r in ratios
    }
    for row in rows:
        capital = _sp.capital_at_risk(row)
        scored = _sp.score_grid(
            row, fee_one_side=_paper.close_fees_one_side, fee_full_ic=_paper.close_fees_full_ic,
            ratios=ratios,
        )
        for ratio, out in scored.items():
            bucket = points[ratio]
            if out["censored"]:
                bucket["censored"] += 1
                continue
            if not out["derivable"]:
                continue
            bucket["derivable"] += 1
            bucket["pnl"] += out["pnl"]
            bucket["fees"] += out["fee"]
            bucket["stopped_sides"] += int(out["put_fired"]) + int(out["call_fired"])
            if capital is not None:
                bucket["capital"] += capital
                bucket["capital_known"] += 1

    curve = []
    for ratio in ratios:
        b = points[ratio]
        n = b["derivable"]
        net = b["pnl"] - b["fees"]
        # on_max_risk only when capital is known for EVERY scored row -- a return computed over a
        # subset of the capital is not this arm's return, it is a different arm's.
        full_capital = n > 0 and b["capital_known"] == n and b["capital"] > 0
        curve.append({
            "ratio": ratio,
            "derivable": n,
            "censored": b["censored"],
            "net_pnl": _round(net),
            "fees": _round(b["fees"]),
            # Sides stopped out of 2 per derivable trade -- the cost side of a tighter threshold.
            "stop_out_rate": _rate(b["stopped_sides"], n * 2) if n else None,
            "on_max_risk": _round(net / b["capital"], 4) if full_capital else None,
        })

    return {
        "arm": arm,
        "trades": len(rows),
        "sessions": len({r["trade_date"] for r in rows if r.get("trade_date")}),
        "ratios": list(ratios),
        "curve": curve,
        "_note": (
            "Exact for the policy an arm really ran; a MAX-COST PROXY for any other threshold "
            "(stop_policies' module docstring measures ~$2-8/side replay error). Censored points "
            "are excluded from every total rather than counted as not-fired."
        ),
    }


def stop_session_rollup(
    conn, start=None, end=None, symbol=None, era=CURRENT_ERA, arm="control"
) -> list[dict]:
    """Per session: what the book really made, what it would have made with no stop at all, and the
    difference. `stop_cost` positive means the stop COST money that session.

    Per session rather than pooled because a session is one market event — pooling asks the stop
    question of an average day that never happened, and the whole reason this arm exists is that
    the answer is regime-dependent.
    """
    from cherrypick.meic import paper as _paper
    from cherrypick.meic import stop_policies as _sp

    where, params = _period_clause(start, end, arm, symbol, era)
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM ic_trades WHERE {where}", params).fetchall()]

    by_session: dict[str, dict] = {}
    for row in rows:
        session = row.get("trade_date")
        if not session:
            continue
        shadow = _sp.shadow_settle(
            row, fee_one_side=_paper.close_fees_one_side, fee_full_ic=_paper.close_fees_full_ic
        )
        bucket = by_session.setdefault(session, {
            "session": session, "arm": arm, "trades": 0, "derivable": 0, "stops_fired": 0,
            "realized_net": 0.0, "shadow_net": 0.0, "real_fees": 0.0, "shadow_fees": 0.0,
        })
        bucket["trades"] += 1
        bucket["stops_fired"] += int(shadow["stop_fired"])
        bucket["real_fees"] += row.get("fees") or 0.0
        if shadow["realized_net"] is None or shadow["shadow_settle_net"] is None:
            continue
        bucket["derivable"] += 1
        bucket["realized_net"] += shadow["realized_net"]
        bucket["shadow_net"] += shadow["shadow_settle_net"]
        # A held-to-settlement side pays no closing fee, so the shadow book's fees are whatever
        # stop-none itself incurs -- which is 0 when neither side fires.
        none_out = _sp.derive(row, "stop-none", fee_one_side=_paper.close_fees_one_side,
                              fee_full_ic=_paper.close_fees_full_ic)
        bucket["shadow_fees"] += none_out["fee"] or 0.0

    out = []
    for session in sorted(by_session):
        b = by_session[session]
        out.append({
            "session": session,
            "arm": arm,
            "trades": b["trades"],
            "derivable": b["derivable"],
            "stops_fired": b["stops_fired"],
            "realized_pnl_with_stop": _round(b["realized_net"]),
            "shadow_pnl_without_stop": _round(b["shadow_net"]),
            "stop_cost": _round(b["shadow_net"] - b["realized_net"]),
            "fee_delta": _round(b["real_fees"] - b["shadow_fees"]),
        })
    return out


# --------------------------------------------------------------------------- session conditioning


def control_fired(conn, start=None, end=None, symbol=None, era=CURRENT_ERA) -> dict:
    """Per session: did `control` take a single fill, and how many did each other arm take?

    Proposal #6. control/control-drift/sign carry a stricter iv_rank floor than open/width-5/
    width-10, so on a low-IV-rank day control can go completely dark (0 of 297 on 2026-08-14) while
    the looser arms trade and lose. On such a session a width comparison has no same-session
    baseline at all, and a loss could be width, regime, or simply that the looser floor let a trade
    happen on a day control would not have traded.

    BUCKETED, NEVER EXCLUDED. This returns the tag and the counts; scoring code groups by it. A
    session where control was fully gated is a real session with a real result — dropping it would
    be deciding the answer by choosing the sample, which is the failure this module already records
    against reading a structural identity as a finding.
    """
    where, params = _period_clause(start, end, None, symbol, era)
    rows = conn.execute(
        f"SELECT trade_date, risk_profile, COUNT(*) n FROM ic_trades WHERE {where}"
        " GROUP BY trade_date, risk_profile",
        params,
    ).fetchall()

    sessions: dict[str, dict] = {}
    for row in rows:
        r = dict(row)
        bucket = sessions.setdefault(r["trade_date"], {"session": r["trade_date"], "by_arm": {}})
        bucket["by_arm"][r["risk_profile"]] = r["n"]

    out = []
    for session in sorted(sessions):
        by_arm = sessions[session]["by_arm"]
        fired = by_arm.get("control", 0) > 0
        out.append({
            "session": session,
            "control_fired": fired,
            "control_fills": by_arm.get("control", 0),
            "by_arm": by_arm,
            # The arms that traded a day control did not — the ones whose result cannot be read
            # against a same-session baseline.
            "unbaselined_arms": sorted(a for a, n in by_arm.items() if n > 0 and a != "control")
            if not fired else [],
        })

    fired_sessions = [s for s in out if s["control_fired"]]
    return {
        "sessions": out,
        "n_sessions": len(out),
        "n_control_fired": len(fired_sessions),
        "n_control_dark": len(out) - len(fired_sessions),
        "_note": (
            "Bucket on control_fired when scoring width-5/width-10/open against control; never "
            "drop the dark sessions. A session control sat out is evidence about the gate, not a "
            "gap in the width evidence."
        ),
    }


# --------------------------------------------------------------------------- gate ledger


def gate_blocks(conn, trade_date: str, symbol: str | None = None) -> dict:
    """Per-stream block reasons for one session, read from the gate_block loop_log rows (Phase
    1d) — what paper_loop._log_gate_blocks wrote instead of the collapsed 'N skip' display
    string. A zero-entry session becomes a stated result (which gate, on which stream) rather than
    an undifferentiated blank.

    Returns {stream: {reason_or_outcome: count}}. A stream absent from the result entered/blocked
    on no logged iteration this session — a load/scheduling gap, not a quiet gate.
    """
    import json as _json

    rows = conn.execute(
        "SELECT reasoning FROM loop_log WHERE action = 'gate_block' AND loop_date = ? "
        + ("AND symbol = ?" if symbol else ""),
        (trade_date, symbol) if symbol else (trade_date,),
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for (reasoning,) in rows:
        try:
            outcomes = _json.loads(reasoning)
        except (TypeError, ValueError):
            continue
        for stream, outcome in outcomes.items():
            label = _outcome_label(outcome)
            out.setdefault(stream, {}).setdefault(label, 0)
            out[stream][label] += 1
    return out


def _outcome_label(outcome) -> str:
    """Collapse one stream's per-tick outcome string to its gate-ledger label: 'FILL $1.80' /
    'EXIT stop_call' collapse to their verb ('FILL' / 'EXIT'); a bare block reason (e.g.
    'iv_rank_below_floor') passes through unchanged."""
    if isinstance(outcome, str) and outcome.startswith(("FILL", "EXIT")):
        return outcome.split(" ", 1)[0]
    return outcome or "unknown"


# --------------------------------------------------------------------------- arm structural agreement


def arm_divergence(
    conn, stream_a: str, stream_b: str, start=None, end=None, symbol=None, era=CURRENT_ERA
) -> dict:
    """How often `stream_a` and `stream_b` realized the IDENTICAL structure (same put/call short
    strikes) on the same session — the check flies' CLAUDE.md records after reading 100% centre
    agreement across 184 iterations as a finding once: a structural identity between two arms is a
    redundancy to report, not a result to celebrate. Built for width-10 vs control (control's
    widest-first selection on SPX's [5, 10] shortlist means it near-duplicates width-10's own book
    whenever the 10-wide clears every gate).

    This is a same-DAY strike match, not a same-tick match (no shared iteration id exists across
    streams) — a coarser proxy than an exact tick-for-tick comparison, but a same-day exact-strike
    match between two independently-gated streams is already strong evidence of duplication.
    """
    where, params = _period_clause(start, end, symbol=symbol, era=era)
    rows = conn.execute(
        f"SELECT risk_profile, trade_date, put_strike, call_strike FROM ic_trades "
        f"WHERE {where} AND risk_profile IN (?, ?)",
        [*params, stream_a, stream_b],
    ).fetchall()
    by_stream: dict[str, set] = {stream_a: set(), stream_b: set()}
    by_date: dict[str, dict[str, set]] = {}
    for r in rows:
        key = (r["put_strike"], r["call_strike"])
        by_stream[r["risk_profile"]].add(key)
        by_date.setdefault(r["trade_date"], {stream_a: set(), stream_b: set()})[r["risk_profile"]].add(key)

    sessions_with_both = [d for d, s in by_date.items() if s[stream_a] and s[stream_b]]
    overlap_fracs = []
    for d in sessions_with_both:
        a, b = by_date[d][stream_a], by_date[d][stream_b]
        overlap_fracs.append(len(a & b) / len(a | b))

    return {
        "stream_a": stream_a,
        "stream_b": stream_b,
        "sessions_with_both": len(sessions_with_both),
        "avg_strike_overlap_pct": _round(sum(overlap_fracs) / len(overlap_fracs) * 100)
        if overlap_fracs
        else None,
        "all_sessions_identical": bool(overlap_fracs) and all(f == 1.0 for f in overlap_fracs),
    }


# --------------------------------------------------------------------------- session-level inference


def session_bootstrap(
    values_by_session_a: dict, values_by_session_b: dict, *, min_sessions: int = 14, iterations: int = 2000
) -> dict:
    """Session-level bootstrap for comparing two streams — the estimator two-tier cadence in
    docs/paper-experiments.md calls for: gate splits use per-SESSION means (a session with zero
    entries counts as 0, not as missing — an entry filter that blocked a whole session is
    informative, not absent data) and stop/width policies use paired within-session contrasts.

    `values_by_session_a`/`_b`: {trade_date: mean_value_that_session} — the caller decides what
    "value" means (a stream's net P&L that session, its per-entry expectancy, etc.) and whether
    zero-entry sessions are included as 0.0. Resamples the UNION of session dates with replacement
    (bootstrap on the session axis, never the trade axis — trades within a session are correlated,
    sessions are the closer-to-independent unit). Refuses below `min_sessions` sessions shared by
    both, matching experiment.py's MIN_SESSIONS_FOR_INTERVAL convention.

    Date.now()/random.random() are avoided in favor of Python's stdlib `random` module seeded
    per-call by the caller if determinism is needed; this function does not seed globally.
    """
    import random as _random

    shared = sorted(set(values_by_session_a) & set(values_by_session_b))
    if len(shared) < min_sessions:
        return {
            "ok": False,
            "reason": f"only {len(shared)} shared sessions, need >= {min_sessions}",
            "shared_sessions": len(shared),
        }

    observed = sum(values_by_session_a[d] - values_by_session_b[d] for d in shared) / len(shared)
    deltas = []
    for _ in range(iterations):
        sample = [_random.choice(shared) for _ in shared]
        deltas.append(sum(values_by_session_a[d] - values_by_session_b[d] for d in sample) / len(sample))
    deltas.sort()
    lo = deltas[int(0.025 * iterations)]
    hi = deltas[int(0.975 * iterations) - 1]
    return {
        "ok": True,
        "shared_sessions": len(shared),
        "observed_diff": _round(observed, 4),
        "ci_low": _round(lo, 4),
        "ci_high": _round(hi, 4),
        "significant": lo > 0 or hi < 0,
    }


def daily_rollup(conn, day: str, era: str | None = None) -> dict:
    """One session's numbers, in the shape `daily_summary` has always declared and never held.

    Fourteen of that table's columns -- every count, every dollar, the win rate, the average IV rank
    -- were written by nothing. Its two writers (`db.cmd_set_session_init` and
    `db.cmd_save_daily_summary`) set only `session_init_at`, `ai_day_summary` and `closing_nlv`, and
    both are called from the agent-driven `/eod-report` command, which the automated paper loop does
    not run. So the paper table was empty outright, the live table held eight rows of zeros, and the
    console rendered those zeros as a "Daily summaries" card. Same shape as the "No side stops fired"
    bug: a surface reading fields nothing writes, and reporting the absence as data.

    Computed here rather than in `db.py` so it is built on THIS module's own definitions -- `_RESOLVED`
    and the fees-inclusive win test -- and cannot drift from what every other read surface means by
    the same words. That drift is a failure this module has already recorded once, when three call
    sites disagreed about "net".

    Era-scoped like every other reader (default `CURRENT_ERA`), so a roll-up written during one
    sampling era is never a blend of two.

    `gross_credit` is the credit as collected -- `net_credit x quantity x dollar_multiplier` -- not
    the raw per-contract quote, so it is comparable with `gross_pnl` and `fees` in the same row
    rather than being a different unit sitting beside them.
    """
    where, params = _period_clause(era=era or CURRENT_ERA)
    rows = conn.execute(
        f"""SELECT status, pnl, fees, net_credit, quantity, dollar_multiplier,
                   iv_rank_at_entry, session_quality
              FROM ic_trades WHERE {where} AND trade_date = ?""",
        (*params, day),
    ).fetchall()
    resolved = [dict(r) for r in rows]

    # Cancelled rows never reach `_RESOLVED`, so `total_entries` is counted separately: an entry that
    # was placed and cancelled is an entry that happened, and a roll-up that hides it would make the
    # loop look quieter than it was.
    total = conn.execute("SELECT COUNT(*) FROM ic_trades WHERE trade_date = ?", (day,)).fetchone()[0]

    def _num(row, key):
        value = row.get(key)
        return 0.0 if value is None else float(value)

    gross_pnl = sum(_num(r, "pnl") for r in resolved)
    fees = sum(_num(r, "fees") for r in resolved)
    gross_credit = sum(
        _num(r, "net_credit") * (r.get("quantity") or 1) * (r.get("dollar_multiplier") or 1) for r in resolved
    )
    # The module-wide win definition: a RESOLVED trade whose P&L clears its own fees. Gross-positive
    # and fee-negative is a loss here, which is the whole point of stating it in one place.
    wins = sum(1 for r in resolved if _num(r, "pnl") - _num(r, "fees") > 0)
    iv_ranks = [r["iv_rank_at_entry"] for r in resolved if r.get("iv_rank_at_entry") is not None]
    sessions = sorted({r["session_quality"] for r in resolved if r.get("session_quality")})

    return {
        "summary_date": day,
        "total_entries": total,
        "entries_filled": len(resolved),
        "entries_stopped": sum(1 for r in resolved if r["status"] == "stopped"),
        "entries_expired": sum(1 for r in resolved if r["status"] == "expired"),
        "entries_cancelled": total - len(resolved),
        "gross_credit": _round(gross_credit),
        "gross_pnl": _round(gross_pnl),
        "fees": _round(fees),
        "net_pnl": _round(gross_pnl - fees),
        "win_count": wins,
        "win_rate_pct": _round(_rate(wins, len(resolved), 4) * 100 if resolved else None),
        "avg_iv_rank": _round(sum(iv_ranks) / len(iv_ranks), 4) if iv_ranks else None,
        "sessions_entered": sessions,
    }
