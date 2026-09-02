"""Hourly suite status digest — one Discord card summarizing the day so far, every module at once.

Composes what the suite already records rather than computing anything new: the review package's
fact set (refreshed to a provisional build for today), the watchdog's last snapshot, the morning
overview pack's phase, and the live halt flag. The one subprocess is `python -m cherrypick.review
build` — this package drives the review by subprocess, never by import, and an extra provisional
build is the same idempotent operation the 16:30 review-provisional job performs (the .md render it
overwrites is regenerated on every build; the narrative .note.md is a separate file it never
touches).

Posture: a network-calling notifier gets its own supervisor job (`status-digest`), never a
watchdog-tick call — the desk-notify rule. Every input is a file another job wrote; a failed pass
costs a Discord post and nothing else.

Two suite conventions this module must never regress on:
- **Null is never zero.** An unmeasured figure renders as an em dash, not $0 — a broken input has
  to look broken, because the hour it is broken is exactly the hour a $0 would mislead.
- **No suite-level net.** The review package refuses to sum across modules on purpose (one module
  dominates; see its `concentration` block). The card shows per-module nets and lets the reader
  decide whether to add them.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Any

from cherrypick.core import home as corehome

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import liveops, timeutil
from .util import CREATE_NO_WINDOW, read_json


def _state_path():
    """Resolved per call, not at import: tests repoint cfgmod.STATE_DIR at a tmp dir (the
    isolated_state fixture), and a module-level constant would dodge that and write real state."""
    return cfgmod.STATE_DIR / "status_digest.json"


_FIELD_MAX = 1024  # Discord's per-field value limit; over it the whole message is rejected
_DASH = "—"  # em dash: the render of "not measured", never 0

# Card color = the worst of (morning phase, watchdog overall) so the color answers "do I need to
# open this" before a single field is read. Same palette family as trade_notifier's cards.
_COLOR_GREEN = 0x10B981
_COLOR_AMBER = 0xF59E0B
_COLOR_RED = 0xEF4444
_COLOR_SLATE = 0x6B7280  # unknown — a missing input blocks green, same rule as the morning gates


def _money(x) -> str:
    if x is None:
        return _DASH
    return f"+${x:,.0f}" if x >= 0 else f"-${abs(x):,.0f}"


def _refresh_facts(timeout_s: int = 600) -> str | None:
    """Rebuild today's provisional fact set so the card describes this hour, not 16:30 yesterday.
    Best-effort: on failure the caller reads whatever artifact exists and says it is stale."""
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), "-m", "cherrypick.review", "build"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            return (r.stderr or r.stdout or "review build failed").strip()[:300]
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:300]


def _load_session_artifact(path, session: str) -> dict | None:
    """A dated artifact, only if it actually describes `session` — yesterday's fact set presented
    as today's would be worse than none."""
    doc = read_json(path)
    if isinstance(doc, dict) and str(doc.get("session")) == session:
        return doc
    return None


# --------------------------------------------------------------------------- pure formatting
def _phase_line(morning: dict | None) -> tuple[str, str]:
    """(one-line phase summary, phase name lowercase or '')."""
    if not morning:
        return f"morning phase {_DASH}", ""
    ph = morning.get("phase") or {}
    name = str(ph.get("phase") or "").lower()
    line = f"morning phase {name.upper() or _DASH}"
    gates_met, gates_total = ph.get("gates_met"), ph.get("gates_total")
    if gates_met is not None and gates_total is not None:
        line += f" ({gates_met}/{gates_total} gates)"
    reason = ph.get("reason")
    if reason and name != "green":
        line += f" — {reason}"
    return line, name


def _watchdog_lines(watchdog: dict | None) -> tuple[list[str], str]:
    """(lines for the header field, overall status or '')."""
    if not watchdog:
        return [f"watchdog {_DASH} (no snapshot)"], ""
    overall = str(watchdog.get("overall") or "")
    age = ""
    try:
        ts = datetime.fromisoformat(str(watchdog.get("ts")))
        minutes = max(0, int((datetime.now(ts.tzinfo) - ts).total_seconds() // 60))
        age = f", {minutes}m old"
    except (TypeError, ValueError):
        pass
    session_bit = "in session" if watchdog.get("in_session") else "off-session"
    lines = [f"watchdog {overall or _DASH} ({session_bit}{age})"]
    bad = [f for f in watchdog.get("findings") or [] if f.get("status") not in (None, "OK")]
    for f in bad[:5]:
        lines.append(f"{f.get('status')}: {f.get('title')} — {f.get('message')}")
    if len(bad) > 5:
        lines.append(f"…and {len(bad) - 5} more findings")
    return lines, overall


def _int_delta(cur, prev) -> str:
    """A count's since-last-post movement, only when both sides are measured — an unmeasured side
    must produce no delta rather than a delta against zero."""
    if isinstance(cur, int) and isinstance(prev, int) and cur != prev:
        return f" (Δ {cur - prev:+d})"
    return ""


def _module_lines(name: str, block: dict, prev_mod: dict | None) -> list[str]:
    if not block.get("ok"):
        return [f"unreadable: {block.get('reason') or 'unknown'}"]
    lines: list[str] = []
    prev_mod = prev_mod or {}
    health = block.get("health") or {}

    # Opened vs completed vs closed are three different populations (flies' lifecycle makes all
    # three visible at once), so the activity line names each it can measure.
    entries = health.get("entries")
    parts = [
        f"entered {entries if entries is not None else _DASH}" + _int_delta(entries, prev_mod.get("entries"))
    ]
    completions = health.get("completions")
    if completions is not None:
        parts.append(f"completed {completions}" + _int_delta(completions, prev_mod.get("completions")))

    results = block.get("results") or {}
    closed = results.get("closed")
    if closed:
        wl = ""
        if results.get("wins") is not None and results.get("losses") is not None:
            wl = f" ({results['wins']}W/{results['losses']}L)"
        line = f"closed {closed}{wl} · net {_money(results.get('net'))}"
        if closed != prev_mod.get("closed") and prev_mod.get("closed") is not None:
            d_closed = closed - int(prev_mod.get("closed") or 0)
            d_net = None
            if results.get("net") is not None and prev_mod.get("net") is not None:
                d_net = results["net"] - prev_mod["net"]
            line += f" (Δ +{d_closed}" + (f", {_money(d_net)}" if d_net is not None else "") + ")"
        parts.append(line)
    else:
        parts.append("no closes yet")
    lines.append(" · ".join(parts))

    conc = block.get("concentration") or {}
    if conc.get("sign_flips_without_largest"):
        largest = (conc.get("largest") or {}).get("profile") or "largest arm"
        lines.append(f"⚠ net sign rests on {largest}")

    health = block.get("health") or {}
    attempts = health.get("entry_attempts") or {}
    if attempts:
        top = sorted(attempts.items(), key=lambda kv: -int(kv[1] or 0))[:3]
        lines.append("attempts: " + " · ".join(f"{k} {v}" for k, v in top))

    carried = block.get("carried_overnight") or {}
    if carried.get("positions"):
        lines.append(f"open {carried['positions']} · at risk {_money(carried.get('capital_at_risk'))}")

    if health:
        if health.get("loop_ticked"):
            loop = "loop ✓"
            if health.get("iterations") is not None:
                loop += f" ({health['iterations']} iterations)"
        else:
            loop = "loop NOT ticked"
        marks, refused = health.get("marks"), health.get("marks_refused")
        if marks is not None:
            loop += f" · marks {marks}" + (f" ({refused} refused)" if refused else "")
        lines.append(loop)

    return lines


def _card_color(phase: str, overall: str) -> int:
    if overall == "CRITICAL" or phase == "red":
        return _COLOR_RED
    if overall == "WARN" or phase == "yellow":
        return _COLOR_AMBER
    if overall == "OK" and phase == "green":
        return _COLOR_GREEN
    return _COLOR_SLATE


def build_digest(
    session: str,
    hhmm: str,
    facts: dict | None,
    watchdog: dict | None,
    morning: dict | None,
    halted: bool,
    prev: dict | None,
) -> tuple[str, str, dict, dict]:
    """The digest as (title, canonical message, Discord embed, snapshot-for-next-delta).

    Pure over its inputs so tests can pin the two conventions (null renders as an em dash; no
    suite-level net anywhere in the output) without touching a file or a clock.
    """
    title = f"DIGEST · SUITE {hhmm} ET"
    phase_line, phase = _phase_line(morning)
    wd_lines, overall = _watchdog_lines(watchdog)

    header = [phase_line, *wd_lines]
    if halted:
        header.append("\U0001f6d1 LIVE HALT FLAG IS SET")
    if facts is None:
        header.append(f"no fact set for {session} yet — module figures unavailable")
    elif facts.get("status"):
        header.append(f"figures are {facts['status']} (review fact set v{facts.get('fact_version', '?')})")

    fields = [{"name": "Suite", "value": "\n".join(header)[:_FIELD_MAX]}]
    snapshot: dict[str, Any] = {"session": session, "hhmm": hhmm, "modules": {}}
    msg_bits: list[str] = []

    prev_modules = (prev or {}).get("modules") or {}
    for name, block in ((facts or {}).get("modules") or {}).items():
        lines = _module_lines(name, block, prev_modules.get(name))
        fields.append({"name": name[:256], "value": "\n".join(lines)[:_FIELD_MAX]})
        results = block.get("results") or {}
        if block.get("ok"):
            health = block.get("health") or {}
            snapshot["modules"][name] = {
                "closed": results.get("closed"),
                "net": results.get("net"),
                "entries": health.get("entries"),
                "completions": health.get("completions"),
            }
            closed = results.get("closed")
            msg_bits.append(f"{name} {_money(results.get('net'))}/{closed}" if closed else f"{name} 0 closed")
        else:
            msg_bits.append(f"{name} unreadable")

    message = f"Suite digest {hhmm} ET — {phase_line} · watchdog {overall or _DASH}"
    if halted:
        message += " · LIVE HALTED"
    if msg_bits:
        message += " | " + " · ".join(msg_bits)

    embed = {"title": title[:256], "color": _card_color(phase, overall), "fields": fields[:25]}
    return title, message, embed, snapshot


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None, force: bool = False) -> dict:
    cfg = cfgmod.load_config() if cfg is None else cfg
    settings = cfgmod.status_digest_settings(cfg)
    now = timeutil.now_et(cfg.get("timezone", "America/New_York"))
    if not force and not timeutil.is_trading_day(now, timeutil.load_holidays([now.year])):
        return {"ok": True, "skipped": "not a trading day"}

    session = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    refresh_error = _refresh_facts()
    facts = _load_session_artifact(corehome.data_dir("review") / f"eod-{session}.json", session)
    morning = _load_session_artifact(corehome.data_dir("overview") / f"morning-{session}.json", session)
    watchdog = read_json(cfgmod.STATE_DIR / "watchdog.last.json")
    halted = liveops.halt_flag_path().exists()

    prev = read_json(_state_path())
    if not (isinstance(prev, dict) and prev.get("session") == session):
        prev = None  # yesterday's watermark must not produce a delta against today

    title, message, embed, snapshot = build_digest(session, hhmm, facts, watchdog, morning, halted, prev)
    notifier = Notifier({**cfg.get("notify", {}), "channels": settings["channels"]})
    results = notifier.notify("INFO", "status.digest", title, message, embed=embed)

    state_path = _state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(snapshot), encoding="utf-8")
    return {
        "ok": True,
        "session": session,
        "posted_at": hhmm,
        "facts": "fresh" if facts else "unavailable",
        "refresh_error": refresh_error,
        "channels": results,
    }
