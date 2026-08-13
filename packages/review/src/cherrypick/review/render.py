"""Markdown render of a fact set.

Renders, never computes. Every number here comes from the written artifact, so this file and the
console page and the narrative cannot disagree about what happened — the failure mode that produced
six incomparable report families in the first place.

Layout follows the reading it is for: the daily exception first (what needs attention today), then
what each module did, then the trend. Anything unmeasured prints as `—` rather than `0`, because a
report that renders "not recorded" as a number is how a cost model came to look 90% cheaper than it
was.
"""

from __future__ import annotations

from cherrypick.review import facts as _facts
from cherrypick.review import paths as _paths
from cherrypick.review import trends as _trends


def _money(value) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _count(value) -> str:
    return "—" if value is None else f"{value:,}"


def _value(value) -> str:
    """A number of unknown units -- module-native expectations are counts for one module and
    dollars for another, so this formats without asserting which."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def _attention(facts: dict) -> list[str]:
    """What a reader needs to see before anything else. Empty is a real and good answer."""
    out = []
    for name, m in (facts.get("modules") or {}).items():
        if not m.get("ok"):
            out.append(f"**{name}** could not be read — {m.get('reason')}")
            continue
        health = m.get("health") or {}
        if not health.get("loop_ticked"):
            out.append(
                f"**{name}** did not tick at all this session — a stopped loop, not a quiet day"
            )
        if health.get("errors"):
            out.append(f"**{name}** logged {health['errors']} iteration error(s)")
        suspected = (m.get("sample") or {}).get("suspected_break")
        if suspected:
            out.append(
                f"**{name}** looks like a regime change with no journaled break — "
                f"{suspected['trades']} trades against a trailing median of "
                f"{suspected['trailing_median_trades']:.0f} ({suspected['ratio']}x). "
                "Trends either side of it are not comparable until it is journaled."
            )
        if (m.get("sample") or {}).get("breaks") is None:
            out.append(
                f"**{name}** does not track measurement breaks — its trend assumes a "
                "continuity nothing verified"
            )
    return out


def render(session: str, window: int = _trends.DEFAULT_WINDOW) -> str:
    facts = _facts.read(session)
    if not facts:
        return f"# Review — {session}\n\n_No fact set written for this session._\n"

    L: list[str] = []
    L.append(f"# Suite review — {session}")
    L.append("")
    L.append(
        f"_Status **{facts['status']}** · fact set v{facts['fact_version']} · paper books._"
        + (
            "  \n_Provisional: the overnight module has not settled yet, so its realised P&L lands "
            "in the next session's report._"
            if facts["status"] == _facts.STATUS_PROVISIONAL
            else ""
        )
    )
    L.append("")

    # --- needs attention -----------------------------------------------------
    L.append("## Needs attention")
    L.append("")
    attention = _attention(facts)
    if attention:
        for item in attention:
            L.append(f"- {item}")
    else:
        L.append("_Nothing flagged: every module ticked, none errored, and no unjournaled regime change._")
    L.append("")

    # --- the day -------------------------------------------------------------
    L.append("## What each module did")
    L.append("")
    L.append(
        "_No suite total, deliberately: these books differ in scale by more than an order of "
        "magnitude, so a combined figure describes the largest one and implies it describes all "
        "three._"
    )
    L.append("")
    L.append("| Module | Closed | Net | Capital at risk | Return on risk | Wins | Raw n | Events |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, m in (facts.get("modules") or {}).items():
        if not m.get("ok"):
            L.append(f"| {name} | — | — | — | — | — | — | — |")
            continue
        r, ret, sample = m["results"], m["return"], m.get("sample") or {}
        L.append(
            f"| {name} | {_count(r['closed'])} | {_money(r['net'])} | "
            f"{_money(ret.get('capital_at_risk'))} | {_pct(ret.get('on_max_risk'))} | "
            f"{_count(r['wins'])} | {_count(sample.get('n'))} | {_count(sample.get('effective_n'))} |"
        )
    L.append("")
    L.append(
        "_**Events** is the independent-observation count: trades sharing a symbol and session "
        "share one market event. A book with hundreds of trades on one name in one session has "
        "one event, not hundreds._"
    )
    L.append("")

    # --- expected vs observed ------------------------------------------------
    L.append("## Expected against observed")
    L.append("")
    L.append("_Each module against its own model — they are not comparable with each other._")
    L.append("")
    for name, m in (facts.get("modules") or {}).items():
        if not m.get("ok"):
            continue
        e = m.get("expected_vs_observed") or {}
        expected, observed = e.get("expected"), e.get("observed")
        if expected is None and observed is None:
            L.append(f"- **{name}** ({e.get('basis', 'n/a')}): nothing to compare this session")
            continue
        # Half a comparison is still worth printing -- flies books carry an actual with no model
        # on some days -- but the missing half prints as unmeasured, never as the string "None".
        L.append(
            f"- **{name}** ({e.get('basis')}): expected {_value(expected)}, observed {_value(observed)}"
            + ("  _(no model recorded for this session)_" if expected is None else "")
        )
    L.append("")

    # --- carried overnight ---------------------------------------------------
    carried = {
        n: m["carried_overnight"]
        for n, m in (facts.get("modules") or {}).items()
        if m.get("ok") and m["carried_overnight"]["positions"]
    }
    if carried:
        L.append("## Carried overnight")
        L.append("")
        for name, c in carried.items():
            L.append(
                f"- **{name}**: {c['positions']} position(s), "
                f"{_money(c['capital_at_risk'])} at risk — no realised P&L until they settle"
            )
        L.append("")

    # --- trend ---------------------------------------------------------------
    L.append(f"## Trend (up to {window} sessions)")
    L.append("")
    L.append(
        "_A trend never crosses a measurement break: results either side of one are not the same "
        "experiment. Where a window collapsed to a session or two, that is the evidence there is._"
    )
    L.append("")
    L.append("| Module | Sessions | Stopped at break | Closed | Net | Win rate | Events |")
    L.append("|---|---:|---|---:|---:|---:|---:|")
    for name in _facts.MODULES:
        t = _trends.trend(name, session, window)
        stopped = t["stopped_at_break"] or ("— (none tracked)" if t["tracks_breaks"] is False else "—")
        L.append(
            f"| {name} | {t['sessions_used']}/{t['sessions_requested']} | {stopped} | "
            f"{_count(t['closed'])} | {_money(t['net'])} | {_pct(t['win_rate'])} | "
            f"{_count(t['effective_n'])} |"
        )
    L.append("")

    L.append("---")
    L.append("")
    L.append(
        f"_Generated from `{_paths.facts_path(session).name}`. Every figure above is read from that "
        "artifact, never recomputed — if this disagrees with the console or the narrative, one of "
        "them has a bug rather than a different opinion._"
    )
    L.append("")
    return "\n".join(L)


def write(session: str, window: int = _trends.DEFAULT_WINDOW):
    target = _paths.render_path(session)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(render(session, window), encoding="utf-8")
    tmp.replace(target)
    return target
