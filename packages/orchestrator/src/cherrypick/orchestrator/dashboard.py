"""Unified status & log dashboard (read-only, file-only).

The read-side counterpart to the watchdog/notify write-side: one self-contained HTML page the
walk-away user can open to *see* suite health, per-module paper P&L, active alerts, and recent logs —
without attaching a terminal. It reads only files already on disk (the watchdog heartbeat, each
module's paper DB via `report`, and log tails) — never the broker, an MCP, or the network — so it adds
no failure mode to the reliability path. The page is static self-contained HTML (inline CSS/JS, no
server, no external assets); it is regenerated on each watchdog tick and by `cherrypick dashboard`.

Health comes from `state/watchdog.last.json` (already computed by the watchdog) rather than re-running
`doctor`, which shells out to the broker/streamer and would be wrong for a fast offline render.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import viz

from cherrypick.notify import secrets as notify_secrets

from . import calibrate, embeds, report, sections, tasks, timeutil
from . import config as cfgmod
from .util import CREATE_NO_WINDOW

_STATUS_COLORS = {
    "OK": "var(--pos)",
    "WARN": "var(--warn)",
    "CRITICAL": "var(--neg)",
    "INFO": "var(--accent)",
    "UNKNOWN": "var(--muted)",
}
_LEVELS = ("CRITICAL", "WARN", "INFO", "NOTIFY", "OK")


# --------------------------------------------------------------------------- file helpers
def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _age_minutes(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except ValueError:
        return None


def _tail(path: Path, n: int) -> list[str]:
    """Last n non-empty lines of a text file; never raises."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
        return lines[-n:]
    except OSError:
        return []


def _parse_log_line(source: str, raw: str) -> dict[str, Any]:
    """Normalize a log line to {source, level, ts, text}. Handles our JSON lines and plain text."""
    level = "INFO"
    ts = None
    text = raw
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        ts = obj.get("ts")
        raw_level = obj.get("level") or obj.get("overall") or obj.get("status")
        if raw_level:
            level = str(raw_level).upper()
        # Compact, human-readable text from the common fields we log.
        bits = [str(obj[k]) for k in ("title", "message", "kind", "phase", "error") if obj.get(k)]
        text = " — ".join(bits) if bits else json.dumps(obj, separators=(",", ":"))
    if level not in _LEVELS:
        level = "INFO"
    return {"source": source, "level": level, "ts": ts, "text": text}


def _git_ref(root: Path) -> str | None:
    """Short commit hash of a module checkout, best-effort. Local `git` only — no network, never
    blocks the render (returns None on any failure, e.g. not a git checkout or git missing)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout.strip() or None if r.returncode == 0 else None
    except OSError:
        return None


def _task_views(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Scheduled-task registry for the System panel. Local OS scheduler query only (`schtasks`/cron),
    same source of truth as `cherrypick status` (tasks.registry_snapshot)."""
    rows = []
    for name, info in tasks.registry_snapshot(cfg).items():
        rows.append(
            {
                "name": name,
                "exists": bool(info.get("exists")),
                "status": info.get("Status") or info.get("backend") or "—",
                "last_run": info.get("Last Run Time", "—"),
                "last_result": info.get("Last Result", "—"),
                "next_run": info.get("Next Run Time", "—"),
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows


def _modules_installed_views(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """What's configured/installed per module: location, source, paper kind, streamer, ladder.
    Filesystem + local `git` only — never the broker."""
    out = []
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        root = cfgmod.module_root(mcfg, name)
        source = f"in-place: {mcfg['path']}" if mcfg.get("path") else (mcfg.get("repo") or "—")
        paper = mcfg.get("paper", {})
        out.append(
            {
                "name": name,
                "root": str(root),
                "source": source,
                "git_ref": _git_ref(root) if root.exists() else None,
                "paper_kind": paper.get("kind", "—"),
                "streamer_enabled": bool(mcfg.get("streamer", {}).get("enabled")),
                "ladder": mcfg.get("calibration", {}).get("ladder") or [],
            }
        )
    return out


def _config_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    """A fixed allowlist view of config.json — never a raw dump, so a future config key can't leak
    onto the page by accident. Webhook URLs are never read from config (they live in the OS keyring);
    this only reports whether a push channel's webhook is *set*, via notify.secrets.status (same helper
    doctor.py uses), never the URL itself."""
    modules = {
        name: {
            "enabled": bool(mcfg.get("enabled")),
            "kind": mcfg.get("paper", {}).get("kind", "—"),
            "streamer_enabled": bool(mcfg.get("streamer", {}).get("enabled")),
            "ladder": mcfg.get("calibration", {}).get("ladder") or [],
        }
        for name, mcfg in cfg.get("modules", {}).items()
    }
    wd = cfg.get("watchdog", {})
    dash = cfg.get("dashboard", {})
    serve_cfg = dash.get("serve", {})
    notify_cfg = cfg.get("notify", {})
    push_channels = [c for c in notify_cfg.get("channels", []) if c in notify_secrets.SUPPORTED]
    return {
        "timezone": cfg.get("timezone", "—"),
        "modules": modules,
        "watchdog": {
            "interval_minutes": wd.get("interval_minutes"),
            "renotify_minutes": wd.get("renotify_minutes"),
            "drawdown_configured": bool(wd.get("drawdown")),
        },
        "dashboard": {
            "output": dash.get("output", "dashboard.html"),
            "serve_host": serve_cfg.get("host", "127.0.0.1"),
            "serve_port": serve_cfg.get("port", 8787),
            "sections": [
                {"id": s.get("id"), "enabled": bool(s.get("enabled"))} for s in dash.get("sections", []) or []
            ],
        },
        "trade_notify_interval_minutes": cfg.get("trade_notify", {}).get("interval_minutes"),
        "notify": {
            "channels": notify_cfg.get("channels", []),
            "trade_channels": notify_cfg.get("trade_channels", []),
            "webhooks": notify_secrets.status(push_channels) if push_channels else {},
        },
    }


# --------------------------------------------------------------------------- model
def _eod_view(cfg: dict[str, Any], modules_cfg: dict[str, Any], tz: str) -> dict[str, Any] | None:
    """Today's (ET) session roll-up for the EOD card. File-only (report reads paper DBs; the paper-eod
    pointers are file-existence checks) and best-effort — a hiccup returns None and the card is omitted.
    Computed live so it stays current between the scheduled digest runs, and without writing a file on
    every dashboard regen."""
    try:
        today = timeutil.now_et(tz).strftime("%Y-%m-%d")
        rep = report.run(cfg, session=today)
    except Exception:
        return None
    files = {}
    for name, mcfg in modules_cfg.items():
        p = cfgmod.module_root(mcfg) / "logs" / f"paper-eod-{today}.md"
        files[name] = str(p) if p.exists() else None
    return {
        "session": today,
        "suite": rep.get("suite", {}),
        "modules": {n: rep.get("modules", {}).get(n, {}) for n in modules_cfg},
        "files": files,
    }


def build_model(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the dashboard render model from on-disk state only (no broker/network)."""
    cfg = cfg or cfgmod.load_config()

    hb = _read_json(cfgmod.STATE_DIR / "watchdog.last.json")
    overall = (hb.get("overall") or "UNKNOWN").upper()
    findings = hb.get("findings", []) if isinstance(hb.get("findings"), list) else []

    tz = cfg.get("timezone", "America/New_York")
    et_clock = hb.get("et")
    if not et_clock:  # no watchdog heartbeat yet — fall back to a live ET clock for the header
        try:
            et_clock = timeutil.now_et(tz).isoformat()
        except Exception:
            et_clock = None

    pnl = report.run(cfg)
    # Per-profile promotion recommendations (advisory, file-only). Best-effort: a calibration hiccup
    # must never break the dashboard render.
    try:
        cal = calibrate.run(cfg)
    except Exception:
        cal = {"modules": {}}
    modules_cfg = cfgmod.enabled_modules(cfg)

    module_views = []
    for name, mcfg in modules_cfg.items():
        mrep = pnl.get("modules", {}).get(name, {})
        mfindings = [f for f in findings if str(f.get("key", "")).startswith(f"{name}.")]
        sla = {}
        if mcfg.get("paper", {}).get("kind") == "cherrypick_scheduled":
            sla = {
                "entry": _read_json(cfgmod.STATE_DIR / "earnings_entry.last.json"),
                "exit": _read_json(cfgmod.STATE_DIR / "earnings_exit.last.json"),
            }
        module_views.append(
            {
                "name": name,
                "pnl": mrep,
                "findings": mfindings,
                "sla": sla,
                "calibration": cal.get("modules", {}).get(name, {}),
                "mode": "PAPER",
            }
        )

    tail_n = int(cfg.get("dashboard", {}).get("log_tail_lines", 50))
    sources: list[tuple[str, Path]] = [
        ("watchdog", cfgmod.LOGS_DIR / "watchdog.log"),
        ("notify", cfgmod.LOGS_DIR / "notify.log"),
    ]
    for name, mcfg in modules_cfg.items():
        log_rel = mcfg.get("paper", {}).get("log")
        if log_rel:
            # Module logs now live in the shared logs home (~/.cherrypick/logs/<name>); take just the
            # filename from the configured path so a stale "logs/…" prefix still resolves correctly.
            sources.append((name, cfgmod.module_logs_dir(name) / Path(log_rel).name))
    log_entries: list[dict[str, Any]] = []
    for src, path in sources:
        for raw in _tail(path, tail_n):
            log_entries.append(_parse_log_line(src, raw))
    # Most recent last, by timestamp where available; undated lines keep their file order at the end.
    log_entries.sort(key=lambda e: (e["ts"] is None, e["ts"] or ""))
    log_entries = log_entries[-tail_n:]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "heartbeat_age_min": _age_minutes(hb.get("ts")),
        "et_clock": et_clock,
        "in_session": hb.get("in_session"),
        "is_trading_day": hb.get("is_trading_day"),
        "notify_channels": cfg.get("notify", {}).get("channels", ["log"]),
        "active_findings": [f for f in findings if str(f.get("status", "")).upper() in ("WARN", "CRITICAL")],
        "suite": pnl.get("suite", {}),
        "eod": _eod_view(cfg, modules_cfg, tz),
        "modules": module_views,
        "logs": log_entries,
        "tasks": _task_views(cfg),
        "modules_installed": _modules_installed_views(cfg),
        "config_summary": _config_summary(cfg),
        "sections": [
            {
                "id": s["id"],
                "title": s.get("title", s["id"]),
                "endpoint": f"/api/section/{s['id']}",
                "refresh": sections.refresh_seconds(s),
            }
            for s in sections.enabled_sections(cfg)
        ],
        "embeds": [
            {
                "id": e["id"],
                "title": e.get("title", e["id"]),
                "url": f"/embed/{e['id']}",
                "kind": e.get("kind", "static"),
            }
            for e in embeds.enabled_embeds(cfg)
        ],
    }


# --------------------------------------------------------------------------- rendering
def _color(status: str) -> str:
    return _STATUS_COLORS.get(str(status).upper(), _STATUS_COLORS["UNKNOWN"])


def _money(v: Any) -> str:
    try:
        return f"${float(v):+,.2f}"
    except (TypeError, ValueError):
        return "—"


def _pill(text: str, status: str) -> str:
    return f'<span class="pill" style="background:{_color(status)}">{html.escape(str(text))}</span>'


def _summary_stats(s: dict[str, Any]) -> str:
    if not s or not s.get("trades"):
        return '<div class="muted">no closed paper trades yet</div>'
    wr = s.get("win_rate")
    wr_str = f"{wr * 100:.0f}%" if isinstance(wr, (int, float)) else "—"
    return (
        '<div class="stats">'
        f'<span>net <b style="color:{_color("OK") if (s.get("net_pnl") or 0) >= 0 else _color("CRITICAL")}">'
        f"{html.escape(_money(s.get('net_pnl')))}</b></span>"
        f"<span>trades {int(s.get('trades', 0))}</span>"
        f"<span>win {html.escape(wr_str)} ({int(s.get('wins', 0))}/{int(s.get('losses', 0))})</span>"
        f"<span>avg {html.escape(_money(s.get('avg_pnl')))}</span>"
        "</div>"
    )


def _by_profile_table(by_profile: dict[str, Any]) -> str:
    if not by_profile:
        return ""
    rows = []
    for tag, s in by_profile.items():
        net = s.get("net_pnl") or 0
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(tag))}</td>"
            f'<td style="color:{_color("OK") if net >= 0 else _color("CRITICAL")}">'
            f"{html.escape(_money(s.get('net_pnl')))}</td>"
            f"<td>{int(s.get('trades', 0))}</td>"
            f"<td>{int(s.get('wins', 0))}/{int(s.get('losses', 0))}</td>"
            "</tr>"
        )
    return (
        '<table class="prof"><thead><tr><th>profile</th><th>net</th><th>trades</th><th>w/l</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _eod_card_html(eod: dict[str, Any] | None) -> str:
    """Today's cross-module session P&L — the same numbers the scheduled digest writes, shown live.
    File-only; safe on the static render (no broker touch)."""
    if not eod:
        return ""
    rows = []
    for name, m in eod.get("modules", {}).items():
        if not m.get("ok"):
            rows.append(
                f"<tr><td>{html.escape(str(name))}</td>"
                f'<td class="muted" colspan="3">{html.escape(str(m.get("reason", "—")))}</td></tr>'
            )
            continue
        net = m.get("net_pnl") or 0
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(name))}</td>"
            f'<td style="color:{_color("OK") if net >= 0 else _color("CRITICAL")}">'
            f"{html.escape(_money(m.get('net_pnl')))}</td>"
            f"<td>{int(m.get('trades', 0))}</td>"
            f"<td>{int(m.get('wins', 0))}/{int(m.get('losses', 0))}</td>"
            "</tr>"
        )
    table = (
        '<table class="prof"><thead><tr><th>module</th><th>net</th><th>trades</th><th>w/l</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        if rows
        else '<div class="muted">no enabled modules</div>'
    )
    files = eod.get("files", {})
    file_bits = " · ".join(
        f"{html.escape(str(n))}: {'report ✓' if f else 'no file yet'}" for n, f in files.items()
    )
    files_line = (
        f'<div class="meta"><span class="muted">module reports — {file_bits}</span></div>' if files else ""
    )
    return (
        '<section class="card"><h2>end of day '
        f'<span class="muted">{html.escape(str(eod.get("session", "")))}</span></h2>'
        + _summary_stats(eod.get("suite", {}))
        + table
        + files_line
        + "</section>"
    )


def _findings_html(findings: list[dict[str, Any]], empty: str) -> str:
    if not findings:
        return f'<div class="muted">{html.escape(empty)}</div>'
    items = []
    for f in findings:
        status = str(f.get("status", "")).upper()
        items.append(
            "<li>" + _pill(status, status) + f" <b>{html.escape(str(f.get('title', '')))}</b> "
            f'<span class="muted">{html.escape(str(f.get("message", "")))}</span></li>'
        )
    return f'<ul class="findings">{"".join(items)}</ul>'


def _sla_html(sla: dict[str, Any]) -> str:
    if not sla:
        return ""
    bits = []
    for label in ("entry", "exit"):
        hb = sla.get(label) or {}
        if not hb:
            continue
        status = "OK" if hb.get("ok", True) else "WARN"
        detail = (
            hb.get("skipped")
            or hb.get("error")
            or (f"opened={hb.get('opened')} closed={hb.get('closed')}" if "opened" in hb else "ran")
        )
        bits.append(
            f'<div>{_pill(label, status)} <span class="muted">{html.escape(str(hb.get("date", "")))} '
            f"— {html.escape(str(detail))}</span></div>"
        )
    return f'<div class="sla">{"".join(bits)}</div>' if bits else ""


def _calibration_html(cal: dict[str, Any]) -> str:
    """Advisory promotion recommendations per ladder profile (from calibrate.run). Omitted if none."""
    profiles = (cal or {}).get("profiles", {})
    rows = []
    for tag, p in profiles.items():
        rec = p.get("recommendation")
        if not rec:  # off-ladder profiles carry a reading but no recommendation
            continue
        r = p.get("reading", {})
        graduate = rec.get("recommendation", "hold").startswith("graduate")
        wr = r.get("win_rate")
        wr_str = f"{wr * 100:.0f}%" if isinstance(wr, (int, float)) else "—"
        rows.append(
            "<li>"
            + _pill("eligible" if graduate else "hold", "OK" if graduate else "WARN")
            + f" <b>{html.escape(str(tag))}</b> "
            f'<span class="muted">n={int(r.get("sample", 0))} win {html.escape(wr_str)} '
            f"days {int(r.get('days', 0))} — {html.escape(str(rec.get('reason', '')))}</span></li>"
        )
    if not rows:
        return ""
    return f'<h3 class="sub">calibration</h3><ul class="findings">{"".join(rows)}</ul>'


def _tasks_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="muted">no tasks registered</div>'
    trs = []
    for r in rows:
        status = "OK" if r["exists"] else "WARN"
        trs.append(
            "<tr>"
            f"<td>{html.escape(r['name'])}</td>"
            f"<td>{_pill('registered' if r['exists'] else 'missing', status)}</td>"
            f"<td>{html.escape(str(r['status']))}</td>"
            f'<td class="num">{html.escape(str(r["last_run"]))}</td>'
            f'<td class="num">{html.escape(str(r["last_result"]))}</td>'
            f'<td class="num">{html.escape(str(r["next_run"]))}</td>'
            "</tr>"
        )
    return (
        '<table class="sys"><thead><tr><th>task</th><th></th><th>status</th>'
        "<th>last run</th><th>result</th><th>next run</th></tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table>"
    )


def _modules_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="muted">no modules enabled</div>'
    trs = []
    for r in rows:
        ladder = ", ".join(r["ladder"]) if r["ladder"] else "—"
        trs.append(
            "<tr>"
            f"<td><b>{html.escape(r['name'])}</b></td>"
            f"<td>{html.escape(r['source'])}</td>"
            f'<td class="num">{html.escape(r["git_ref"] or "—")}</td>'
            f"<td>{html.escape(str(r['paper_kind']))}</td>"
            f"<td>{_pill('on', 'OK') if r['streamer_enabled'] else _pill('off', 'UNKNOWN')}</td>"
            f"<td>{html.escape(ladder)}</td>"
            "</tr>"
        )
    return (
        '<table class="sys"><thead><tr><th>module</th><th>source</th><th>ref</th>'
        "<th>paper kind</th><th>streamer</th><th>ladder</th></tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table>"
    )


def _config_summary_html(cs: dict[str, Any]) -> str:
    if not cs:
        return '<div class="muted">no config</div>'
    bits = [f'<div><span class="muted">timezone</span> {html.escape(str(cs.get("timezone")))}</div>']
    for name, m in cs.get("modules", {}).items():
        ladder = f" · ladder {html.escape(', '.join(m['ladder']))}" if m.get("ladder") else ""
        bits.append(
            f'<div><span class="muted">{html.escape(name)}</span> '
            f"{'enabled' if m.get('enabled') else 'disabled'} · {html.escape(str(m.get('kind')))} · "
            f"streamer {'on' if m.get('streamer_enabled') else 'off'}{ladder}</div>"
        )
    wd = cs.get("watchdog", {})
    bits.append(
        f'<div><span class="muted">watchdog</span> every {html.escape(str(wd.get("interval_minutes")))}min'
        f", renotify {html.escape(str(wd.get('renotify_minutes')))}min"
        f", drawdown guard {'on' if wd.get('drawdown_configured') else 'off'}</div>"
    )
    dash = cs.get("dashboard", {})
    secs = (
        ", ".join(
            f"{html.escape(str(s.get('id')))}({'on' if s.get('enabled') else 'off'})"
            for s in dash.get("sections", [])
        )
        or "none"
    )
    bits.append(
        f'<div><span class="muted">dashboard</span> {html.escape(str(dash.get("output")))} · '
        f"serve {html.escape(str(dash.get('serve_host')))}:{html.escape(str(dash.get('serve_port')))} · "
        f"sections {secs}</div>"
    )
    notif = cs.get("notify", {})
    webhooks = (
        ", ".join(f"{html.escape(k)}={html.escape(v)}" for k, v in notif.get("webhooks", {}).items())
        or "none"
    )
    channels = html.escape(", ".join(notif.get("channels", [])))
    trade_channels = html.escape(", ".join(notif.get("trade_channels", [])))
    bits.append(
        f'<div><span class="muted">notify</span> channels {channels} · trades {trade_channels} '
        f"· webhooks {webhooks}</div>"
    )
    bits.append(
        f'<div><span class="muted">trade-notify</span> every '
        f"{html.escape(str(cs.get('trade_notify_interval_minutes')))}min</div>"
    )
    return "".join(bits)


def _doctor_live_html() -> str:
    """Serve-only live checks card: polls /api/system (doctor.run) — the only place the System panel
    touches the broker/streamer, kept off the static auto-regenerated path (see module docstring)."""
    return (
        '<div class="doctor-live" data-cp-doctor data-endpoint="/api/system">'
        '<h3 class="sub">live checks <span class="dot"></span></h3>'
        '<div class="doctor-rows muted">loading…</div></div>'
    )


def _reconcile_card_html() -> str:
    """Serve-only paper↔live isolation card. Broker-touching (`reconcile.run` → get_positions), so it
    runs on page load and on the Run button — never on a background poll (broker rate limits, same
    reasoning as `doctor --fast`). Omitted from the static file render (no server to answer the call)."""
    return (
        '<section class="card recon"><h2>paper↔live isolation '
        '<button class="recon-btn" data-cp-reconcile-run>run check</button></h2>'
        '<div class="recon-body" data-cp-reconcile data-endpoint="/api/reconcile">'
        '<span class="muted">checking real account…</span></div></section>'
    )


def _system_card_html(model: dict[str, Any], serve: bool) -> str:
    body = (
        '<h3 class="sub">scheduled tasks</h3>'
        + _tasks_table(model.get("tasks", []))
        + '<h3 class="sub">modules installed</h3>'
        + _modules_table(model.get("modules_installed", []))
        + '<h3 class="sub">config</h3>'
        + f'<div class="cfgsummary">{_config_summary_html(model.get("config_summary", {}))}</div>'
    )
    if serve:
        body += _doctor_live_html()
    return f'<section class="card"><h2>system</h2>{body}</section>'


def _module_card(mv: dict[str, Any]) -> str:
    name = mv["name"]
    rep = mv.get("pnl", {})
    if not rep.get("ok", True):
        body = f'<div class="muted">report unavailable: {html.escape(str(rep.get("reason", "")))}</div>'
    else:
        body = (
            _summary_stats(rep)
            + _by_profile_table(rep.get("by_profile", {}))
            + _findings_html(mv.get("findings", []), "no health findings")
            + _sla_html(mv.get("sla", {}))
            + _calibration_html(mv.get("calibration", {}))
        )
    return (
        '<section class="card">'
        f"<h2>{html.escape(name)} {_pill(mv.get('mode', 'PAPER'), 'INFO')}</h2>"
        f"{body}</section>"
    )


def _log_html(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return '<div class="muted">no log lines</div>'
    rows = []
    for e in entries:
        lvl = e["level"]
        rows.append(
            f'<div class="logline" data-level="{lvl}">'
            f'<span class="lvl" style="color:{_color(lvl)}">{lvl:<8}</span>'
            f'<span class="src">{html.escape(e["source"])}</span> '
            f'<span class="txt">{html.escape(e["text"])}</span></div>'
        )
    buttons = "".join(f'<button onclick="flt(this,\'{lv}\')" class="on">{lv}</button>' for lv in _LEVELS)
    return f'<div class="logbar">{buttons}</div><div class="logs">{"".join(rows)}</div>'


_CSS = """
:root{color-scheme:dark;
--bg:#0a0e12;--panel:#12161c;--border:#232a33;--text:#e6edf3;--muted:#8a97a3;
--accent:#2dd4bf;--pos:#16c784;--neg:#ea3943;--warn:#f0b429;
--mono:ui-monospace,"SF Mono",Consolas,monospace;--sans:-apple-system,Segoe UI,Roboto,sans-serif}
@media(prefers-color-scheme:light){:root{color-scheme:light;
--bg:#f6f8fa;--panel:#fff;--border:#d0d7de;--text:#1f2328;--muted:#57606a}}
body{font:14px/1.5 var(--sans);margin:0;background:var(--bg);color:var(--text)}
.wrap{max-width:1150px;margin:0 auto;padding:16px}
.header,.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;
padding:14px 16px;margin:0 0 14px}
h1{font-size:18px;margin:0 0 8px;display:flex;align-items:center;gap:8px}
h2{font-size:15px;margin:0 0 8px}
h3.sub{font-size:12px;margin:12px 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.paperbadge{font-size:10px;font-weight:700;letter-spacing:.04em;color:var(--warn);
border:1px solid var(--warn);border-radius:4px;padding:1px 6px}
.pill{color:#0a0e12;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:700;vertical-align:middle}
.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.stats{display:flex;gap:18px;flex-wrap:wrap;margin:8px 0}
.stats b{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:15px}
.meta{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);align-items:center;
font-family:var(--mono);font-variant-numeric:tabular-nums}
table.prof,table.sys{border-collapse:collapse;margin:6px 0 10px;font-size:12.5px;width:100%}
table.prof th,table.prof td,table.sys th,table.sys td{border:1px solid var(--border);
padding:3px 8px;text-align:left}
table.prof td:nth-child(2),table.sys td.num{font-family:var(--mono);
font-variant-numeric:tabular-nums;text-align:right}
table.sys th{color:var(--muted);font-weight:600;text-transform:uppercase;
font-size:10.5px;letter-spacing:.03em}
ul.findings{margin:8px 0;padding-left:18px}ul.findings li{margin:2px 0}
.sla div{margin:2px 0}
.cfgsummary div{margin:3px 0;font-size:12.5px}
.doctor-live{margin-top:10px;border-top:1px solid var(--border);padding-top:8px}
.drow{margin:3px 0;font-size:12.5px}
.dot,.live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--pos);
animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(22,199,132,.55)}70%{box-shadow:0 0 0 6px rgba(22,199,132,0)}
100%{box-shadow:0 0 0 0 rgba(22,199,132,0)}}
.logbar{margin:6px 0}
.logbar button{font:11px var(--mono);margin-right:4px;cursor:pointer;
border:1px solid var(--border);border-radius:4px;background:var(--panel);color:var(--text)}
.logbar button.off{opacity:.35}
.logs{font:12px/1.5 var(--mono);background:#05070a;color:#e6edf3;
border-radius:6px;padding:8px;max-height:340px;overflow:auto;border:1px solid var(--border)}
.logline{white-space:pre-wrap}.lvl{display:inline-block}.src{color:var(--muted)}
.embed-heading{font-size:15px;margin:2px 0 10px}
.card.embed{padding:0;overflow:hidden}
.card.embed h2{display:flex;align-items:center;gap:8px;margin:0;padding:10px 14px;
border-bottom:1px solid var(--border)}
.embed-open{margin-left:auto;font-size:12px;font-weight:600;color:var(--accent);text-decoration:none}
.embed-frame{display:block;width:100%;height:820px;border:0;background:var(--bg)}
.recon-btn{margin-left:auto;font:11px var(--mono);cursor:pointer;color:var(--text);
border:1px solid var(--border);border-radius:4px;background:var(--panel);padding:2px 10px}
.recon-btn:disabled{opacity:.5;cursor:default}
.card.recon h2{display:flex;align-items:center;gap:8px}
.recon-body .drow{margin:3px 0;font-size:12.5px}
"""

_JS = """
function flt(btn,lvl){btn.classList.toggle('off');btn.classList.toggle('on');
var show=btn.classList.contains('on');
document.querySelectorAll('.logline[data-level="'+lvl+'"]').forEach(function(r){r.style.display=show?'':'none'});}
"""

# Serve-only: polls /api/system (doctor.run) for the live-checks subsection. Kept out of the always-on
# _JS (unlike section JS, which is gated by whether any section is enabled) so its `data-cp-doctor`
# selector never appears in the static, offline render even as inert script text.
_DOCTOR_JS = """
(function(){
  var el=document.querySelector('[data-cp-doctor]'); if(!el) return;
  var url=el.getAttribute('data-endpoint'); var rows=el.querySelector('.doctor-rows');
  function tick(){ fetch(url).then(function(r){return r.json();}).then(function(d){
    if(!d||!d.ok){ rows.className='doctor-rows muted'; rows.textContent=(d&&d.error)||'unavailable'; return; }
    rows.className='doctor-rows';
    rows.innerHTML=(d.checks||[]).map(function(c){
      var color={OK:'var(--pos)',WARN:'var(--warn)',FAIL:'var(--neg)'}[c.status]||'var(--muted)';
      return '<div class="drow"><span class="pill" style="background:'+color+'">'+c.status+'</span> '
        +'<b>'+c.name+'</b> <span class="muted">'+c.detail+'</span></div>';
    }).join('');
  }).catch(function(){}); }
  tick(); setInterval(tick, 30000);
})();
"""

# Serve-only: fetches /api/reconcile (broker-touching) on page load and on the Run button. NOT an
# interval — each run authenticates to the broker. Kept out of the always-on _JS so its selector never
# appears in the static render.
_RECONCILE_JS = """
(function(){
  var body=document.querySelector('[data-cp-reconcile]'); if(!body) return;
  var url=body.getAttribute('data-endpoint');
  var btn=document.querySelector('[data-cp-reconcile-run]');
  function esc(s){var d=document.createElement('div');d.textContent=s==null?'':(''+s);return d.innerHTML;}
  function run(){
    body.className='recon-body muted'; body.textContent='checking real account…';
    if(btn){ btn.disabled=true; }
    fetch(url).then(function(r){return r.json();}).then(function(d){
      if(btn){ btn.disabled=false; }
      if(!d){ body.className='recon-body muted'; body.textContent='unavailable'; return; }
      if(d.ok===false && d.error){ body.className='recon-body muted'; body.textContent=d.error; return; }
      var v=d.verdict||'UNKNOWN';
      var color={FLAT:'var(--pos)',DRIFT:'var(--neg)',UNKNOWN:'var(--warn)'}[v]||'var(--muted)';
      var b=d.broker||{}; var html='';
      if(!b.reachable){
        html+='<div class="drow"><span class="pill" style="background:'+color+'">'+v+'</span> '
          +'<span class="muted">broker accounts could not be checked: '+esc(b.detail||'unavailable')
          +'</span></div>';
      } else {
        var accts=b.accounts||[];
        html+='<div class="drow"><span class="pill" style="background:'+color+'">'+v+'</span> '
          +'checked '+accts.length+' real account(s)</div>';
        accts.forEach(function(a){
          if(a.error){ html+='<div class="drow muted">account <b>'+esc(a.account||'****')+'</b>: '
            +esc(a.error)+'</div>'; return; }
          var pos=a.open_positions||[];
          var tag=a.designated? ' <span class="muted">(live — expected)</span>' : '';
          var state;
          if(!pos.length){ state='<span style="color:var(--pos)">is flat</span>'; }
          else if(a.designated){ state='<span style="color:var(--accent)">has '+pos.length
            +' open position(s) (expected)</span>'; }
          else { state='<b style="color:var(--neg)">has '+pos.length+' open position(s)</b>'; }
          html+='<div class="drow">account <b>'+esc(a.account||'****')+'</b>'+tag+' '+state+'</div>';
          pos.slice(0,20).forEach(function(p){
            var sym=p['symbol']||p['underlying-symbol']||p['instrument-type']||'?';
            var qty=p['quantity']||p['quantity-direction']||'';
            html+='<div class="drow muted">· '+esc(sym)+' '+esc(qty)+'</div>';
          });
        });
      }
      var paper=d.paper||{};
      Object.keys(paper).forEach(function(k){ var pv=paper[k]||{};
        var t=pv.ok? (pv.open_count||0)+' open (paper, context)' : (pv.reason||'unavailable');
        html+='<div class="drow muted">paper['+esc(k)+']: '+esc(t)+'</div>';
      });
      body.className='recon-body'; body.innerHTML=html;
    }).catch(function(){ if(btn){ btn.disabled=false; }
      body.className='recon-body muted'; body.textContent='request failed'; });
  }
  if(btn){ btn.addEventListener('click', run); }
  run();
})();
"""


def _embed_cards_html(embed_views: list[dict[str, Any]]) -> str:
    """One iframe card per embedded module dashboard (serve-only). Each `url` is the orchestrator's local
    `/embed/<id>` route: for a "server" embed it 302-redirects to the module's port (launching it if
    down); for a "static" embed it regenerates and serves the module's HTML file. A per-card
    open-in-new-tab link is the graceful fallback if a module server refuses to be framed."""
    if not embed_views:
        return ""
    cards = []
    for e in embed_views:
        url = html.escape(e["url"])
        cards.append(
            '<section class="card embed">'
            f"<h2>{html.escape(e['title'])} {_pill('PAPER', 'INFO')}"
            f'<a class="embed-open" href="{url}" target="_blank" rel="noopener">open ↗</a></h2>'
            f'<iframe class="embed-frame" src="{url}" loading="lazy" '
            'referrerpolicy="no-referrer" title="'
            f'{html.escape(e["title"])} dashboard"></iframe>'
            "</section>"
        )
    return '<h2 class="embed-heading">embedded module dashboards</h2>' + "".join(cards)


def _render_html(model: dict[str, Any], serve: bool = False) -> str:
    overall = model.get("overall", "UNKNOWN")
    age = model.get("heartbeat_age_min")
    age_str = f"{age:.0f} min ago" if isinstance(age, (int, float)) else "no watchdog heartbeat yet"
    session = model.get("in_session")
    session_pill = (
        _pill("OPEN", "OK")
        if session is True
        else _pill("CLOSED", "UNKNOWN")
        if session is False
        else _pill("UNKNOWN", "UNKNOWN")
    )
    live_dot = ' <span class="live-dot" title="live"></span>' if serve else ""
    header = (
        '<div class="header"><h1>cherrypick <span class="paperbadge">PAPER</span> — suite status '
        + _pill(overall, overall)
        + live_dot
        + "</h1>"
        '<div class="meta">'
        f"<span>watchdog: {html.escape(age_str)}</span>"
        f"<span>ET: {html.escape(str(model.get('et_clock') or '—'))} {session_pill}</span>"
        f"<span>trading day: {html.escape(str(model.get('is_trading_day')))}</span>"
        f"<span>notify: {html.escape(', '.join(model.get('notify_channels', [])))}</span>"
        "</div>"
        + _summary_stats(model.get("suite", {}))
        + "<h2>active alerts</h2>"
        + _findings_html(model.get("active_findings", []), "no active WARN/CRITICAL findings")
        + "</div>"
    )
    system_card = _system_card_html(model, serve)
    eod_card = _eod_card_html(model.get("eod"))
    cards = "".join(_module_card(mv) for mv in model.get("modules", []))
    logs = '<section class="card"><h2>recent logs</h2>' + _log_html(model.get("logs", [])) + "</section>"
    footer = (
        '<div class="meta"><span class="muted">read-only · paper · generated '
        f"{html.escape(str(model.get('generated_at')))}</span></div>"
    )
    # Live sections are serve-only: they poll /api/section/<id>, which exists only under
    # `dashboard --serve`. A static file render omits them (no server to answer the poll).
    live_sections = model.get("sections", []) if serve else []
    section_cards = "".join(
        viz.card_skeleton_html(s["id"], s["title"], s["endpoint"], s["refresh"]) for s in live_sections
    )
    # Embedded module dashboards are serve-only too: each iframe points at /embed/<id>, a route the
    # live server owns (it launches/regenerates the module dashboard on demand). Omitted in the static
    # file render — there's no orchestrator server to answer /embed/<id>.
    embed_cards = _embed_cards_html(model.get("embeds", [])) if serve else ""
    # Reconcile is broker-touching, so its card is serve-only too (the /api/reconcile route only exists
    # under `dashboard --serve`); the static file render omits it.
    reconcile_card = _reconcile_card_html() if serve else ""
    extra_style = viz.SECTION_STYLE if live_sections else ""
    extra_script = (viz.SECTION_JS if live_sections else "") + (_DOCTOR_JS + _RECONCILE_JS if serve else "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>cherrypick status</title><style>"
        + _CSS
        + extra_style
        + "</style></head><body><div class='wrap'>"
        + header
        + system_card
        + eod_card
        + reconcile_card
        + section_cards
        + f'<div class="grid">{cards}</div>'
        + logs
        + embed_cards
        + footer
        + "</div><script>"
        + _JS
        + extra_script
        + "</script></body></html>"
    )


# --------------------------------------------------------------------------- entrypoints
def _output_path(cfg: dict[str, Any]) -> Path:
    out = Path(cfg.get("dashboard", {}).get("output", "dashboard.html"))
    if not out.is_absolute():
        out = cfgmod.ROOT / out
    return out


def render(cfg: dict[str, Any] | None = None) -> Path:
    """Build the dashboard and write it atomically. Read-only w.r.t. all data sources."""
    cfg = cfg or cfgmod.load_config()
    model = build_model(cfg)
    out = _output_path(cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(_render_html(model), encoding="utf-8")
    os.replace(tmp, out)
    return out


def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or cfgmod.load_config()
    path = render(cfg)
    model = build_model(cfg)
    return {
        "ok": True,
        "path": str(path),
        "overall": model.get("overall"),
        "suite_net_pnl": model.get("suite", {}).get("net_pnl"),
    }
