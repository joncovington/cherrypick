"""Score a proposed entry screen against what the recorded scans actually saw.

Phase 3 of `docs/control-book-plan.md`, modelled on calendars' `exit_policies.py`. The advisor's
twin cannot express an entry-side change — a looser screen produces a position the control does not
hold, so nothing pairs — which is why entry screening moves to the read side: a wide control plus a
replay that scores proposed screens over recorded outcomes.

**The honesty rule this module exists to carry.** `screen_report --what-if` refuses to report P&L,
because its candidates were never traded and a name with no outcome has no return. Widening the
control lifts that restriction *only for names inside the new bar*; everything outside it stays
counts-and-symbols-only, forever. So the answer is partitioned STRUCTURALLY rather than flagged:
`measured` carries returns, `counterfactual` carries no P&L key at all and cannot grow one by
accident. A single report that mixes measured returns with counterfactual counts and does not say
which is which is worse than two reports.

**Fidelity, not independence.** This calls the scanner's own `apply_soft_criteria` rather than
re-deriving the gates. That is the opposite of the choice `meic.analytics.settlement_audit` makes,
and deliberately so: an audit wants a second implementation so the two can disagree, while a replay
is only useful if it answers exactly what the real scanner would have done. A re-implementation here
would silently stop predicting the scanner the first time a gate changed.

**What the replay can and cannot reconstruct.** Levels (`pass`/`near_miss`/`off`) are recoverable
for any past date, because a level change is journalled in `measurement_breaks` with its old and new
values. The `min_*` THRESHOLDS behind those levels are not journalled anywhere, so the replay applies
today's. `validate()` is what makes that visible rather than silent: it reproduces 550 of 555
recorded decisions, and all five disagreements are 2026-07-20..23 rows carrying an iv_rv_ratio
between 1.00 and 1.20 — below today's `min_iv_rv_ratio` of 1.25 but above the near-miss bar of 1.00,
i.e. rows whose level or threshold was looser then than the break record can show. Every `replay()`
result therefore carries its own `validation`, so an answer cannot be read without the caveat.

**What a proposed screen may change.** Only the five soft criteria and their levels
(`pass`/`near_miss`/`off`). Hard filters — price, weeklies, chain completeness, expiration distance,
liquidity preconditions — are not screen opinions and are not replayable: a row the scanner rejected
on one of those would never have reached an entry under any screen, so it is excluded from the
universe and counted as such rather than silently admitted.
"""

from __future__ import annotations

import datetime
import json
import sqlite3

from cherrypick.earnings import scanner as _scanner

# The five the screen can actually move. Mirrors `scanner._SOFT_CRITERIA`, and a test pins that they
# stay equal — a criterion that became configurable there and not here would be replayed at a bar
# nobody set.
SOFT_CRITERIA = _scanner._SOFT_CRITERIA
LEVELS = ("pass", "near_miss", "off")


def _session_of(epoch: float | None) -> str | None:
    """`trades.opened_at` is EPOCH SECONDS while a scan_date is a local calendar day.

    The same trap `core.ledgers` documents for `closed_at`: SQLite's `date(x,'unixepoch')` is UTC,
    so an evening entry lands on the wrong session and the join silently returns nothing. Converted
    in Python, in local time, which is what the scan_date is in.
    """
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _screen_config(config: dict, levels: dict) -> dict:
    """A strategy sub-config carrying the PROPOSED levels, in the shape the scanner's gate reads."""
    merged = dict(config.get("strategy_defaults") or {})
    merged["_symbol_screen"] = {c: levels.get(c, "pass") for c in SOFT_CRITERIA}
    return merged


def _soft_failures(criteria: dict, config: dict, levels: dict) -> list[str]:
    """Which soft criteria refuse this row at these levels — the scanner's own gate, called."""
    failures: list[str] = []
    _scanner.apply_soft_criteria(criteria, _screen_config(config, levels), failures)
    return failures


# The journalled key under which a screen-level change is recorded. `measurement_breaks` stores the
# old and new level strings verbatim ("winrate=pass,iv_rv_ratio=pass,market_cap=pass"), which is
# what makes the levels in force on a PAST scan date recoverable at all.
SCREEN_BREAK_KEY = "symbol_screen_edge_gates_off"


def _parse_levels(text: str | None) -> dict:
    """`"winrate=pass,iv_rv_ratio=pass"` -> `{"winrate": "pass", ...}`. Unknown names are ignored
    rather than guessed at."""
    out = {}
    for part in (text or "").split(","):
        name, _, level = part.strip().partition("=")
        if name in SOFT_CRITERIA and level in LEVELS:
            out[name] = level
    return out


def levels_in_force(conn: sqlite3.Connection, config: dict) -> tuple[dict, list]:
    """`(levels_now, [(effective_from, levels)])` — the screen as it actually stood over time.

    The screen is not a constant over the recorded window and treating it as one is a real error,
    not a rounding one. `symbol_screen_edge_gates_off` (2026-08-25) turned three gates from `pass`
    to `off`, so a row scanned on 08-20 and refused by the market-cap gate was refused BY THE
    SCREEN — while today's levels say market cap is off, which would classify that same row as
    "refused outside the screen" and quietly remove it from the replay's universe.

    Read from the module's own `measurement_breaks`, which records the old and new level strings
    verbatim. That table exists so a past configuration is recoverable; this is the use.
    """
    now = {c: (config.get("symbol_screen") or {}).get(c, "pass") for c in SOFT_CRITERIA}
    try:
        rows = conn.execute(
            "SELECT break_date, old_value FROM measurement_breaks WHERE key = ? ORDER BY break_date DESC",
            (SCREEN_BREAK_KEY,),
        ).fetchall()
    except sqlite3.Error:
        return now, []

    history = []
    walking = dict(now)
    for row in rows:
        # Walking backwards: before this break, the levels were whatever it replaced.
        walking = {**walking, **_parse_levels(row["old_value"])}
        history.append((row["break_date"], dict(walking)))
    return now, history


def _levels_on(scan_date: str, levels_now: dict, history: list) -> dict:
    """The screen in force on `scan_date`. `history` is newest-break-first from `levels_in_force`."""
    for break_date, levels in history:
        if scan_date < break_date:
            return levels
    return levels_now


def _reviews(conn: sqlite3.Connection, start: str | None, end: str | None) -> list[dict]:
    where, params = ["criteria_json IS NOT NULL"], []
    if start:
        where.append("scan_date >= ?")
        params.append(start)
    if end:
        where.append("scan_date <= ?")
        params.append(end)
    rows = conn.execute(
        f"SELECT scan_date, symbol, selected, reason, criteria_json FROM entry_reviews"
        f" WHERE {' AND '.join(where)} ORDER BY scan_date, symbol",
        params,
    ).fetchall()
    out = []
    for r in rows:
        try:
            criteria = json.loads(r["criteria_json"])
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "scan_date": r["scan_date"],
                "symbol": r["symbol"],
                "selected": bool(r["selected"]),
                "reason": r["reason"] or "",
                "criteria": criteria,
            }
        )
    return out


def _outcomes(conn: sqlite3.Connection) -> dict:
    """(symbol, session) -> the trades opened on it. One review can produce several: the scan is
    per symbol and each admitted strategy opens its own position."""
    out: dict[tuple, list[dict]] = {}
    for t in conn.execute(
        "SELECT order_id, symbol, strategy, opened_at, pnl, entry_cost, exit_cost, status FROM trades"
    ):
        session = _session_of(t["opened_at"])
        if session:
            out.setdefault((t["symbol"], session), []).append(dict(t))
    return out


def _net(trade: dict) -> float:
    """Net is pnl minus both costs — the earnings rule, stated once in `core.ledgers` and matched
    here so the replay's returns and the module's own net cannot disagree."""
    return (trade["pnl"] or 0.0) - (trade["entry_cost"] or 0.0) - (trade["exit_cost"] or 0.0)


def replay(
    conn: sqlite3.Connection,
    levels: dict,
    *,
    config: dict | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """What `levels` would have admitted, and what the admitted names actually returned.

    `levels` maps each of `SOFT_CRITERIA` to "pass" / "near_miss" / "off"; anything unnamed defaults
    to "pass", the strict bar, matching `scanner._screen_levels`.

    The universe is deliberately narrower than every recorded review. A row the real scanner rejected
    for a reason OUTSIDE the soft screen — a hard filter, a tier exclusion, a timeout — could not
    have been entered under any screen, so admitting it here would invent a candidate. Those rows
    are excluded and counted in `universe.not_replayable`, never silently folded in.
    """
    config = config or _scanner._load_config()
    levels = {c: levels.get(c, "pass") for c in SOFT_CRITERIA}
    levels_now, history = levels_in_force(conn, config)

    reviews = _reviews(conn, start, end)
    outcomes = _outcomes(conn)

    measured_trades: list[dict] = []
    measured_names: set = set()
    counterfactual_names: set = set()
    refused: dict[str, int] = {}
    not_replayable = 0

    for review in reviews:
        # Could this row have been entered at all, under ANY screen? If it passes the screen that
        # was actually in force and still was not selected, something outside the screen refused it.
        # ...judged against the screen in force ON THAT DATE, not today's.
        in_force = _levels_on(review["scan_date"], levels_now, history)
        if not review["selected"] and not _soft_failures(review["criteria"], config, in_force):
            not_replayable += 1
            continue

        failures = _soft_failures(review["criteria"], config, levels)
        if failures:
            for f in failures:
                refused[f] = refused.get(f, 0) + 1
            continue

        key = (review["symbol"], review["scan_date"])
        trades = outcomes.get(key)
        if trades:
            measured_trades.extend(trades)
            measured_names.add(key)
        else:
            counterfactual_names.add(key)

    closed = [t for t in measured_trades if t["status"] == "closed"]
    nets = [_net(t) for t in closed]

    # Every result carries its own validation. calendars' exit-policy replay validates against the
    # real books on every run, for the same reason: a replay nobody checks is a model of itself, and
    # the failure it would hide — the replay and the scanner having come apart — makes every answer
    # wrong in a way the numbers cannot show.
    checked = validate(conn, config=config, start=start, end=end)

    return {
        "screen": levels,
        "validation": {
            "reproduces": f"{checked['agree']} of {checked['replayable']} recorded decisions",
            "ok": checked["ok"],
            "verified_from": checked["verified_from"],
            "disagreements": len(checked["disagreements"]),
            "_note": (
                "levels are recoverable from measurement_breaks; the min_* thresholds behind them "
                "are not journalled, so today's are applied to every date. Answers before "
                "`verified_from` rest on a screen this module cannot fully reconstruct."
            ),
        },
        "recorded_screen": levels_now,
        # Every distinct screen the window actually ran under, newest break first. A replay over a
        # window whose screen changed is answering about more than one regime, and the reader is
        # owed that.
        "screen_history": [{"before": d, "levels": v} for d, v in history],
        "universe": {
            "reviews": len(reviews),
            # Rejected outside the screen — a hard filter, a tier exclusion, a timeout. No screen
            # reaches these, so no proposal can claim them.
            "not_replayable": not_replayable,
            "replayable": len(reviews) - not_replayable,
        },
        # Admitted AND actually traded. These have outcomes because the control was wide enough to
        # contain them, so a return here is measured, not modelled.
        "measured": {
            "candidates": len(measured_names),
            "trades": len(measured_trades),
            "closed": len(closed),
            "open": len(measured_trades) - len(closed),
            "net_pnl": round(sum(nets), 2) if nets else None,
            "wins": sum(1 for n in nets if n > 0),
            "losses": sum(1 for n in nets if n <= 0),
            "symbols": sorted({s for s, _ in measured_names}),
            "sessions": sorted({d for _, d in measured_names}),
        },
        # Admitted and NEVER TRADED. There is deliberately no P&L key here and there must never be
        # one: these names have no outcome, and a counterfactual return is a number nobody measured.
        "counterfactual": {
            "candidates": len(counterfactual_names),
            "symbols": sorted({s for s, _ in counterfactual_names}),
            "sessions": sorted({d for _, d in counterfactual_names}),
            "_no_return": (
                "never traded — counts and symbols only. A name with no outcome has no return, and "
                "widening the control lifts that only for names inside the new bar."
            ),
        },
        "refused_by_criterion": dict(sorted(refused.items(), key=lambda kv: -kv[1])),
    }


def validate(conn: sqlite3.Connection, *, config: dict | None = None, **kw) -> dict:
    """Replay the screen that was ACTUALLY in force and check it reproduces the recorded decisions.

    calendars' exit-policy replay validates to the cent against the real books on every run, and a
    replay nobody checks is a model of itself. The check here: over the replayable universe, every
    row this module would admit at the recorded levels should be a row the scanner selected, and
    vice versa. A mismatch means the replay and the scanner have come apart — which is the one
    failure that makes every proposed-screen answer wrong in a way the numbers will not show.
    """
    config = config or _scanner._load_config()
    levels_now, history = levels_in_force(conn, config)

    disagreements = []
    replayable = 0
    for review in _reviews(conn, kw.get("start"), kw.get("end")):
        recorded = _levels_on(review["scan_date"], levels_now, history)
        failures = _soft_failures(review["criteria"], config, recorded)
        if not review["selected"] and not failures:
            continue  # refused outside the screen; not this module's to reproduce
        replayable += 1
        would_admit = not failures
        if would_admit != review["selected"]:
            disagreements.append(
                {
                    "scan_date": review["scan_date"],
                    "symbol": review["symbol"],
                    "recorded_selected": review["selected"],
                    "replay_admits": would_admit,
                    "soft_failures": failures,
                    "recorded_reason": review["reason"][:120],
                }
            )

    # The first date from which the replay reproduces reality with no disagreement. More useful
    # than a bare failure count: it says which answers to trust rather than only that some are not
    # trustworthy.
    bad_dates = sorted({d["scan_date"] for d in disagreements})
    verified_from = None
    if replayable:
        seen = sorted({r["scan_date"] for r in _reviews(conn, kw.get("start"), kw.get("end"))})
        verified_from = next((d for d in seen if not bad_dates or d > bad_dates[-1]), None)

    return {
        "screen": levels_now,
        "screen_history": [{"before": d, "levels": v} for d, v in history],
        "replayable": replayable,
        "agree": replayable - len(disagreements),
        "disagreements": disagreements,
        "verified_from": verified_from,
        "ok": not disagreements,
    }


def main(argv=None) -> int:
    """`python -m cherrypick.earnings.entry_replay --screen winrate=off,iv_rv_ratio=near_miss`

    Read-only over the paper ledger: no broker, no Dolt, safe to run mid-session — the same posture
    as `screen_report`, which this is the outcome-aware sibling of.
    """
    import argparse

    from cherrypick.earnings import paths as _paths

    ap = argparse.ArgumentParser(
        prog="entry-replay", description="score a proposed entry screen over recorded outcomes"
    )
    ap.add_argument("--screen", help='e.g. "winrate=off,iv_rv_ratio=near_miss"; unnamed default to pass')
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--validate-only", action="store_true", dest="validate_only")
    ap.add_argument("--db", help="paper ledger path")
    args = ap.parse_args(argv)

    path = args.db or str(_paths.data_path("paper_trades.db"))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if args.validate_only:
            out = validate(conn, start=args.start, end=args.end)
        else:
            out = replay(conn, _parse_levels(args.screen), start=args.start, end=args.end)
    finally:
        conn.close()
    print(json.dumps({"ok": True, **out}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
