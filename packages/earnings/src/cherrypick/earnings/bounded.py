"""A wall-clock ceiling for the Dolt-heavy scan steps, shared by both scans that make them.

Dolt's Python path cannot be bounded from the caller: mysql-connector offers no client-side read
timeout, and Dolt does not honor the server-side ``max_execution_time`` SELECT cap (verified against
the live server). So a Dolt that is cold-starting or compacting makes ``cur.execute()`` block
forever, and there is no argument that makes it stop.

This lives in its own leaf module — importing only the standard library — because BOTH of this
module's scans need it and the obvious homes create a cycle: ``strat_test_harness`` already imports
``symbol_watch``, so the primitive cannot live in the harness where it was first written.

Both callers hold the paper loop's single-writer lock while they run, which is what makes an
unbounded step expensive rather than merely slow: a hung query does not just stall its own scan, it
blocks every later phase of the session behind it.
"""

from __future__ import annotations

import threading


class OpTimeout(Exception):
    """A bounded scan step (a Dolt-heavy operation) exceeded its wall-clock budget."""


def run_bounded(fn, timeout_s, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` with a wall-clock ceiling; return its result or raise OpTimeout.

    Running the step in a daemon thread lets a scan abandon a hung symbol and move on; the orphaned
    thread cannot be killed but dies with this short-lived process. Same bounded-and-returns-failure
    intent as ``scanner.call_tt``, without a subprocess (the Dolt calls are in-process).
    """
    box: dict = {}

    def _target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the caller's except below
            box["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise OpTimeout(f"exceeded {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")
