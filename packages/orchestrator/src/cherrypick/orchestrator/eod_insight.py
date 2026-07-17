"""Optional Claude-synthesized EOD insight (read/enrichment side).

When Claude Code (`claude`) is on PATH and `eod_insight.enabled` is true, this shells out to Claude in
headless print mode to synthesize a genuine cross-module narrative over the day's **deterministic** paper
reports (each module's `eod-analysis` + `paper-eod`, and the suite `eod-digest`) and writes
`eod-insight-<day>.md` to the logs home. This is the "why / what to change" layer the rule-based templater
can't produce.

Strictly an enrichment layer, strictly off the reliability path — the guardrails are load-bearing:
  - **Feature-detected + opt-in.** No `claude`, or `enabled` false → skipped; the deterministic
    `eod-analysis` remains the guaranteed floor. Default off (it needs Claude + auth + a paid call).
  - **Files in, text out, no dangerous tools.** The report markdown is piped to Claude on stdin; the
    invocation disallows Bash/Edit/Write/NotebookEdit/WebFetch/WebSearch/Task, so the agent can't run
    commands, edit/write files, or reach the network — it reads the text and emits prose. The
    **orchestrator** writes the output file; the agent never gets filesystem/broker access.
  - **Best-effort.** Any failure returns `{"ok": False, ...}` and never raises fatally; a caller must
    invoke it off the watchdog reliability path (like `eod_digest`).

Paper reports only; live accounts untouched. Output is clearly labelled AI-generated and not advice.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

from . import config as cfgmod
from . import timeutil

# The agent never gets execution, file-write, or arbitrary-URL tools — everything that could run a
# command, mutate the tree, or fetch a page is denied. **WebSearch is the one exception**, and only when
# `eod_insight.research_events` is on: it lets the debrief research upcoming macro/earnings events. No MCP
# config is passed, so there are no MCP tools either. (Read/Glob/Grep are harmless and unused — input is
# on stdin.)
_DISALLOWED_TOOLS = ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "Task"]

_SYSTEM = (
    "You are a seasoned options-trading analyst writing a daily debrief for the operator of the cherrypick "
    "paper-trading suite. You understand 0DTE and defined-risk options mechanics deeply — dealer gamma / "
    "GEX positioning (negative GEX = dealers amplify moves, the trending regime hostile to short condors; "
    "positive GEX = mean-reversion/pinning), IV rank vs realized vol, theta and gamma near expiration, "
    "credit-to-width and cost drag, per-side stops, IV crush on earnings, and correlation across index "
    "products (SPX/XSP are the same underlying; QQQ/IWM track the same broad factor). Explain the session "
    "THROUGH these mechanics — teach, don't just restate tables. All data is PAPER (simulated), for "
    "education and research only — NOT financial, investment, or trading advice, and never a recommendation "
    "to place a live order. Analyze only the reports provided; do not use any tools."
)


def _claude_available() -> str | None:
    """Path to the `claude` executable, or None. Injectable seam for tests."""
    return shutil.which("claude")


def _gather_inputs(cfg: dict, day: str) -> list[tuple[str, Path]]:
    """The deterministic report files for `day` that exist: each enabled module's eod-analysis +
    paper-eod, then the suite digest. (label, path) pairs, in a stable reading order."""
    out: list[tuple[str, Path]] = []
    for name in cfgmod.enabled_modules(cfg):
        for kind in ("eod-analysis", "paper-eod"):
            p = cfgmod.module_logs_dir(name) / f"{kind}-{day}.md"
            if p.exists():
                out.append((f"{name} {kind}", p))
    digest = cfgmod.log_file(f"eod-digest-{day}.md")
    if digest.exists():
        out.append(("suite digest", digest))
    return out


def build_input(inputs: list[tuple[str, Path]]) -> str:
    """Concatenate the report files into one delimited text block for stdin."""
    parts = []
    for label, p in inputs:
        try:
            parts.append(f"===== {label} ({p.name}) =====\n{p.read_text(encoding='utf-8')}")
        except OSError:
            continue
    return "\n\n".join(parts)


def _upcoming_calendar(day: str, horizon_days: int = 45) -> str:
    """A deterministic block of the suite's own known market-calendar events in the next `horizon_days`
    (FOMC decisions, quarterly / triple-witching expiries, NYSE holidays), computed by
    cherrypick.core.calendar — no network. This is the *authoritative anchor* for the forward-looking
    section; web research supplements it (data releases, anticipated earnings). Empty string on failure."""
    try:
        from cherrypick.core import calendar as _cal
        d0 = date.fromisoformat(day)
    except Exception:
        return ""
    end = d0 + timedelta(days=horizon_days)
    years = sorted({d0.year, end.year})

    def _between(fn):
        out = []
        for y in years:
            try:
                out += [x for x in fn(y) if d0 < x <= end]
            except Exception:
                continue
        return sorted(out)

    lines = []
    fomc = _between(_cal.fomc_dates)
    if fomc:
        lines.append("- FOMC decision: " + ", ".join(x.isoformat() for x in fomc))
    tw = _between(_cal.triple_witching_dates)
    if tw:
        lines.append("- Triple-witching expiry: " + ", ".join(x.isoformat() for x in tw))
    q = [x for x in _between(_cal.quarterly_expiry_dates) if x not in set(tw)]
    if q:
        lines.append("- Quarterly expiry: " + ", ".join(x.isoformat() for x in q))
    hol = _between(_cal.nyse_holidays)
    if hol:
        lines.append("- NYSE holiday (market closed): " + ", ".join(x.isoformat() for x in hol))
    if not lines:
        return ""
    header = (f"===== suite market calendar — next ~{horizon_days} days from {day} "
              "(deterministic, authoritative) =====")
    return header + "\n" + "\n".join(lines)


def _prompt(day: str) -> str:
    return (
        f"Below are today's ({day}) deterministic paper-trading EOD reports for the cherrypick suite "
        "(MEIC = 0DTE iron condors on index/ETF products with VIX-banded short deltas and VIX / VIX1D / "
        "ATR / GEX regime gates and per-side stops; Earnings = overnight defined-risk plays harvesting IV "
        "crush). The MEIC analysis includes an 'entry gates at the last iteration' line — use it to explain "
        "why entries did or didn't fire. Write a substantive end-of-day debrief for the operator; the goal "
        "is genuine understanding of the day, not a summary of the numbers.\n\n"
        "Cover, in whatever structure reads best (markdown, short bold section labels):\n"
        "1. **What happened and WHY** — explain the results through the mechanics. If the book traded, why "
        "did entries/exits go the way they did (regime, IV rank, gamma/theta, cost drag, which side "
        "stopped and against what move, IV crush realized vs expected)? If it stayed flat or entries were "
        "gated, say exactly which gates fired (negative GEX, IV-rank floor, late-entry bias, VIX/ATR "
        "regime) and whether standing aside was the correct call — a flat day can be a good decision, not a "
        "gap.\n"
        "2. **Anticipate the operator's questions and answer them** — the obvious follow-ups, e.g. 'should "
        "more condors have been added here?', 'where was the risk?', 'was the flatness safe or a coiled "
        "setup?', 'why did that side stop?'. Address them directly and honestly.\n"
        "3. **Background / context** — enough regime and setup explanation that the reader understands the "
        "environment: what negative vs positive GEX implies here, why this IV rank / VIX level matters, how "
        "correlated the traded names were. Make it teach.\n"
        "4. **What worked, what didn't, and patterns worth watching** across sessions.\n"
        "5. **Upcoming events to watch** — a forward-looking section. Use **WebSearch** to research the "
        "notable macro events in the next ~2 weeks (FOMC / rate decisions, CPI, PCE, jobs / NFP, PPI, GDP, "
        "major Fed speakers) and any highly anticipated earnings (mega-caps and high-implied-move names). "
        "Anchor on the deterministic 'suite market calendar' block provided below (FOMC / expiries / "
        "holidays are authoritative from it); use search for the data-release dates and earnings names. "
        "For each, note the date and, briefly, the likely effect on these strategies (e.g. an event day "
        "pushes VIX1D up and MEIC's regime gate stands aside; a big single-name print is an earnings "
        "opportunity or an underlying-move risk). Give dates as specifically as the sources allow, and "
        "flag where a date/expectation is uncertain or should be verified — do not invent specifics.\n"
        "6. **2–4 concrete paper-tuning recommendations** (gates, widths, deltas, stops, timing) — or, if "
        "the day validated the current settings, say so plainly.\n\n"
        "Be specific and quantitative where the reports allow, but prefer the 'why' over restating figures. "
        "Depth over brevity is welcome (~450–900 words). Paper/educational only — not financial advice."
    )


def _run_claude(prompt: str, stdin_text: str, model: str | None, timeout: int,
                research: bool = False) -> dict:
    """Invoke Claude Code headless (print mode). Dangerous tools are always denied; WebSearch is granted
    only when `research` is on (for the upcoming-events section). Injectable seam so tests never call the
    real CLI. Returns {"ok": True, "text": ...} or {"ok": False, "error": ...}."""
    disallowed = list(_DISALLOWED_TOOLS) if research else [*_DISALLOWED_TOOLS, "WebSearch"]
    cmd = ["claude", "-p", prompt, "--output-format", "text",
           "--disallowed-tools", *disallowed,
           "--append-system-prompt", _SYSTEM]
    if research:
        # Grant WebSearch and bound the tool-use loop so a research session can't run away.
        cmd += ["--allowed-tools", "WebSearch", "--max-turns", "8"]
    if model:
        cmd += ["--model", model]
    try:
        # Force UTF-8 on the pipe: the reports contain non-cp1252 characters (e.g. the Δ in MEIC's
        # greeks), and text=True would otherwise encode stdin with the Windows locale and blow up.
        r = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"claude invocation failed: {exc}"}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "claude nonzero exit").strip()[:300]}
    text = (r.stdout or "").strip()
    return {"ok": True, "text": text} if text else {"ok": False, "error": "empty output"}


def _wrap(day: str, text: str, researched: bool) -> str:
    tools = ("read-only over the day's reports, with web search for the upcoming-events section"
             if researched else "read-only over the day's reports, no tools")
    return (
        f"# cherrypick - EOD Insight {day}\n\n"
        f"_AI-synthesized narrative over the day's deterministic paper reports (Claude Code, {tools}). "
        "Paper data only; educational, not financial advice — the deterministic eod-analysis files remain "
        "the source of record, and any forward-looking / researched notes are best-effort, verify before "
        "relying on them._\n\n"
        f"{text.strip()}\n"
    )


def run(cfg: dict | None = None, day: str | None = None) -> dict:
    """Write `eod-insight-<day>.md` from a Claude synthesis of the day's deterministic reports. Opt-in and
    feature-detected; best-effort (never raises). Returns a summary dict — `skipped` when disabled / no
    claude / no reports, `error` on a failed call, else the path written."""
    cfg = cfg or cfgmod.load_config()
    st = cfgmod.insight_settings(cfg)
    day = day or timeutil.now_et(cfg.get("timezone", "America/New_York")).strftime("%Y-%m-%d")
    if not st["enabled"]:
        return {"ok": False, "session": day, "skipped": "disabled"}
    if not _claude_available():
        return {"ok": False, "session": day, "skipped": "claude_not_found"}
    inputs = _gather_inputs(cfg, day)
    if not inputs:
        return {"ok": False, "session": day, "skipped": "no_reports"}
    research = bool(st.get("research_events", True))
    stdin_text = build_input(inputs)
    if research:
        cal = _upcoming_calendar(day)
        if cal:
            stdin_text += "\n\n" + cal
    # Web research needs headroom over the offline synthesis, so give the research path a longer timeout.
    timeout = st.get("timeout_seconds", 120)
    if research:
        timeout = max(timeout, 300)
    res = _run_claude(_prompt(day), stdin_text, st.get("model"), timeout, research=research)
    if not res.get("ok"):
        return {"ok": False, "session": day, "error": res.get("error")}
    path = cfgmod.log_file(f"eod-insight-{day}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_wrap(day, res["text"], research), encoding="utf-8")
    return {"ok": True, "session": day, "insight": str(path), "sources": [lbl for lbl, _ in inputs]}
