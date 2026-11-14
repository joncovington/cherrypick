"""The vendor licence's state, as the backtest package reports it (`python -m cherrypick.backtest
status`, its `license` block from `licensing.status`), read by subprocess: this package imports no
other package.

Once the operator records `vendor.license.terminated_on`, the research side stops asking for vendor
data (the user's decision, #1091): the queue refuses and cancels fetch rows, the forward retests plan
nothing that rests on the stores, and a forward check whose backtest reference was purged judges the
paper arm alone. A licence that cannot be read is "unknown", never "ended": nothing is cancelled on a
failed read.
"""

from __future__ import annotations

import json
import subprocess
import sys

from .util import CREATE_NO_WINDOW


def status(run=subprocess.run) -> dict | None:
    """The backtest's licence block (`active`, `terminated_on`, `purge_by`, `phase`, ...), or None
    when it cannot be read (the package absent, its config unreadable, a timeout)."""
    try:
        r = run(
            [sys.executable, "-m", "cherrypick.backtest", "status"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
        doc = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    lic = doc.get("license") if isinstance(doc, dict) else None
    return lic if isinstance(lic, dict) and "active" in lic else None


def ended(lic: dict | None) -> bool:
    """Whether the licence is known to have ended. Unknown is not ended."""
    return isinstance(lic, dict) and lic.get("active") is False
