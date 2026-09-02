"""Analysis of what the screen rejected and why -- the read side of `scan_log`.

Pure functions over scan_log rows, with `screen_report.py` as the thin CLI on top, the same split
`strategy_metrics.py`/`strategy_report.py` already uses (so a future console surface reads the same
numbers the terminal report prints, and the two can never disagree).

The question this exists to answer is not "how many names were rejected" -- that was always
visible -- but **which threshold is worth moving**. Three things make that answerable:

- A rejection blocked by exactly one gate is the only kind a threshold change can rescue. A name
  failing six gates will still fail five. `sole_blockers` separates them.
- The distance to the bar decides whether a gate is doing its job or mistuned. `scan_log.
  reject_details` carries the measured value and the threshold it missed (see
  `scanner.explain_reject_reasons`); `threshold_distances`/`counterfactual` turn those into "a floor
  of X would have admitted N more names, and here they are".
- A gate that fires constantly but never *alone* is shadowed by another gate and tuning it changes
  nothing. The `sole` column against `total` shows that directly.

Two honesty constraints run through every function here.

**Coverage is reported, never assumed.** `reject_details` only exists on rows written after
2026-08-12; earlier rows carry reason names alone. Every function that depends on measurements
reports how many rows actually had them (`measured_rows` / `coverage`), because a distance analysis
that silently drops 90% of its input reads exactly like one that found nothing.

**A counterfactual counts candidates, not profit.** Loosening a floor admits names, and we have no
outcome for names never traded -- no fill, no P&L, nothing. `counterfactual` deliberately returns
counts and symbols and nothing resembling an expected return.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from cherrypick.earnings import strategy_metrics as _sm

# scan_log rows whose `strategy` is one of these are bookkeeping, not a strategy's verdict:
# "_ranked" is rank_strategies.py's cross-strategy summary row, "_prefilter" the morning snapshot's
# skips. Counting them as screening decisions would double-count every symbol.
PSEUDO_STRATEGIES = ("_ranked", "_prefilter")

STAGE_PREFILTER = "prefilter"
STAGE_SCREEN = "screen"
STAGE_EXECUTION = "execution"

# scan_log has accumulated four incompatible vocabularies, and pooling them produces confidently
# wrong tuning advice -- the reason a row must be CLASSIFIED before it is counted.
#
#   current    outcome 'accepted'/'rejected', reason a "; "-joined list of gate names
#   legacy     outcome 'Reject'/'Near Miss'/'Tier 1'/'Tier 2', from the graded tier ladder that
#              was replaced by a single binary bar. Its `reason` is a bare criterion name
#              ('iv_rv_ratio'), which reads like a gate name but means something else entirely.
#   exit       tier 'close_sweep' -- a position being CLOSED, logged here by the exit path. Its
#              reasons ('close_window', 'time_exit') are exit decisions, not screening ones.
#   retired    a strategy that no longer exists (the undefined-risk ones removed from the suite).
#              Genuine screening decisions, but tuning a threshold for a strategy that cannot
#              trade is wasted effort.
LEGACY_TIER_OUTCOMES = ("Reject", "Near Miss", "Tier 1", "Tier 2")
CURRENT_SCREEN_OUTCOMES = ("accepted", "rejected")

# A close used to be marked by tier='close_sweep'; it is stage='exit' now. Both are recognised,
# because the old marker is the only thing identifying the 24 historical close rows -- which is
# also why `scan_log.tier` is no longer written but still read.
STAGE_EXIT = "exit"
LEGACY_EXIT_TIERS = ("close_sweep",)

# Kept as a literal, the way strategy_report.py keeps STRATEGY_NAMES, rather than importing the
# registry -- a report module should not drag in every strategy's broker-facing machinery.
CURRENT_STRATEGIES = (
    "iron_fly",
    "double_calendar",
    "iron_condor",
    "atm_calendar",
    "directional_credit_spread",
    "broken_wing_butterfly",
)


def classify(row: dict) -> str:
    """Which vocabulary a scan_log row belongs to. See the constants above for why this matters."""
    stage = row.get("stage") or STAGE_SCREEN
    # Stage first: a prefilter row is written under the '_prefilter' pseudo-strategy, so checking
    # the strategy name first would file it as bookkeeping and drop it out of the funnel entirely.
    if stage == STAGE_PREFILTER:
        return "prefilter"
    if row.get("strategy") in PSEUDO_STRATEGIES:
        return "bookkeeping"
    if stage == STAGE_EXIT or row.get("tier") in LEGACY_EXIT_TIERS or row.get("outcome") == "closed":
        return "exit"
    if stage == STAGE_EXECUTION:
        return "execution"
    if row.get("outcome") in LEGACY_TIER_OUTCOMES:
        return "legacy"
    if row.get("strategy") not in CURRENT_STRATEGIES:
        return "retired"
    if row.get("outcome") in CURRENT_SCREEN_OUTCOMES:
        return "screen"
    return "unknown"


def excluded_summary(rows: list[dict]) -> list[dict]:
    """What the screening analysis left out and why -- printed, never silent.

    A report that quietly drops 60% of the table looks identical to one analysing all of it.
    """
    labels = {
        "legacy": "graded tier ladder, replaced by the binary accept/reject bar",
        "exit": "position closes logged to scan_log by the exit path",
        "retired": "strategies removed from the suite",
        "bookkeeping": "_ranked summaries and _prefilter skips",
        "unknown": "outcome in no recognised vocabulary",
    }
    counts: Counter = Counter(classify(r) for r in rows)
    return [
        {"kind": kind, "rows": counts[kind], "note": note}
        for kind, note in labels.items()
        if counts.get(kind)
    ]


def load_scan_rows(
    db_path: Path | str,
    profile: str | None = None,
    strategy: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Every scan_log row, optionally filtered, with `reject_details` parsed.

    Read-only direct SQLite (not the db_paper.py CLI), same as `strategy_metrics.load_closed_trades`
    -- a report runs several of these per invocation.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM scan_log WHERE 1=1"
        params: list = []
        if profile:
            frag, fparams = _sm.book_family_filter(profile)
            query += f" AND {frag}"
            params.extend(fparams)
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if since:
            query += " AND scan_date >= ?"
            params.append(since)
        rows = conn.execute(query + " ORDER BY scan_date, symbol", params).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        r = dict(row)
        raw = r.get("reject_details")
        try:
            r["reject_details"] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            r["reject_details"] = []
        # Rows written before the stage column existed are all screening verdicts -- that was the
        # only stage there was. The migration defaults them the same way.
        r["stage"] = r.get("stage") or STAGE_SCREEN
        out.append(r)
    return out


def split_reasons(row: dict) -> list[str]:
    """The individual gate names in a row's reason string ("a; b; c" -> [a, b, c])."""
    return [p.strip() for p in (row.get("reason") or "").split(";") if p.strip()]


def strategy_rows(rows: list[dict], stage: str | None = None) -> list[dict]:
    """Rows in the CURRENT screening vocabulary, for strategies that still exist.

    `stage` selects 'screen' or 'execution'; omit it for both. Everything else -- the legacy tier
    ladder, exit-path rows, retired strategies, bookkeeping -- is excluded here and accounted for
    by `excluded_summary`, because averaging four vocabularies together yields a number that
    describes none of them.
    """
    kinds = {"screen", "execution"} if stage is None else {stage}
    return [r for r in rows if classify(r) in kinds]


def funnel(rows: list[dict]) -> dict:
    """Counts along calendar -> prefilter -> screen -> execution.

    `execution_recorded` is the honest denominator for the execution half: those rows only exist
    from 2026-08-12, so on older data `accepted` will tower over `opened` with nothing in between --
    which is the very gap the stage column was added to close, not a new one.
    """
    prefiltered = [r for r in rows if classify(r) == "prefilter"]
    screened = strategy_rows(rows, STAGE_SCREEN)
    executed = strategy_rows(rows, STAGE_EXECUTION)

    accepted = [r for r in screened if r.get("outcome") == "accepted"]
    opened = [r for r in executed if r.get("outcome") == "opened"]
    dropped = [r for r in executed if r.get("outcome") == "dropped"]

    return {
        "prefiltered_symbols": len({r["symbol"] for r in prefiltered}),
        "screened_decisions": len(screened),
        "screened_symbols": len({r["symbol"] for r in screened}),
        "accepted": len(accepted),
        "rejected": len(screened) - len(accepted),
        "execution_recorded": len(executed),
        "opened": len(opened),
        "dropped": len(dropped),
        "unexplained_accepted": max(0, len(accepted) - len(executed)),
        "drop_reasons": Counter(r.get("reason") or "unspecified" for r in dropped).most_common(),
    }


def reason_frequency(rows: list[dict]) -> list[dict]:
    """Every gate that fired, with how often it was the ONLY thing blocking the candidate.

    `sole` is the actionable column. A gate with a high `total` and a `sole` of zero never
    independently blocked anything -- it is shadowed by another gate, and moving its threshold would
    change no outcome at all.
    """
    total: Counter = Counter()
    sole: Counter = Counter()
    strategies: defaultdict[str, set] = defaultdict(set)
    for row in strategy_rows(rows, STAGE_SCREEN):
        if row.get("outcome") == "accepted":
            continue
        reasons = split_reasons(row)
        for reason in reasons:
            total[reason] += 1
            strategies[reason].add(row.get("strategy"))
        if len(reasons) == 1:
            sole[reasons[0]] += 1

    return sorted(
        (
            {
                "reason": reason,
                "total": count,
                "sole": sole.get(reason, 0),
                "strategies": len(strategies[reason]),
            }
            for reason, count in total.items()
        ),
        key=lambda d: (-d["sole"], -d["total"], d["reason"]),
    )


def sole_blockers(rows: list[dict]) -> list[dict]:
    """Screen rejections blocked by exactly one gate -- the only ones a threshold change can move.

    Carries the row's measurement when it has one, so the caller can compute distance without going
    back to the ledger.
    """
    out = []
    for row in strategy_rows(rows, STAGE_SCREEN):
        if row.get("outcome") == "accepted":
            continue
        reasons = split_reasons(row)
        if len(reasons) != 1:
            continue
        reason = reasons[0]
        detail = next((d for d in row["reject_details"] if d.get("reason") == reason), None)
        out.append(
            {
                "scan_date": row.get("scan_date"),
                "symbol": row.get("symbol"),
                "strategy": row.get("strategy"),
                "reason": reason,
                "criterion": (detail or {}).get("criterion"),
                "measured": (detail or {}).get("measured"),
                "threshold": (detail or {}).get("threshold"),
                "comparator": (detail or {}).get("comparator"),
            }
        )
    return out


def _is_numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def threshold_distances(rows: list[dict], reason: str) -> dict:
    """How far the sole-blocked candidates for one gate sat from its bar.

    `measured_rows` against `rows` is the coverage caveat made explicit: rows predating
    `reject_details` have the reason but no number, and are counted here rather than quietly
    thinning the sample.
    """
    blocked = [b for b in sole_blockers(rows) if b["reason"] == reason]
    measured = [b for b in blocked if _is_numeric(b["measured"]) and _is_numeric(b["threshold"])]

    ratios = []
    for b in measured:
        threshold = b["threshold"]
        if threshold:
            ratios.append(b["measured"] / threshold)

    return {
        "reason": reason,
        "rows": len(blocked),
        "measured_rows": len(measured),
        "criterion": next((b["criterion"] for b in measured), None),
        "comparator": next((b["comparator"] for b in measured), None),
        "threshold": next((b["threshold"] for b in measured), None),
        "closest": min(ratios, default=None) if ratios and ratios[0] else None,
        "ratios": sorted(ratios),
        "samples": sorted(
            measured,
            key=lambda b: abs((b["measured"] / b["threshold"]) - 1) if b["threshold"] else 0,
        ),
    }


def counterfactual(rows: list[dict], reason: str, threshold: float) -> dict:
    """Which sole-blocked candidates a different bar would have admitted.

    Counts and names only, deliberately. A name that was never traded has no fill, no exit and no
    P&L, so there is nothing here to turn into an expected return -- this sizes how much more the
    screen would have looked at, and stops there. Whether those names were worth trading is a
    question only forward sampling can answer.
    """
    blocked = [
        b
        for b in sole_blockers(rows)
        if b["reason"] == reason and _is_numeric(b["measured"]) and b["comparator"] in ("<", ">")
    ]
    admitted = [
        b
        for b in blocked
        if (b["measured"] >= threshold if b["comparator"] == "<" else b["measured"] <= threshold)
    ]
    return {
        "reason": reason,
        "threshold": threshold,
        "measurable": len(blocked),
        "admitted": len(admitted),
        "symbols": sorted({b["symbol"] for b in admitted}),
        "symbol_nights": len({(b["scan_date"], b["symbol"]) for b in admitted}),
    }


def cooccurrence(rows: list[dict], limit: int = 15) -> list[dict]:
    """Gate pairs that fire on the same rejection, with how often either fires without the other.

    A pair that is always seen together is one finding reported twice: `no_weekly_options` and
    `front_expiration_days_too_far_out` are both just "this name has monthly options only".
    """
    pairs: Counter = Counter()
    singles: Counter = Counter()
    for row in strategy_rows(rows, STAGE_SCREEN):
        if row.get("outcome") == "accepted":
            continue
        reasons = sorted(set(split_reasons(row)))
        for reason in reasons:
            singles[reason] += 1
        for i, a in enumerate(reasons):
            for b in reasons[i + 1 :]:
                pairs[(a, b)] += 1

    out = []
    for (a, b), together in pairs.most_common(limit):
        out.append(
            {
                "a": a,
                "b": b,
                "together": together,
                "a_alone": singles[a] - together,
                "b_alone": singles[b] - together,
            }
        )
    return out


def coverage_gaps(rows: list[dict]) -> list[dict]:
    """`*_unverified` rejections -- where the blocker was our data, not the candidate.

    These are not screening results. A name rejected `iv_rv_ratio_unverified` was never measured
    against the bar at all, and reading it as "failed IV/RV" turns a data-pipeline outage into an
    apparently reasoned decision. That misreading is exactly how the earnings calendar's `when`
    column decayed for three weeks without anyone noticing.
    """
    counts: Counter = Counter()
    symbols: defaultdict[str, set] = defaultdict(set)
    for row in strategy_rows(rows, STAGE_SCREEN):
        for reason in split_reasons(row):
            if reason.endswith("_unverified"):
                counts[reason] += 1
                symbols[reason].add(row.get("symbol"))
    return [
        {"reason": reason, "count": count, "symbols": len(symbols[reason])}
        for reason, count in counts.most_common()
    ]


def load_trade_costs(
    db_path: Path | str,
    profile: str | None = None,
    strategy: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Closed trades reduced to what a cost gate would have judged them on, plus what they made.

    No new column is stored for this: `entry_cost`, `entry_slippage` and `capital_at_risk` are
    already on `trades`, so cost-to-risk is a query over what is recorded rather than a second copy
    to drift. That also makes it retroactive -- it answers for all 64 existing trades, unlike every
    other measurement added this week, which only starts accumulating now.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM trades WHERE closed_at IS NOT NULL AND capital_at_risk > 0"
        params: list = []
        if profile:
            frag, fparams = _sm.book_family_filter(profile)
            query += f" AND {frag}"
            params.extend(fparams)
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if since:
            query += " AND date(opened_at, 'unixepoch', 'localtime') >= ?"
            params.append(since)
        rows = conn.execute(query + " ORDER BY opened_at", params).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        t = dict(row)
        risk = t["capital_at_risk"] or 0
        entry_cost = t["entry_cost"] or 0
        cost = entry_cost + (t["exit_cost"] or 0)
        out.append(
            {
                "symbol": t["symbol"],
                "strategy": t["strategy"],
                "capital_at_risk": risk,
                # The entry-side ratio is what a gate could actually read: it is known when the
                # order is built. The round trip is not -- exit cost depends on the spread hours
                # later, and on the 18 trades with full attribution the exit was often the more
                # expensive side, so doubling the entry would understate rather than bound it.
                "entry_cost_to_risk": entry_cost / risk if risk else None,
                "round_trip_cost_to_risk": cost / risk if risk else None,
                "gross_pnl": t["pnl"] or 0,
                "net_pnl": (t["pnl"] or 0) - cost,
            }
        )
    return out


def cost_gate_counterfactual(trades: list[dict], threshold: float) -> dict:
    """What an entry-side cost-to-risk ceiling would have excluded, and what it cost or saved.

    Unlike `counterfactual`, this one may honestly report P&L: every trade here was actually taken,
    so the excluded set has real outcomes rather than none. `net_pnl_excluded` is what the book
    would have given up (if positive) or avoided (if negative) -- at 14 independent earnings events
    on file, treat the sign as a hint and the magnitude as noise.
    """
    judged = [t for t in trades if t["entry_cost_to_risk"] is not None]
    excluded = [t for t in judged if t["entry_cost_to_risk"] > threshold]
    kept = [t for t in judged if t["entry_cost_to_risk"] <= threshold]
    return {
        "threshold": threshold,
        "judged": len(judged),
        "excluded": len(excluded),
        "kept": len(kept),
        "net_pnl_excluded": round(sum(t["net_pnl"] for t in excluded), 2),
        "net_pnl_kept": round(sum(t["net_pnl"] for t in kept), 2),
        "strategies_excluded": sorted({t["strategy"] for t in excluded}),
    }


def measurement_coverage(rows: list[dict]) -> dict:
    """What fraction of rejections carry the numbers behind their reasons.

    Printed at the top of any distance-based section, because those sections are only as
    trustworthy as this figure -- and it starts near zero, since `reject_details` began on
    2026-08-12 and every earlier row has reason names alone.
    """
    rejections = [r for r in strategy_rows(rows, STAGE_SCREEN) if r.get("outcome") != "accepted"]
    with_details = [r for r in rejections if r["reject_details"]]
    return {
        "rejections": len(rejections),
        "with_details": len(with_details),
        "fraction": (len(with_details) / len(rejections)) if rejections else 0.0,
        "first_detailed_scan": min((r["scan_date"] for r in with_details), default=None),
    }
