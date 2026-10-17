"""Whether the vendor collector is on: the one switch every vendor-fed reader and job follows.

The subscription can lapse and be taken up again (it ends 2026-10-24), so the vendor is a
configured source, never an assumed one. `market_report.collector` in the suite config is the
switch, and it counts as on only when the two generic keys that say where to read and what the
report is called are set too -- switched on without them, nothing could be collected. The
orchestrator's collector jobs and every reader that prefers a vendor file over a public one use
this, so a job and a reader can never disagree about whether the vendor is in use.

Off, a reader uses its public source, and the vendor files already saved stay on disk as the
answer key the engines are scored against; they are no longer read as live inputs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import home as _home

VENDOR_COLLECTOR_KEYS = ("vendor_dashboard_url", "vendor_edition_title")


def collector_missing(market_report: Mapping[str, Any] | None) -> list[str]:
    """The generic keys the collector needs and the `market_report` block lacks."""
    mr = market_report or {}
    return [k for k in VENDOR_COLLECTOR_KEYS if not (isinstance(mr.get(k), str) and mr.get(k).strip())]


def collector_on(cfg: Mapping[str, Any] | None) -> bool:
    """The suite config's verdict: the switch on and both keys set."""
    mr = (cfg or {}).get("market_report") or {}
    return bool(mr.get("collector", False)) and not collector_missing(mr)


def suite_collector_on() -> bool:
    """`collector_on` for the suite config on disk; off when there is none or it cannot be read."""
    try:
        cfg = json.loads(_home.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return collector_on(cfg if isinstance(cfg, dict) else {})
