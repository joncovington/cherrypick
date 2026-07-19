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
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analytics  # noqa: E402


def logs_dir() -> Path:
    """`~/.cherrypick/logs/flies` — the same path `cfgmod.module_logs_dir("flies")` derives, so both
    sides agree without importing each other."""
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return Path(home) / "logs" / "flies"


def _money(v) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


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
        f"- Misses, buffer too tight: {completion['buffer_too_tight']}",
        f"- Misses, never priced: {completion['counterfactual_unknown']}",
        f"- Median completion latency: {_num(completion['median_latency_min'], 1)} min",
        f"- Median spot move to completion: {_num(completion['median_spot_move'], 2)}",
        "",
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
            L.append(f"| {a['arm']} | {a['trades']} | {_money(a['net_pnl'])} | "
                     f"{_pct(a['win_rate'])} | {_drag(a['fee_drag_pct'])} |")
    else:
        L.append("_Nothing settled today._")
    L.append("")

    if windows:
        L.append("## By entry window")
        L.append("| window | trades | net | win rate |")
        L.append("|---|---|---|---|")
        for w in windows:
            L.append(f"| {w['window']} | {w['trades']} | {_money(w['net_pnl'])} | "
                     f"{_pct(w['win_rate'])} |")
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
        return ("No legged entries today, so there is nothing to say about completion yet. That is "
                "the measurement the whole module exists to take, and a day without one is a day "
                "without data — worth checking the decision journal for what gated the entries.")

    rate = completion["completion_rate"] or 0
    parts = [
        f"{completed} of {legged} legged entries completed into a butterfly "
        f"({rate * 100:.0f}%)."
    ]
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

    never, tight = completion["never_offered"], completion["buffer_too_tight"]
    if never or tight:
        parts.append(
            f"Of the misses, {never} never saw a completing debit below the credit at all, and "
            f"{tight} got below the credit but not past the fee buffer. Those two look identical in "
            "the P&L and call for opposite responses: the first is the market simply not offering "
            "the trade, which no threshold change would fix; the second is our own gate turning down "
            "flies that were available."
        )
        if tight > never and tight:
            parts.append(
                "With more misses landing on the wrong side of our own buffer than on the market's, "
                "the buffer is the first thing worth re-examining — bearing in mind it exists to stop "
                "us building flies whose floor is negative after fees, so loosening it is not free."
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
        band = ("no profitable band at all" if b["band_low"] is None
                else f"profitable only between {b['band_low']:.0f} and {b['band_high']:.0f}")
        parts.append(
            f"The {b['arm']} book is {band}, worst case {_money(b['worst'])} around "
            f"{_num(b['worst_at'], 0)}. Its risk graph may look green across the middle, but it is "
            "leaning on open short verticals, so calling it risk-free would be wrong — the floor is "
            "conditional on price staying inside those wings."
        )
    return " ".join(parts)


def _divergence_paragraph(divergence: dict) -> str:
    if not divergence["iterations"]:
        return ("Not enough iterations to compare what the arms wanted. Once there are, this is where "
                "we find out whether the comparison can answer anything at all.")
    rate = divergence["all_agree_rate"] or 0
    body = (f"Across {divergence['iterations']} iterations the arms picked the same centre "
            f"{rate * 100:.0f}% of the time.")
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
    parts = [f"Fees took {_money(stats['fees'])} against {_money(stats['gross_pnl'])} of gross, "
             f"a drag of {_drag(stats['fee_drag_pct'])}."]
    worst = [a for a in arms if (a["fee_drag_pct"] or 0) > 30]
    if worst:
        names = ", ".join(a["arm"] for a in worst)
        parts.append(
            f"On {names} the drag is above 30%, which is the level where the strategy is mostly "
            "paying the broker. This suite has already recorded a trade collecting $4.00 against "
            "$4.96 of fees, so this is a live failure mode, not a theoretical one."
        )
    return " ".join(parts)


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
