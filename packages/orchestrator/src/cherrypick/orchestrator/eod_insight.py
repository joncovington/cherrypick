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
from pathlib import Path

from . import config as cfgmod
from . import timeutil

# The agent gets no execution, file-write, or network tools. Read-only tools (Read/Glob/Grep) are
# harmless (local report files) and left alone; everything that could run a command, mutate the tree,
# or leave the box is denied. No MCP config is passed, so there are no MCP tools either.
_DISALLOWED_TOOLS = ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

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
        "5. **2–4 concrete paper-tuning recommendations** (gates, widths, deltas, stops, timing) — or, if "
        "the day validated the current settings, say so plainly.\n\n"
        "Be specific and quantitative where the reports allow, but prefer the 'why' over restating figures. "
        "Depth over brevity is welcome (~400–800 words). Paper/educational only — not financial advice."
    )


def _run_claude(prompt: str, stdin_text: str, model: str | None, timeout: int) -> dict:
    """Invoke Claude Code headless (print mode, no dangerous tools). Injectable seam so tests never call
    the real CLI. Returns {"ok": True, "text": ...} or {"ok": False, "error": ...}."""
    cmd = ["claude", "-p", prompt, "--output-format", "text",
           "--disallowed-tools", *_DISALLOWED_TOOLS,
           "--append-system-prompt", _SYSTEM]
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


def _wrap(day: str, text: str) -> str:
    return (
        f"# cherrypick - EOD Insight {day}\n\n"
        "_AI-synthesized narrative over the day's deterministic paper reports (Claude Code, read-only, "
        "no tools). Paper data only; educational, not financial advice — the deterministic eod-analysis "
        "files remain the source of record._\n\n"
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
    res = _run_claude(_prompt(day), build_input(inputs), st.get("model"), st.get("timeout_seconds", 120))
    if not res.get("ok"):
        return {"ok": False, "session": day, "error": res.get("error")}
    path = cfgmod.log_file(f"eod-insight-{day}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_wrap(day, res["text"]), encoding="utf-8")
    return {"ok": True, "session": day, "insight": str(path), "sources": [lbl for lbl, _ in inputs]}
