"""End-of-day reports: `paper-eod-<day>.md` (terse metrics) and `eod-analysis-<day>.md` (the read).

Both land in `~/.cherrypick/logs/flies/`. The orchestrator's `eod_digest` and `eod_insight` discover
them purely by filename convention, so nothing on that side needs to know this module exists.

**These lead with completion rate, the counterfactual, and the floor after fees — not with P&L.** On a
handful of 0DTE sessions P&L is mostly noise, and a report that opened with it would invite exactly the
wrong conclusion in either direction. The numbers that decide whether this strategy is real are how
often a leg-in actually completed and whether the floor survived costs.

Deterministic and offline: plain string formatting over the paper DB. No model call, no network. The
AI pass is the orchestrator's `eod_insight`, which reads these files.
"""

from __future__ import annotations

import os
from pathlib import Path

from cherrypick.core import viz  # noqa: E402

from cherrypick.flies import analytics  # noqa: E402


def logs_dir() -> Path:
    """`~/.cherrypick/logs/flies` — the same path `cfgmod.module_logs_dir("flies")` derives, so both
    sides agree without importing each other."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return Path(home) / "logs" / "flies"


def _money(v) -> str:
    # The suite's one formatter (cherrypick.core.viz), keeping this report's "n/a" placeholder.
    return viz.fmt_money(v, none="n/a")


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def _num(v, digits=2) -> str:
    return "n/a" if v is None else f"{v:,.{digits}f}"


def _drag(v) -> str:
    return "n/a" if v is None else f"{v:.1f}%"


# --------------------------------------------------------------------------- terse metrics file
def build_paper_eod(conn, day: str) -> str:
    stats = analytics.stats_for_period(conn, day, day)
    completion = analytics.completion_stats(conn, day, day)
    books = analytics.books_for_day(conn, day)
    arms = analytics.by_arm(conn, day, day)
    excluded = analytics.arm_comparison_exclusions(conn, day, day)
    divergence = analytics.arm_divergence(conn, day)
    windows = analytics.by_entry_window(conn, day, day)

    L = [
        f"# Flies paper — {day}",
        "",
        "0DTE net-credit butterflies (SPX). Paper only. Every figure is net of the modeled fee and",
        "slippage stack.",
        "",
        "## The numbers that decide this strategy",
        f"- Completion rate: {_pct(completion['completion_rate'])} "
        f"({completion['completed']} of {completion['legged_entries']} legged entries)",
        f"- Misses, market never offered it: {completion['never_offered']}",
        f"- Misses, blocked by fee_buffer: {completion['buffer_blocked']}",
        f"- Misses, blocked by min_floor_dollars: {completion['floor_blocked']}",
        f"- Misses, never priced: {completion['counterfactual_unknown']}",
        f"- Median completion latency: {_num(completion['median_latency_min'], 1)} min",
        f"- Median spot move to completion: {_num(completion['median_spot_move'], 2)}",
        "",
    ]

    # Post-completion counterfactual (book.py step 1d): what waiting past the first qualifying
    # tick would have captured. Reported only once a mode has tracked completions — an empty
    # section would read as "measured, nothing found", which is the opposite of "not yet measured".
    for mode, label in (("debit_first", "debit-first"), ("legged", "legged")):
        lot = analytics.left_on_table(conn, day, day, entry_mode=mode)
        if lot["n"] == 0:
            continue
        L += [
            f"## Completion credit left on table — {label}",
            f"- Tracked completions: {lot['n']} ({lot['improved']} saw a better price later)",
            f"- Median improvement: {_num(lot['median_improvement_pts'], 2)} pts "
            f"({_money(lot['median_improvement_dollars'])})",
            f"- Best case: {_num(lot['max_improvement_pts'], 2)} pts; "
            f"total across positions: {_money(lot['total_improvement_dollars'])}",
        ]
        for bucket, s in lot["by_gex_bucket"].items():
            L.append(
                f"- GEX {bucket}: {s['improved']}/{s['n']} improved, "
                f"median {_num(s['median_improvement_pts'], 2)} pts"
            )
        L.append("")

    L += [
        "## Session P&L",
        f"- Trades settled: {stats['trades']}",
        f"- Net: {_money(stats['net_pnl'])} "
        f"(gross {_money(stats['gross_pnl'])}, fees {_money(stats['fees'])})",
        f"- Win rate: {_pct(stats['win_rate'])} ({stats['wins']}W / {stats['losses']}L)",
        f"- Fee drag: {_drag(stats['fee_drag_pct'])} of gross",
        "",
    ]

    L.append("## Books")
    if books:
        L.append("| arm | credit | debits | fees | worst case | floor holds | band |")
        L.append("|---|---|---|---|---|---|---|")
        for b in books:
            band = "–" if b["band_low"] is None else f"{b['band_low']:.0f}–{b['band_high']:.0f}"
            holds = "yes" if b["floor_holds"] else "no"
            L.append(
                f"| {b['arm']} | {_money(b['credit_collected'])} | {_money(b['debits_paid'])} | "
                f"{_money(b['fees'])} | {_money(b['worst'])} | {holds} | {band} |"
            )
    else:
        L.append("_No books today._")
    L.append("")

    L.append("## By arm")
    if arms:
        L.append("| arm | trades | net | win rate | fee drag |")
        L.append("|---|---|---|---|---|")
        for a in arms:
            L.append(
                f"| {a['arm']} | {a['trades']} | {_money(a['net_pnl'])} | "
                f"{_pct(a['win_rate'])} | {_drag(a['fee_drag_pct'])} |"
            )
    else:
        L.append("_Nothing settled today._")
    if excluded["trades"]:
        # Stated, not implied: without this the table simply sums to less than Session P&L above.
        L.append("")
        L.append(
            f"_Compares {'/'.join(analytics.COMPARISON_ENTRY_MODES)} entries only. "
            f"Excludes {excluded['trades']} "
            f"{'/'.join(excluded['excluded_modes'])} position(s) worth "
            f"{_money(excluded['net_pnl'])}, which only some arms ever traded — they are in "
            f"Session P&L above and in the entry-mode breakdown, just not in this ranking._"
        )
    L.append("")

    if windows:
        L.append("## By entry window")
        L.append("| window | trades | net | win rate |")
        L.append("|---|---|---|---|")
        for w in windows:
            L.append(f"| {w['window']} | {w['trades']} | {_money(w['net_pnl'])} | {_pct(w['win_rate'])} |")
        L.append("")

    L.append("## Arm divergence")
    if divergence["iterations"]:
        L.append(f"- Iterations compared: {divergence['iterations']}")
        L.append(f"- All arms agreed: {_pct(divergence['all_agree_rate'])}")
        for p in divergence["pairs"]:
            L.append(f"- {p['arms']}: agreed {_pct(p['agreement_rate'])} of {p['iterations']}")
    else:
        L.append("_Not enough iterations to compare arms._")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- the conversational read
def _completion_paragraph(completion: dict) -> str:
    legged, completed = completion["legged_entries"], completion["completed"]
    if not legged:
        return (
            "No legged entries today, so there is nothing to say about completion yet. That is "
            "the measurement the whole module exists to take, and a day without one is a day "
            "without data — worth checking the decision journal for what gated the entries."
        )

    rate = completion["completion_rate"] or 0
    parts = [f"{completed} of {legged} legged entries completed into a butterfly ({rate * 100:.0f}%)."]
    if completed:
        parts.append(
            f"The ones that did took a median of {_num(completion['median_latency_min'], 1)} minutes "
            f"and about {_num(completion['median_spot_move'], 1)} points of spot movement. That "
            "matters beyond curiosity: a completion that needed a big, slow move is one a live "
            "working order would plausibly have caught, while one that came and went in seconds of "
            "quote drift probably would not have filled."
        )
    else:
        parts.append(
            "None completed, which means every legged entry today is sitting as an ordinary short "
            "vertical carrying its full defined risk. That is the branch this strategy has to beat, "
            "and on days like this it simply didn't."
        )

    never = completion["never_offered"]
    buffer_blocked, floor_blocked = completion["buffer_blocked"], completion["floor_blocked"]
    ours = buffer_blocked + floor_blocked
    if never or ours:
        parts.append(
            f"Of the misses, {never} never saw a completing debit below the credit at all, and "
            f"{ours} got below the credit but were turned down by our own gates. Those two look "
            "identical in the P&L and call for opposite responses: the first is the market simply not "
            "offering the trade, which no threshold change would fix; the second is our own gate "
            "turning down flies that were available."
        )
    if ours:
        parts.append(
            f"Of those {ours}, {buffer_blocked} missed the fee buffer and {floor_blocked} cleared the "
            f"buffer but landed under min_floor_dollars. That split decides which knob is even "
            "relevant, and they are not interchangeable."
        )
    if floor_blocked > buffer_blocked and floor_blocked:
        parts.append(
            "The floor minimum, not the buffer, is what is costing completions here. Worth weighing "
            "against what refusing actually buys: it does not free the slot, it leaves an uncompleted "
            "short vertical carrying full defined risk, which is the losing branch."
        )
    elif buffer_blocked:
        parts.append(
            "The buffer is the binding gate — bearing in mind it exists to stop us building flies "
            "whose floor is negative after fees, so loosening it is not free."
        )
    return " ".join(parts)


def _floor_paragraph(books: list[dict]) -> str:
    if not books:
        return "No books were opened, so there is no floor to report."
    holds = [b for b in books if b["floor_holds"]]
    bounded = [b for b in books if not b["floor_holds"]]
    parts = []
    if holds:
        names = ", ".join(b["arm"] for b in holds)
        parts.append(
            f"The {names} book{'s' if len(holds) > 1 else ''} closed with a floor that holds at every "
            "price — genuinely unable to lose at expiry, after fees. That is the actual claim this "
            "strategy makes, and on this book it is true rather than merely marketed."
        )
    for b in bounded:
        band = (
            "no profitable band at all"
            if b["band_low"] is None
            else f"profitable only between {b['band_low']:.0f} and {b['band_high']:.0f}"
        )
        parts.append(
            f"The {b['arm']} book is {band}, worst case {_money(b['worst'])} around "
            f"{_num(b['worst_at'], 0)}. Its risk graph may look green across the middle, but it is "
            "leaning on open short verticals, so calling it risk-free would be wrong — the floor is "
            "conditional on price staying inside those wings."
        )
    return " ".join(parts)


def _divergence_paragraph(divergence: dict) -> str:
    if not divergence["iterations"]:
        return (
            "Not enough iterations to compare what the arms wanted. Once there are, this is where "
            "we find out whether the comparison can answer anything at all."
        )
    rate = divergence["all_agree_rate"] or 0
    body = (
        f"Across {divergence['iterations']} iterations the arms picked the same centre "
        f"{rate * 100:.0f}% of the time."
    )
    if rate > 0.8:
        return body + (
            " That is high agreement, and it is a problem for the experiment rather than a happy "
            "result: if gex and control keep choosing the same strike, their P&L will look alike no "
            "matter how good or bad the GEX signal is, and separating them would need far more "
            "sample than the trade count suggests. Worth deciding early whether to widen the arms' "
            "differences rather than collecting data that was never going to distinguish them."
        )
    return body + (
        " That is healthy disagreement — the arms are genuinely testing different choices, which is "
        "what makes any eventual difference between them meaningful."
    )


def _cost_paragraph(stats: dict, arms: list[dict]) -> str:
    if not stats["trades"]:
        return "Nothing settled, so there is no cost picture yet."
    parts = [
        f"Fees took {_money(stats['fees'])} against {_money(stats['gross_pnl'])} of gross, "
        f"a drag of {_drag(stats['fee_drag_pct'])}."
    ]
    worst = [a for a in arms if (a["fee_drag_pct"] or 0) > 30]
    if worst:
        names = ", ".join(a["arm"] for a in worst)
        parts.append(
            f"On {names} the drag is above 30%, which is the level where the strategy is mostly "
            "paying the broker. This suite has already recorded a trade collecting $4.00 against "
            "$4.96 of fees, so this is a live failure mode, not a theoretical one."
        )
    return " ".join(parts)


def _drift_alignment_paragraph(conn) -> str:
    """Whether the completing direction agreed with the day's committed drift.

    Cumulative, like the regime paragraph: one session's opposing entries are a handful of rows, and
    the point is to read each new session against a STATED prior rather than rediscover the split
    every time. Reported and not gated — see `analytics.by_drift_alignment`.

    **Split per symbol, because the eras do not agree.** On SPX the opposing bucket completes 7%;
    blended with the XSP era it reads 53%, which would present a real signal as a weak one. Every
    other cross-symbol read in this module is separated for the same reason (fee schedules and wing
    scale both differ), and this one has the sharper motive: the band is a fraction of spot, so the
    buckets are comparable in shape but the underlying regimes are not.
    """
    symbols = [
        r["symbol"]
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM fly_positions WHERE status = 'settled' "
            "AND symbol IS NOT NULL ORDER BY symbol"
        )
    ]
    sections = []
    for symbol in symbols:
        rows = analytics.by_drift_alignment(conn, symbol=symbol)
        if not rows:
            continue
        by = {r["alignment"]: r for r in rows}
        band = rows[0]["band_pct"]
        lines = [
            f"**{symbol}** — completing direction against the session's drift "
            f"(committed past ±{band * 100:.2f}% of spot):",
            "",
            "| drift vs completing direction | n | completed | rate | net |",
            "|---|---|---|---|---|",
        ]
        for key, label in (("with", "with"), ("flat", "flat"), ("against", "**against**")):
            r = by.get(key)
            if not r:
                continue
            rate = f"{r['completion_rate'] * 100:.0f}%" if r["completion_rate"] is not None else "n/a"
            lines.append(f"| {label} | {r['trades']} | {r['completed']} | {rate} | ${r['net_pnl']:,.2f} |")
        sections.append("\n".join(lines))

    if not sections:
        return (
            "No settled position carries a recorded session open yet, so there is nothing to say "
            "about drift. Rows without one are omitted rather than counted flat — a session whose "
            "open was never captured is not an uncommitted day."
        )

    tail = (
        "An entry in the **against** bucket needs spot to reverse a drift the session has already "
        "committed to, in the hours it has left. `choose_side` is what produces these: on a trending "
        "day spot moves away from the centre, so it sells the side that then needs a reversal to "
        "complete. **Nothing gates on this** — the band and the rule were both chosen on the rows "
        "that measure them, so the case rests on the next clearly down-trending session reproducing "
        "it out of sample."
    )
    return "\n\n".join([*sections, tail])


def _regime_paragraph(coverage: dict, conn) -> str:
    """Regime coverage across the WHOLE book (not just today) plus any dimension that separates.

    Deliberately cumulative: one session's regime tags are four labels on a handful of rows and say
    nothing. The question this section exists to answer is whether the tags are accumulating into
    something answerable, and the honest daily answer for a while will be "not yet".
    """
    total = coverage["settled_trades"]
    if not total:
        return "No settled positions yet, so nothing is regime-tagged."

    lines, degenerate, thin = [], [], []
    for dim, info in coverage["dimensions"].items():
        if not info["tagged"]:
            thin.append(dim)
            continue
        spread = ", ".join(f"{b} {n}" for b, n in info["buckets"].items())
        lines.append(
            f"- **{dim}** — {info['tagged']}/{total} tagged ({_drag(info['coverage_pct'])}): {spread}"
        )
        if info["degenerate"]:
            degenerate.append(dim)

    out = [
        "Regime tags are descriptive only — nothing here gates a decision. They exist so a future "
        "regime-conditioned mode selector can be built from labelled outcomes rather than guessed at. "
        "Tagging began 2026-07-31 and **cannot be backfilled**, so coverage climbs only as new "
        "sessions settle.",
        "",
        *lines,
    ]
    if thin:
        out += ["", f"No rows tagged yet for: {', '.join(thin)}."]
    if degenerate:
        out += [
            "",
            f"⚠️ **{', '.join(degenerate)}** landed every tagged row in a single bucket. That is a "
            "measurement problem, not a market observation — a tag that cannot take its other value "
            "carries no information, and any P&L split on it would be an artefact. The continuous "
            "measure behind each bucket is stored alongside it, so the cut can be re-derived with "
            "`flies regime --dimension <dim> --bucket-edges ...` without re-running any session.",
        ]

    # Only show a P&L split where the dimension actually separates — a one-bucket table reads as a
    # finding to anyone skimming, and it is not one.
    for dim, info in coverage["dimensions"].items():
        if info["tagged"] < 2 or info["degenerate"]:
            continue
        rows = [r for r in analytics.by_regime(conn, dim) if r["bucket"] != "untagged"]
        if len(rows) < 2:
            continue
        out += ["", f"**{dim}** split (settled, legged, all sessions):", ""]
        out += ["| bucket | trades | net P&L | avg | win rate |", "|---|---:|---:|---:|---:|"]
        for r in rows:
            out.append(
                f"| {r['bucket']} | {r['trades']} | {_money(r['net_pnl'])} | "
                f"{_money(r['avg_pnl'])} | {_pct(r['win_rate'])} |"
            )
    out += [
        "",
        "Read the trade counts before the P&L. At these sample sizes a bucket's net is one or two "
        "sessions' noise, and no configuration change should follow from it yet.",
    ]
    return "\n".join(out)


def build_eod_analysis(conn, day: str) -> str:
    stats = analytics.stats_for_period(conn, day, day)
    completion = analytics.completion_stats(conn, day, day)
    books = analytics.books_for_day(conn, day)
    arms = analytics.by_arm(conn, day, day)
    divergence = analytics.arm_divergence(conn, day)
    journal = analytics.decision_journal(conn, day)
    positions = analytics.positions_for_day(conn, day)

    L = [
        f"# Flies — what happened on {day}",
        "",
        "_Paper trading. Companion to the terse `paper-eod-" + day + ".md`._",
        "",
        "## Did the mechanism work?",
        "",
        _completion_paragraph(completion),
        "",
        "## Were the floors real?",
        "",
        _floor_paragraph(books),
        "",
        "## Can the arms actually be told apart?",
        "",
        _divergence_paragraph(divergence),
        "",
        "## What did it cost?",
        "",
        _cost_paragraph(stats, arms),
        "",
        "## What regimes did we trade into?",
        "",
        _regime_paragraph(analytics.regime_coverage(conn), conn),
        "",
        "## Did we bet against the day?",
        "",
        _drift_alignment_paragraph(conn),
        "",
        "## What stopped us trading",
        "",
    ]

    refusals = [r for r in journal if not r["accepted"]]
    if not refusals:
        L.append("Nothing was refused today — every evaluation led to an action.")
    else:
        by_reason: dict[str, int] = {}
        for r in refusals:
            by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + (r["occurrences"] or 1)
        ranked = sorted(by_reason.items(), key=lambda kv: kv[1], reverse=True)
        top = ", ".join(f"`{reason}` ({n}x)" for reason, n in ranked[:4])
        L.append(
            f"The gates that fired most were {top}. These are counted runs, not individual log lines, "
            "so a large number means a gate stayed shut for a long stretch rather than that it "
            "triggered repeatedly on different setups."
        )
        if len(positions) == 0:
            L.append("")
            L.append(
                "No positions at all today. Before reading that as the strategy finding nothing, "
                "check whether the gates above are about the market (`credit_below_floor`) or about "
                "our own plumbing (`missing_leg_quotes`, `no_spot_price`) — the second kind means we "
                "had no data, not that there was no trade."
            )
    L.append("")

    L.append("## Reading this honestly")
    L.append("")
    L.append(
        "One 0DTE session a day means the P&L above is close to meaningless on its own; it will take "
        "weeks before any arm separates from the others. Completion rate and the counterfactual split "
        "accumulate much faster, and they are what should drive any change to the configuration. If "
        "the floors keep coming out negative after fees, the answer is to stop rather than to tune."
    )
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- live EOD
def build_live_eod(live_conn, paper_conn, day: str) -> str:
    """The LIVE day's metrics file — deliberately terse, and honest about provenance: every
    settlement line names its source (last_trade_provisional vs official), and the live-vs-paper
    section carries the plan doc's abort rule so the pilot's central measurement is on paper
    (the kind you read), not a manual chore."""
    from cherrypick.flies import analytics

    books = analytics.books_for_day(live_conn, day)
    positions = analytics.positions_for_day(live_conn, day)
    arm = books[0]["arm"] if books else (positions[0]["arm"] if positions else "gex")
    lvp = analytics.live_vs_paper(live_conn, paper_conn, arm)

    L = [f"# Flies LIVE EOD — {day}", ""]
    L.append("_Real-money ledger (live_trades.db). Not paper; excluded from every promotion reading._")
    L.append("")

    L.append("## Book")
    if not books:
        L.append("- No live book rows for this day.")
    for b in books:
        src = b.get("settlement_source") or ("unsettled" if b["status"] != "settled" else "?")
        pnl = _money(b.get("pnl")) if b.get("pnl") is not None else "n/a"
        L.append(
            f"- **{b['book_id']}** — {b['status']} (settlement: {src}), P&L {pnl}, "
            f"credit {_money(b.get('credit_collected'))}, debits {_money(b.get('debits_paid'))}, "
            f"fees {_money(b.get('fees'))}"
        )
        if b.get("settlement_source") == "last_trade_provisional":
            L.append(
                "  - PROVISIONAL — confirm with "
                f"`python src/live_loop.py --settle --price <official print> --date {day}`"
            )
    L.append("")

    L.append("## Positions")
    if not positions:
        L.append("- none")
    else:
        L.append("| id | kind | center | W | credit | debit | fees | floor | P&L | fills |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for p in positions:
            fills = f"{p.get('entry_fill_status') or '-'}/{p.get('completion_fill_status') or '-'}"
            L.append(
                f"| {p['position_id']} | {p['kind']} | {_num(p['center'], 0)} | {_num(p['wing_width'], 0)} "
                f"| {_num(p.get('credit'), 2)} | {_num(p.get('debit'), 2)} | {_money(p.get('fees'))} "
                f"| {_money(p.get('floor_dollars'))} | {_money(p.get('pnl'))} | {fills} |"
            )
    L.append("")

    L.append("## Live vs contemporaneous paper (the pilot's instrument)")
    lv, pp = lvp["live"], lvp["paper"]
    L.append(
        f"- Live: {lv['completed']}/{lv['entries']} completed ({_pct(lv['completion_rate'])}) over "
        f"{lv['sessions']} session(s); median latency {_num(lv['median_latency_min'], 0)} min; "
        f"avg credit {_num(lv['avg_credit'], 2)}, avg completion debit {_num(lv['avg_completion_debit'], 2)}"
    )
    L.append(
        f"- Paper (same arm, same sessions): {pp['completed']}/{pp['entries']} completed "
        f"({_pct(pp['completion_rate'])}); median latency {_num(pp['median_latency_min'], 0)} min; "
        f"avg credit {_num(pp['avg_credit'], 2)}, avg completion debit {_num(pp['avg_completion_debit'], 2)}"
    )
    ab = lvp["abort_rule"]
    gap = lvp["completion_gap"]
    if ab["triggered"]:
        L.append(
            f"- **ABORT RULE TRIGGERED**: live completion runs {_pct(gap)} below paper with "
            f"{lv['entries']} live entries (limit {_pct(ab['gap_limit'])} at ≥{ab['min_live_entries']}). "
            "Per docs/live-trading-plan.md: halt the pilot — paper's upper bound is not achievable."
        )
    elif ab["armed"]:
        L.append(
            f"- Abort rule ARMED ({lv['entries']} ≥ {ab['min_live_entries']} live entries): "
            f"completion gap {_pct(gap)} vs limit {_pct(ab['gap_limit'])} — within bounds."
        )
    else:
        L.append(
            f"- Abort rule not yet armed: {lv['entries']}/{ab['min_live_entries']} live entries "
            f"accrued (current completion gap {_pct(gap) if gap is not None else 'n/a'})."
        )
    L.append("")
    return "\n".join(L)


def write_live_report(live_conn, paper_conn, day: str, directory: Path | None = None) -> dict:
    """Write live-eod-<day>.md. Overwrites — the official-print re-settle should refresh it."""
    directory = directory or logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"live-eod-{day}.md"
    path.write_text(build_live_eod(live_conn, paper_conn, day), encoding="utf-8")
    return {"ok": True, "day": day, "live_eod": str(path)}


# --------------------------------------------------------------------------- writers
def write_reports(conn, day: str, directory: Path | None = None) -> dict:
    """Write both files for `day`. Overwrites — a re-run after a late settle should refresh them."""
    directory = directory or logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    paper = directory / f"paper-eod-{day}.md"
    analysis = directory / f"eod-analysis-{day}.md"
    paper.write_text(build_paper_eod(conn, day), encoding="utf-8")
    analysis.write_text(build_eod_analysis(conn, day), encoding="utf-8")
    return {"ok": True, "day": day, "paper_eod": str(paper), "eod_analysis": str(analysis)}
