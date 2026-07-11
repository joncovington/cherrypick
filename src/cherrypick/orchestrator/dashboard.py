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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import calibrate, report, timeutil
from . import config as cfgmod

_STATUS_COLORS = {
    "OK": "#1a7f37",
    "WARN": "#9a6700",
    "CRITICAL": "#cf222e",
    "INFO": "#0969da",
    "UNKNOWN": "#6e7781",
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


# --------------------------------------------------------------------------- model
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
            sources.append((name, cfgmod.module_root(mcfg) / log_rel))
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
        "modules": module_views,
        "logs": log_entries,
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
:root{color-scheme:light dark}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}
@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}.card,.header{background:#161b22;border-color:#30363d}}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
.header,.card{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:14px 16px;margin:0 0 14px}
h1{font-size:18px;margin:0 0 8px}h2{font-size:15px;margin:0 0 8px}
h3.sub{font-size:13px;margin:8px 0 2px;color:#57606a;text-transform:uppercase;letter-spacing:.03em}
.pill{color:#fff;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:600;vertical-align:middle}
.muted{color:#6e7781}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.stats{display:flex;gap:16px;flex-wrap:wrap;margin:6px 0}.stats b{font-variant-numeric:tabular-nums}
.meta{display:flex;gap:16px;flex-wrap:wrap;color:#57606a}
table.prof{border-collapse:collapse;margin:8px 0;font-size:13px}
table.prof th,table.prof td{border:1px solid #d0d7de;padding:2px 8px;text-align:left}
ul.findings{margin:8px 0;padding-left:18px}ul.findings li{margin:2px 0}
.sla div{margin:2px 0}
.logbar{margin:6px 0}
.logbar button{font:11px monospace;margin-right:4px;cursor:pointer;
border:1px solid #d0d7de;border-radius:4px;background:#eaeef2}
.logbar button.off{opacity:.35}
.logs{font:12px/1.5 ui-monospace,Consolas,monospace;background:#0d1117;color:#e6edf3;
border-radius:6px;padding:8px;max-height:340px;overflow:auto}
.logline{white-space:pre-wrap}.lvl{display:inline-block}.src{color:#8b949e}
"""

_JS = """
function flt(btn,lvl){btn.classList.toggle('off');btn.classList.toggle('on');
var show=btn.classList.contains('on');
document.querySelectorAll('.logline[data-level="'+lvl+'"]').forEach(function(r){r.style.display=show?'':'none'});}
"""


def _render_html(model: dict[str, Any]) -> str:
    overall = model.get("overall", "UNKNOWN")
    age = model.get("heartbeat_age_min")
    age_str = f"{age:.0f} min ago" if isinstance(age, (int, float)) else "no watchdog heartbeat yet"
    session = model.get("in_session")
    session_str = "in session" if session else ("off-hours" if session is not None else "unknown")
    header = (
        '<div class="header"><h1>Cherrypick — suite status ' + _pill(overall, overall) + "</h1>"
        '<div class="meta">'
        f"<span>watchdog: {html.escape(age_str)}</span>"
        f"<span>ET: {html.escape(str(model.get('et_clock') or '—'))} ({html.escape(session_str)})</span>"
        f"<span>trading day: {html.escape(str(model.get('is_trading_day')))}</span>"
        f"<span>notify: {html.escape(', '.join(model.get('notify_channels', [])))}</span>"
        "</div>"
        + _summary_stats(model.get("suite", {}))
        + "<h2>active alerts</h2>"
        + _findings_html(model.get("active_findings", []), "no active WARN/CRITICAL findings")
        + "</div>"
    )
    cards = "".join(_module_card(mv) for mv in model.get("modules", []))
    logs = '<section class="card"><h2>recent logs</h2>' + _log_html(model.get("logs", [])) + "</section>"
    footer = (
        '<div class="meta"><span class="muted">read-only · paper · generated '
        f"{html.escape(str(model.get('generated_at')))}</span></div>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Cherrypick status</title><style>"
        + _CSS
        + "</style></head><body><div class='wrap'>"
        + header
        + f'<div class="grid">{cards}</div>'
        + logs
        + footer
        + "</div><script>"
        + _JS
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
