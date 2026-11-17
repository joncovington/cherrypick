"""Whether the vendor licence allows new work, as the backtest package reports it (`python -m
cherrypick.backtest status`, the `active` of its `license` block from `licensing.status`), read by
subprocess: this package imports no backtest code.

The backtest suggestion asks it before offering a licensed store, and the automation backtest driver
applies the same test before reading one, so the Builder never offers a run the driver would refuse.
FAIL-CLOSED: the package absent, its config unreadable, a timeout or any other answer reads as "no".
"""

from __future__ import annotations

import json
import subprocess
import sys

#: Keeps a console-launched child from flashing a window on Windows; 0 elsewhere.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT_S = 60


def allows_new_work(run=subprocess.run) -> bool:
    """True only when the backtest package says its licence is active."""
    try:
        r = run(
            [sys.executable, "-m", "cherrypick.backtest", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
        doc = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    lic = doc.get("license") if isinstance(doc, dict) else None
    return isinstance(lic, dict) and lic.get("active") is True
