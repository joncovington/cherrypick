"""Hourly suite status digest — one Discord card: the day so far, live first.

The card answers three questions in reading order: what is live money doing, how did the paper
arms do, and does anything need fixing. Live leads because it is the only part a person might act
on mid-session; paper is one line per module; health is shown only when something is wrong (loop
iterations, mark counts and blocked-attempt tallies are the console's job, not a phone's).

Composes what the suite already records rather than computing anything new: the review package's
fact set (refreshed to a provisional build for today), the live ledgers through `report.live_run`
plus flies' own book roll-up and live marks, the broker reconcile snapshot, the arm records, the
watchdog's last snapshot, the morning overview pack's phase, and the live halt flag. The one
subprocess is `python -m cherrypick.review build` — this package drives the review by subprocess,
never by import, and an extra provisional build is the same idempotent operation the 16:30
review-provisional job performs.

Posture: a network-calling notifier gets its own supervisor job (`status-digest`), never a
watchdog-tick call — the desk-notify rule. Every input is a file another job wrote, read-only; a
failed pass costs a Discord post and nothing else. The broker is never called: the broker's view
is whatever the last reconcile wrote, with its age.

Two suite conventions this module must never regress on:
- **Null is never zero.** An unmeasured figure renders as an em dash, not $0 — a broken input has
  to look broken, because the hour it is broken is exactly the hour a $0 would mislead.
- **No suite-level net.** The review package refuses to sum across modules on purpose (one module
  dominates; see its `concentration` block). The card shows per-module nets — live and paper
  apart — and lets the reader decide whether to add them.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime
from typing import Any

from cherrypick.core import home as corehome
from cherrypick.core.config import first_present as _first_present

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import liveops, report, timeutil
from .util import CREATE_NO_WINDOW, read_json


def _state_path():
    """Resolved per call, not at import: tests repoint cfgmod.STATE_DIR at a tmp dir (the
    isolated_state fixture), and a module-level constant would dodge that and write real state."""
    return cfgmod.STATE_DIR / "status_digest.json"


_FIELD_MAX = 1024  # Discord's per-field value limit; over it the whole message is rejected
_DASH = "—"  # em dash: the render of "not measured", never 0
_MAX_FINDINGS = 4
_STALE_MINUTES = 20  # an input older than this says its age; a fresh one doesn't spend the words

# Card color = the worst of (live broker verdict, morning phase, watchdog overall) so the color
# answers "do I need to open this" before a single field is read.
_COLOR_GREEN = 0x10B981
_COLOR_AMBER = 0xF59E0B
_COLOR_RED = 0xEF4444
_COLOR_SLATE = 0x6B7280  # unknown — a missing input blocks green, same rule as the morning gates

# Flies' structure kinds as the card says them. A legged entry's row turns into `fly` when it
# completes, so an open vertical is an UNCOMPLETED leg — the live exposure worth a glance.
_FLY_KINDS = {"fly": "fly", "bwb": "bwb", "short_vertical": "open vertical", "long_vertical": "open vertical"}


def _money(x) -> str:
    if x is None:
        return _DASH
    return f"+${x:,.0f}" if x >= 0 else f"-${abs(x):,.0f}"


def _dollars(x) -> str:
    """An unsigned amount (capital at risk) — a sign there would read as P&L."""
    return _DASH if x is None else f"${x:,.0f}"


def _age_minutes(ts) -> int | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return max(0, int((datetime.now(t.tzinfo) - t).total_seconds() // 60))


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


# --------------------------------------------------------------------------- live inputs (files)
def _fly_intraday(db_path, session: str) -> dict:
    """Flies' live day in its own terms: structures by kind, the book roll-up (net cash, the true
    worst case and the band it settles green over) and the latest net-of-fees mark. The generic
    ledger readers see none of this — a 0DTE book has no closes and no overnight carry until the
    bell. Best-effort per table: an older ledger missing one degrades to fewer facts, not an error,
    and a ledger that will not open comes back as {"error": ...} for the card to show.

    An entry still working at the broker (`entry_fill_status = 'pending'`) is not a held structure,
    the same exclusion the module's own `fly.held` makes. Marks are kept per (arm, symbol), so a
    second book never borrows the first one's mark or spot."""
    out: dict[str, Any] = {}
    try:
        conn = report._connect_ro(db_path)
    except sqlite3.Error as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}
    try:
        try:
            pos_cols = {r[1] for r in conn.execute("PRAGMA table_info(fly_positions)")}
        except sqlite3.Error:
            pos_cols = set()
        held = (
            " AND COALESCE(p.entry_fill_status, '') != 'pending'" if "entry_fill_status" in pos_cols else ""
        )
        try:
            kinds: dict[str, int] = {}
            for kind, n in conn.execute(
                "SELECT p.kind, COUNT(*) FROM fly_positions p WHERE p.trade_date = ?"
                " AND p.status NOT IN ('cancelled', 'voided')" + held + " GROUP BY p.kind",
                (session,),
            ):
                label = _FLY_KINDS.get(kind, kind)
                kinds[label] = kinds.get(label, 0) + int(n)
            out["kinds"] = kinds
            open_n = conn.execute(
                "SELECT COUNT(*) FROM fly_positions p WHERE p.trade_date = ? AND p.status = 'open'" + held,
                (session,),
            ).fetchone()[0]
            out["open"] = int(open_n)
        except sqlite3.Error:
            pass
        try:
            cols = (
                "arm",
                "symbol",
                "net_cash",
                "worst",
                "worst_at",
                "floor_holds",
                "band_low",
                "band_high",
                "unbounded_below",
                "pnl",
            )
            out["books"] = [
                dict(zip(cols, r, strict=True))
                for r in conn.execute(
                    f"SELECT {', '.join(cols)} FROM fly_books WHERE trade_date = ?", (session,)
                )
            ]
        except sqlite3.Error:
            pass
        try:
            marks = {}
            for arm, symbol, pnl, spot, ts in conn.execute(
                "SELECT p.arm, p.symbol, SUM(m.mark_pnl), MAX(m.spot), m.iteration_ts"
                " FROM fly_live_marks m JOIN fly_positions p ON p.position_id = m.position_id"
                " WHERE m.trade_date = ? AND m.iteration_ts ="
                " (SELECT MAX(iteration_ts) FROM fly_live_marks WHERE trade_date = ?)"
                + held
                + " GROUP BY p.arm, p.symbol",
                (session, session),
            ):
                marks[f"{arm}:{symbol}"] = {"pnl": pnl, "spot": spot, "ts": ts}
            out["marks"] = marks
        except sqlite3.Error:
            pass
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        conn.close()
    return out


def _live_inputs(cfg: dict, session: str) -> dict:
    """Everything the LIVE field needs, from files only. A module appears when it is armed today
    or has live rows today/on the book; one that is live-enabled but unarmed is named once, so a
    forgotten arm is visible without a block of its own. A ledger that fails to read is always
    shown, armed or not — it may hold open positions, and a broken input has to look broken.
    Each module is read on its own: one failure costs that module's lines, never the post."""
    from .supervisor import read_arm_records  # lazy: the supervisor module is heavy to import

    modules: dict[str, dict] = {}
    not_armed: list[str] = []
    try:
        lr = report.live_run(cfg, session)
    except Exception as exc:  # a broken live read must say so, never read as "no live today"
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}
    try:
        arm_records = read_arm_records(cfg)
    except Exception:
        arm_records = {}
    for name, env in (lr.get("modules") or {}).items():
        reason = env.get("reason")
        if not env.get("ok") and reason == "no live_db configured":
            continue
        rec = arm_records.get(name) or {}
        armed = rec.get("date") == session
        block: dict[str, Any] = {"armed": armed, "ok": True}
        if armed and isinstance(rec.get("intraday_agent"), dict):
            block["agent"] = rec["intraday_agent"].get("mode")
        try:
            if env.get("ok"):
                block["closed"] = {k: env.get(k) for k in ("trades", "net_pnl", "wins", "losses")}
                block["carried"] = env.get("open") or {}
                if env.get("schema") == "fly_book":
                    db_path = cfgmod.live_db_path(cfg["modules"][name], name)
                    block["fly"] = _fly_intraday(db_path, session)
                    if block["fly"].get("error"):
                        block.update(ok=False, reason=f"read failed: {block['fly']['error']}")
            elif reason != "no live ledger yet":  # a missing file before the first trade is normal
                block.update(ok=False, reason=reason)
        except Exception as exc:
            block.update(ok=False, reason=f"{type(exc).__name__}: {exc}"[:200])
        active = (
            armed
            or not block["ok"]
            or (block.get("closed") or {}).get("trades")
            or (block.get("carried") or {}).get("positions")
            or (block.get("fly") or {}).get("kinds")
        )
        if active:
            modules[name] = block
            continue
        try:
            root = cfgmod.module_root(cfg["modules"][name], name)
            flag, _src = liveops._live_enabled(name, root) if root.exists() else (None, None)
        except Exception:
            flag = None
        if flag:
            not_armed.append(name)
    broker = read_json(cfgmod.STATE_DIR / "live_positions.last.json")
    return {
        "modules": modules,
        "not_armed": not_armed,
        "broker": broker if isinstance(broker, dict) else None,
    }


# --------------------------------------------------------------------------- pure formatting
def _status_line(
    morning: dict | None, watchdog: dict | None, facts: dict | None, session: str
) -> tuple[str, str, str]:
    """(one-line card description, phase lowercase or '', watchdog overall or '')."""
    bits: list[str] = []
    phase = ""
    if morning:
        ph = morning.get("phase") or {}
        phase = str(ph.get("phase") or "").lower()
        s = phase.upper() or _DASH
        if ph.get("gates_met") is not None and ph.get("gates_total") is not None:
            s += f" {ph['gates_met']}/{ph['gates_total']}"
        if phase != "green" and ph.get("reason"):
            # The gate's name only; its reference figures are the morning card's job.
            reason = str(ph["reason"]).removeprefix("blocked by: ").split(" (")[0]
            s += f" ({reason[:1].lower()}{reason[1:]})"
        bits.append(s)
    else:
        bits.append(f"phase {_DASH}")
    overall = str((watchdog or {}).get("overall") or "")
    wd = f"watchdog {overall or _DASH}"
    if watchdog is None:
        wd += " (no snapshot)"
    else:
        age = _age_minutes(watchdog.get("ts"))
        if age is not None and age > _STALE_MINUTES:
            wd += f" ({age}m old)"
    bits.append(wd)
    if facts is None:
        bits.append(f"no paper fact set for {session} yet")
    return " · ".join(bits), phase, overall


def _attention_lines(watchdog: dict | None) -> list[str]:
    """Non-OK findings, title plus the first sentence of the message — the job names, not the
    runbook prose that follows them."""
    bad = [f for f in (watchdog or {}).get("findings") or [] if f.get("status") not in (None, "OK")]
    lines = []
    for f in bad[:_MAX_FINDINGS]:
        msg = str(f.get("message") or "").split(". ")[0].rstrip(".")
        line = f"{f.get('status')}: {f.get('title')}"
        lines.append(f"{line} — {msg}"[:200] if msg else line)
    if len(bad) > _MAX_FINDINGS:
        lines.append(f"…and {len(bad) - _MAX_FINDINGS} more")
    return lines


def _closed_bit(trades, net, wins, losses) -> str:
    wl = f" {wins}W/{losses}L" if wins is not None and losses is not None else ""
    return f"{trades} closed{wl} {_money(net)}"


# The reconcile's verdicts (livepositions.py) by how loudly the card should say them. IDLE is "nothing
# armed, the broker was not asked" and SETTLING a difference seen once (by design not an alarm, it
# usually clears on the next pass); only a held MISMATCH turns the card red, as the watchdog grades it.
_BROKER_SEVERITY = {"MATCH": None, "IDLE": None, "SETTLING": "amber", "UNKNOWN": "amber", "MISMATCH": "red"}


def _broker_line(broker: dict | None) -> tuple[str, str | None]:
    """(line, severity: None / 'amber' / 'red'). The reconcile's word and its age."""
    if not broker:
        return f"broker {_DASH} (no reconcile snapshot)", None
    verdict = str(broker.get("verdict") or _DASH)
    pending = sum(int(a.get("pending_orders") or 0) for a in broker.get("accounts") or [])
    line = f"broker {verdict}"
    if pending:
        line += f" · {pending} order{'s' if pending != 1 else ''} working"
    age = _age_minutes(broker.get("generated_at"))
    if age is not None and age > _STALE_MINUTES:
        line += f" ({age}m old)"
    return line, _BROKER_SEVERITY.get(verdict, "amber")


def _plural(kind: str, n: int) -> str:
    if n == 1 or kind == "bwb":
        return kind
    return "flies" if kind == "fly" else kind + "s"


def _fly_live_lines(fly: dict) -> list[str]:
    lines = []
    kinds = fly.get("kinds") or {}
    if kinds:
        order = sorted(kinds.items(), key=lambda kv: (kv[0] == "open vertical", kv[0]))
        lines.append(" · ".join(f"{n} {_plural(k, n)}" for k, n in order))
    books = fly.get("books") or []
    marks = fly.get("marks") or {}
    for b in books:
        if b.get("pnl") is not None:
            lines.append(f"{b.get('arm')} {b.get('symbol')} settled {_money(b['pnl'])}")
            continue
        mark = marks.get(f"{b.get('arm')}:{b.get('symbol')}") if fly.get("open") else None
        parts = []
        if mark:
            s = f"mark {_money(mark.get('pnl'))}"
            age = _age_minutes(mark.get("ts"))
            if age is not None and age > _STALE_MINUTES:
                s += f" ({age}m old)"  # a dead loop's last mark must not read as current
            parts.append(s)
        # A book-level floor always carries the band over which it holds (root CLAUDE.md).
        band = None
        if b.get("band_low") is not None and b.get("band_high") is not None:
            band = f"{b['band_low']:.0f}–{b['band_high']:.0f}"
        if b.get("floor_holds"):
            s = "floor holds" + (f" {band}" if band else "")
            if b.get("unbounded_below"):
                s += " (unbounded below)"
            parts.append(s)
        elif band:
            parts.append(f"green {band}")
        if b.get("worst") is not None:
            at = f" @{b['worst_at']:.0f}" if b.get("worst_at") is not None else ""
            parts.append(f"worst {_money(b['worst'])}{at}")
        if mark and mark.get("spot") is not None:
            parts.append(f"spot {mark['spot']:.0f}")
        if parts:
            prefix = f"{b.get('arm')} {b.get('symbol')} · " if len(books) > 1 else ""
            lines.append(prefix + " · ".join(parts))
    return lines


_HALT_LINE = "\U0001f6d1 LIVE HALT FLAG IS SET"


def _live_field(live: dict | None, halted: bool) -> tuple[str, str | None]:
    """(field value, broker severity)."""
    lines: list[str] = []
    if halted:
        lines.append(_HALT_LINE)
    if live is None:
        lines.append(f"live {_DASH} (not read)")
        return "\n".join(lines), None
    if live.get("error"):
        lines.append(f"live read failed: {live['error']}")
        return "\n".join(lines), None

    modules = live.get("modules") or {}
    for name, b in modules.items():
        head = f"**{name}**"
        head += " armed" if b.get("armed") else " not armed today"
        if b.get("agent"):
            head += f" · agent {b['agent']}"
        lines.append(head)
        if not b.get("ok"):
            lines.append(f"unreadable: {b.get('reason') or 'unknown'}")
            continue
        if b.get("fly"):
            lines.extend(_fly_live_lines(b["fly"]))
        c = b.get("closed") or {}
        if c.get("trades"):
            lines.append(_closed_bit(c["trades"], c.get("net_pnl"), c.get("wins"), c.get("losses")))
        carried = b.get("carried") or {}
        if carried.get("positions"):
            unknown = carried.get("capital_unknown") or 0
            tail = f" (+{unknown} unknown)" if unknown else ""
            lines.append(
                f"{carried['positions']} open · {_dollars(carried.get('capital_at_risk'))} at risk{tail}"
            )
        if (
            b.get("armed")
            and not b.get("fly", {}).get("kinds")
            and not c.get("trades")
            and not carried.get("positions")
        ):
            lines.append("no live entries yet")
    if not modules:
        lines.append("nothing live today")
    if live.get("not_armed"):
        lines.append("not armed: " + ", ".join(live["not_armed"]))
    severity = None
    if modules or live.get("broker"):
        bline, severity = _broker_line(live.get("broker"))
        lines.append(("⚠ " if severity else "") + bline)
    return "\n".join(lines), severity


def _paper_line(name: str, block: dict, prev_mod: dict | None) -> tuple[str, bool]:
    """(one line, quiet). Quiet = measured and nothing happened: folded into one 'quiet:' line."""
    if not block.get("ok"):
        return f"**{name}** ⚠ unreadable: {block.get('reason') or 'unknown'}", False
    prev_mod = prev_mod or {}
    health = block.get("health") or {}
    results = block.get("results") or {}
    carried = block.get("carried_overnight") or {}
    entries, completions, closed = health.get("entries"), health.get("completions"), results.get("closed")

    parts = []
    if entries is None:
        parts.append(f"{_DASH} in")
    elif entries:
        s = f"{entries} in"
        if completions:
            s += f" ({completions} done)"
        parts.append(s)
    if closed:
        s = _closed_bit(closed, results.get("net"), results.get("wins"), results.get("losses"))
        prev_closed, prev_net = prev_mod.get("closed"), prev_mod.get("net")
        if (
            prev_closed is not None
            and closed != prev_closed
            and results.get("net") is not None
            and prev_net is not None
        ):
            s += f" ({_money(results['net'] - prev_net)} since last)"
        parts.append(s)
    if carried.get("positions"):
        unknown = carried.get("capital_unknown") or 0
        tail = f" (+{unknown} unknown)" if unknown else ""
        parts.append(f"{carried['positions']} open {_dollars(carried.get('capital_at_risk'))} at risk{tail}")

    flags = []
    if health and not health.get("loop_ticked"):
        flags.append("⚠ loop not ticked")
    conc = block.get("concentration") or {}
    if conc.get("sign_flips_without_largest"):
        # `arm` from fact_version 8, `profile` in the sets written before it.
        largest = _first_present(conc.get("largest"), "arm", "profile") or "largest arm"
        flags.append(f"⚠ net sign rests on {largest}")

    if not parts and not flags:
        return name, True
    return f"**{name}** " + " · ".join(parts + flags), False


def _card_color(phase: str, overall: str, broker: str | None = None, halted: bool = False) -> int:
    if broker == "red" or overall == "CRITICAL" or phase == "red":
        return _COLOR_RED
    if broker == "amber" or halted or overall == "WARN" or phase == "yellow":
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
    close: bool = False,
    live: dict | None = None,
) -> tuple[str, str, dict, dict]:
    """The digest as (title, canonical message, Discord embed, snapshot-for-next-delta).

    Pure over its inputs so tests can pin the two conventions (null renders as an em dash; no
    suite-level net anywhere in the output) without touching a file or a clock.
    """
    # The close card is the same composition with the day settled — the 0DTE books have expired by
    # 16:35, so its figures are the day's final intraday word, delta'd against the last hourly post.
    title = f"{'CLOSE' if close else 'DIGEST'} · SUITE {hhmm} ET"
    status, phase, overall = _status_line(morning, watchdog, facts, session)
    live_value, broker_severity = _live_field(live, halted)

    fields = [{"name": "Live", "value": live_value[:_FIELD_MAX]}]
    snapshot: dict[str, Any] = {"session": session, "hhmm": hhmm, "modules": {}}
    msg_bits: list[str] = []

    prev_modules = (prev or {}).get("modules") or {}
    paper_lines: list[str] = []
    quiet: list[str] = []
    for name, block in ((facts or {}).get("modules") or {}).items():
        line, is_quiet = _paper_line(name, block, prev_modules.get(name))
        (quiet if is_quiet else paper_lines).append(line)
        results = block.get("results") or {}
        if block.get("ok"):
            # Only what the next post's "since last" reads.
            snapshot["modules"][name] = {"closed": results.get("closed"), "net": results.get("net")}
            closed = results.get("closed")
            if closed:
                msg_bits.append(f"{name} {_money(results.get('net'))}/{closed}")
        else:
            msg_bits.append(f"{name} unreadable")
    if quiet:
        paper_lines.append("quiet: " + ", ".join(quiet))
    if facts is not None and facts.get("status") and facts["status"] != "final":
        paper_lines.append(f"_{facts['status']} figures_")
    if paper_lines:
        fields.append({"name": "Paper", "value": "\n".join(paper_lines)[:_FIELD_MAX]})

    attention = _attention_lines(watchdog)
    if attention:
        fields.append({"name": "Attention", "value": "\n".join(attention)[:_FIELD_MAX]})

    # The halt is already in the message as LIVE HALTED; don't spend a live slot repeating it.
    live_bits = [ln for ln in live_value.splitlines() if ln and not ln.startswith("**") and ln != _HALT_LINE]
    message = f"Suite {'close' if close else 'digest'} {hhmm} ET — {status}"
    if halted:
        message += " · LIVE HALTED"
    if live_bits:
        message += " | live: " + "; ".join(live_bits[:3])
    if msg_bits:
        message += " | paper: " + " · ".join(msg_bits)

    embed = {
        "title": title[:256],
        "description": status[:4096],
        "color": _card_color(phase, overall, broker_severity, halted),
        "fields": fields[:25],
    }
    return title, message, embed, snapshot


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None, force: bool = False, close: bool = False) -> dict:
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
    live = _live_inputs(cfg, session)

    prev = read_json(_state_path())
    if not (isinstance(prev, dict) and prev.get("session") == session):
        prev = None  # yesterday's watermark must not produce a delta against today

    title, message, embed, snapshot = build_digest(
        session, hhmm, facts, watchdog, morning, halted, prev, close=close, live=live
    )
    notifier = Notifier({**cfg.get("notify", {}), "channels": settings["channels"]})
    # No inputs fingerprinted: the provisional fact set is rebuilt every hour by design, so every
    # earlier card would read as stale. The card is a snapshot of its hour, and says so.
    results = notifier.notify(
        "INFO", "status.digest", title, message, embed=embed, kind="digest", session=session
    )

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
