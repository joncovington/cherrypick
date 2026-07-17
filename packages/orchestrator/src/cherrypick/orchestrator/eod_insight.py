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
    "You are a read-only paper-trading analyst for the cherrypick suite. All data is PAPER (simulated), "
    "for education and research only — NOT financial, investment, or trading advice, and never a "
    "recommendation to place a live order. Analyze only the reports provided; do not use any tools."
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
        "(MEIC 0DTE iron condors on indices/ETFs, plus defined-risk earnings plays). Write a concise, "
        "plain-English **insight brief** for the trader:\n\n"
        "- What actually happened and why — tie exits, cost drag, and market context together, and call "
        "out differences across modules/symbols.\n"
        "- What worked, what didn't, and any pattern worth watching over coming sessions.\n"
        "- 2–4 concrete, actionable recommendations (paper-tuning only: gates, widths, stops, timing).\n\n"
        "Keep it under ~400 words, markdown, with short bold section labels. This is paper data for "
        "research — educational, not financial advice."
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
