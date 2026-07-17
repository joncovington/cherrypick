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

    L.append("## Module reports")
    for name, mcfg in enabled.items():
        f = _module_eod_file(mcfg, name, day)
        L.append(f"- **{name}**: {f}" if f else f"- **{name}**: _(no paper-eod-{day}.md written)_")
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
