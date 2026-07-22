"""Suite end-of-day digest (read-only).

One markdown roll-up across every enabled module for a single trading session: the normalized,
cost-adjusted suite/per-module P&L from `report.run(session=day)` plus a pointer to each module's own
deterministic `paper-eod-<day>.md` file. Citing `report`'s numbers (rather than re-summing the DBs)
means the suite total can never drift from what `report`/`calibrate` show for the same day.

Read-only: files only, no broker, no network, no trading. This is a scheduled/on-demand surface, **off**
the watchdog reliability path — callers on that path (the watchdog tick) must invoke it best-effort
(try/except), the same discipline as the tick-time dashboard render.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config as cfgmod
from . import report, timeutil


def _money(x: float | None) -> str:
    if x is None:
        return "-"
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _pct(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "-"


def _module_eod_file(mcfg: dict, name: str, day: str):
    """The module's own deterministic paper EOD file for `day`
    (~/.cherrypick/logs/<name>/paper-eod-<day>.md), or None if it hasn't written one for that session
    yet. `mcfg` is unused now that logs live in the shared logs home, kept for signature stability."""
    p = cfgmod.module_logs_dir(name) / f"paper-eod-{day}.md"
    return p if p.exists() else None


def _module_analysis_file(name: str, day: str):
    """The module's conversational 7-section EOD analysis for `day`
    (~/.cherrypick/logs/<name>/eod-analysis-<day>.md), the companion to the terse paper-eod file, or
    None if the module hasn't written one for that session yet."""
    p = cfgmod.module_logs_dir(name) / f"eod-analysis-{day}.md"
    return p if p.exists() else None


def _snapshot(suite: dict, modules: dict) -> list[str]:
    """A short conversational read on the suite session, templated from the same `report` numbers the
    tables below cite (so it can never disagree with them). No synthesis, no live data."""
    trades = suite.get("trades", 0)
    sopen = suite.get("open") or {}
    if not trades:
        if sopen.get("positions"):
            # The blind spot the two-section earnings EOD was built to close, at suite level: a day
            # that closed nothing but opened positions is not flat — it put real overnight risk on.
            risk = _money(sopen["capital_at_risk"])
            return [f"_No module *closed* a trade today, but **{sopen['positions']}** position(s) were "
                    f"opened and are carried overnight (**{risk}** of defined max loss at risk). "
                    "Realized P&L lands when they settle; see 'Opened this session' below._"]
        return ["_Flat suite session - no module closed a trade today. Each module's own report says which "
                "gates held its book out; a quiet day is a decision, not a gap._"]
    net = suite.get("net_pnl")
    gross = suite.get("gross_pnl")
    cost = suite.get("cost") or 0.0
    ok_mods = {n: m for n, m in modules.items() if m.get("ok") and m.get("trades")}
    carrier = max(ok_mods.items(), key=lambda kv: kv[1].get("net_pnl") or 0.0) if ok_mods else None
    if gross and gross > 0:
        drag = f" Costs took {_money(cost)} - {cost / gross * 100:.0f}% of the {_money(gross)} gross."
    else:
        drag = f" Costs took {_money(cost)} on top of a losing gross."
    lead = (f"Across the enabled modules the suite closed **{trades}** trade{'s' if trades != 1 else ''} "
            f"for **Net {_money(net)}**.{drag}")
    out = [lead]
    # The gross-vs-net win-rate gap: how many trades had edge before costs but not after.
    gwr, nwr = suite.get("gross_win_rate"), suite.get("win_rate")
    if gwr is not None and nwr is not None:
        flipped = round((gwr - nwr) * trades)
        gap = f" {_pct(gwr)} of trades were green before costs but only {_pct(nwr)} after"
        if flipped > 0:
            gap += f" - costs alone flipped ~{flipped} trade{'s' if flipped != 1 else ''} from win to loss."
        else:
            gap += "."
        out.append("Edge check:" + gap)
    if carrier:
        cname, cm = carrier
        cnet = cm.get("net_pnl") or 0.0
        if cnet > 0:
            # At least one module finished green — it carried the day; name any that dragged.
            tail = f"{cname} carried the session ({_money(cnet)})."
            laggards = [n for n, m in ok_mods.items() if n != cname and (m.get("net_pnl") or 0.0) < 0]
            if laggards:
                tail += f" Dragged by: {', '.join(laggards)}."
        elif len(ok_mods) == 1:
            tail = f"{cname} finished in the red ({_money(cnet)})."
        else:
            # Every module finished flat/negative — name the least-bad and the worst rather than
            # calling the least-bad module a "carrier" it wasn't.
            worst_name, worst_m = min(ok_mods.items(), key=lambda kv: kv[1].get("net_pnl") or 0.0)
            tail = (f"No module finished green — {cname} least-bad ({_money(cnet)}), "
                    f"{worst_name} worst ({_money(worst_m.get('net_pnl'))}).")
        out.append(tail)
    if sopen.get("positions"):
        out.append(f"Separately, {sopen['positions']} position(s) were opened and carried overnight "
                   f"({_money(sopen['capital_at_risk'])} at risk) — no realized P&L yet.")
    return out


def build_markdown(cfg: dict, day: str, rep: dict | None = None) -> str:
    """Render the digest markdown for `day` from the shared report roll-up. Pure; no file writes.
    Pass a precomputed `rep` (report.run(cfg, session=day)) to avoid re-reading the paper DBs."""
    rep = rep if rep is not None else report.run(cfg, session=day)
    modules = rep.get("modules", {})
    suite = rep.get("suite", {})
    enabled = cfgmod.enabled_modules(cfg)

    L = [f"# cherrypick - Suite EOD Digest {day}", ""]
    L.append(
        "_Read-only roll-up across every enabled module for this session, net of costs. Paper "
        "DBs only; live accounts untouched. Numbers match `cherrypick report --date` for the "
        "same day._"
    )
    L.append("")

    L.append("## Snapshot")
    L.extend(_snapshot(suite, modules))
    L.append("")

    L.append("## Suite total")
    L.append(f"- Trades closed: **{suite.get('trades', 0)}**")
    L.append(
        f"- Gross **{_money(suite.get('gross_pnl'))}** &minus; costs "
        f"**{_money(suite.get('cost'))}** = **Net {_money(suite.get('net_pnl'))}**"
    )
    L.append(
        f"- Wins / Losses: {suite.get('wins', 0)} / {suite.get('losses', 0)} "
        f"(net win rate {_pct(suite.get('win_rate'))}, gross {_pct(suite.get('gross_win_rate'))})"
    )
    L.append("")

    L.append("## Per module")
    L.append("| Module | Trades | Wins | Losses | Win % | Gross | Cost | Net P&L |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name in enabled:
        m = modules.get(name, {})
        if not m.get("ok"):
            reason = m.get("reason", "no data")
            L.append(f"| {name} | - | - | - | - | - | - | _{reason}_ |")
            continue
        L.append(
            f"| {name} | {m.get('trades', 0)} | {m.get('wins', 0)} | {m.get('losses', 0)} | "
            f"{_pct(m.get('win_rate'))} | {_money(m.get('gross_pnl'))} | "
            f"{_money(m.get('cost'))} | {_money(m.get('net_pnl'))} |"
        )
    L.append("")

    # Overnight-carry section: only rendered when something is actually carried, so it stays absent
    # on a pure-0DTE day (MEIC + flies settle by the bell) and appears when a multi-day module
    # (earnings) opens positions. This is what the closed-trade Per-module table structurally cannot
    # show — capital at risk, not realized P&L.
    sopen = suite.get("open") or {}
    if sopen.get("positions"):
        L.append("## Opened this session (carried overnight)")
        L.append("_Positions entered today and held past the close — capital at risk (defined max loss), "
                 "not realized P&L. These settle at the next open and land in that day's closed totals "
                 "above. Multi-day strategies only; the 0DTE modules are flat by the bell._")
        L.append("| Module | Positions | Capital at risk | Names |")
        L.append("|---|---|---|---|")
        for name in enabled:
            o = modules.get(name, {}).get("open") or {}
            if not o.get("positions"):
                continue
            names = ", ".join(f"{s} x{n}" for s, n in (o.get("by_symbol") or {}).items())
            L.append(f"| {name} | {o['positions']} | {_money(o['capital_at_risk'])} | {names} |")
        L.append(f"- Suite total carried overnight: **{sopen['positions']}** position(s), "
                 f"**{_money(sopen['capital_at_risk'])}** of defined max loss at risk.")
        L.append("")

    L.append("## Module reports")
    L.append("_Each module writes a terse metrics file (paper-eod) and a conversational 7-section read "
             "(eod-analysis) for the session._")
    for name, mcfg in enabled.items():
        f = _module_eod_file(mcfg, name, day)
        a = _module_analysis_file(name, day)
        parts = []
        parts.append(f"metrics: {f}" if f else f"metrics: _(no paper-eod-{day}.md yet)_")
        parts.append(f"analysis: {a}" if a else f"analysis: _(no eod-analysis-{day}.md yet)_")
        L.append(f"- **{name}** - " + "; ".join(parts))
    L.append("")

    L.append(
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
        "· paper DBs only; live accounts untouched._"
    )
    return "\n".join(L)


def run(cfg: dict | None = None, day: str | None = None) -> dict:
    """Write the suite EOD digest for `day` (default: today ET) to logs/eod-digest-<day>.md.
    Read-only over the paper DBs; returns the path written plus the suite total for a caller
    (e.g. the notifier) to summarize without re-reading anything."""
    cfg = cfg or cfgmod.load_config()
    day = day or timeutil.now_et().strftime("%Y-%m-%d")
    rep = report.run(cfg, session=day)
    md = build_markdown(cfg, day, rep=rep)
    path = cfgmod.log_file(f"eod-digest-{day}.md")
    path.write_text(md, encoding="utf-8")
    return {"ok": True, "session": day, "digest": str(path), "suite": rep.get("suite", {})}
