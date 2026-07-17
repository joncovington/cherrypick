"""Render the orchestrator dashboard (serve mode) with a fixed fixture model and print the HTML
to stdout.

The headless-browser test loads this HTML to exercise the *client-side* drag-to-reorder and
collapsible-section JavaScript, without needing a live server, config, or paper databases. Only
the DOM/JS behaviour is under test here — data plumbing is covered by the Python tests.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "src")))

from cherrypick.orchestrator import dashboard  # noqa: E402

_MODEL = {
    "overall": "OK",
    "generated_at": "2026-01-01T00:00:00Z",
    "heartbeat_age_min": 1,
    "in_session": True,
    "et_clock": "10:00:00",
    "is_trading_day": True,
    "notify_channels": ["log"],
    "active_findings": [],
    "suite": {"trades": 0, "gross_pnl": 0, "cost": 0, "net_pnl": 0, "wins": 0, "losses": 0},
    "tasks": [],
    "modules_installed": [],
    "config_summary": {},
    "modules": [
        {"name": "meic", "mode": "PAPER", "pnl": {"ok": True, "net_pnl": 0, "by_profile": {}},
         "findings": [], "sla": {}, "calibration": {}},
        {"name": "earnings", "mode": "PAPER", "pnl": {"ok": True, "net_pnl": 0, "by_profile": {}},
         "findings": [], "sla": {}, "calibration": {}},
    ],
    "logs": [],
    "eod": {
        "session": "2026-01-01", "is_today": False,
        "suite": {"net_pnl": 0, "trades": 0, "wins": 0, "losses": 0},
        "modules": {}, "files": {}, "digest": None,
    },
    "sections": [],
    "embeds": [
        {"id": "meic", "title": "meic", "url": "/embed/meic"},
        {"id": "earnings", "title": "earnings", "url": "/embed/earnings"},
        {"id": "gex", "title": "gex dashboard", "url": "/embed/gex"},
    ],
}

if __name__ == "__main__":
    sys.stdout.write(dashboard._render_html(_MODEL, serve=True))
