"""`run.py settle-overdue-live` -- catch-up settlement of past sessions a live ledger still holds open.

Item 4 of the 2026-10-08 safeguards audit. flies and bwb settle a live book only inside an armed
day's own ticks, so a machine down between the close and the disarm left that day's book open in the
ledger for good (the watchdog's `<module>.live_overdue` now says so). This job settles it, the way
the owner chose: at THAT session's official close (`core.settlement.dated_index_close`, source
`yahoo_daily`) and never at a provisional price. A session with a pending entry, or no official close
found, is left open and keeps alerting.

Each module does its own settlement (`live_loop --settle-overdue`, by subprocess, as every module
call here); this only runs them and tells every channel what was settled, so a write to a live ledger
never happens quietly.
"""

from __future__ import annotations

from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import doctor
from .util import first_json

MODULES = ("flies", "bwb")


def run(cfg: dict[str, Any] | None = None, *, notifier: Notifier | None = None) -> dict[str, Any]:
    cfg = cfgmod.load_config() if cfg is None else cfg
    notifier = notifier or Notifier(cfg.get("notify"))
    results: dict[str, Any] = {}
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        if name not in MODULES:
            continue
        try:
            r = doctor._run(
                cfgmod.module_root(mcfg, name),
                ["-m", f"cherrypick.{name}.live_loop", "--settle-overdue"],
                timeout=180,
            )
            out = first_json(r.stdout) or {"ok": False, "error": (r.stderr or "no JSON")[-300:]}
        except Exception as exc:  # noqa: BLE001 -- one module failing must not stop the other
            out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        results[name] = out
        for s in out.get("settled") or []:
            notifier.notify(
                "INFO",
                f"{name}.live_settle_overdue.{s.get('session')}",
                f"{name} LIVE book from {s.get('session')} settled (catch-up)",
                f"Settled at {float(s.get('price') or 0):.2f}, that session's official close (yahoo_daily). "
                "It had been left open since the session -- the machine was not running between the "
                "close and the disarm.",
            )
    return {"ok": all(v.get("ok") for v in results.values()), "modules": results}
