"""Data-home paths for cherrypick-backtest, through :mod:`cherrypick.core.home`.

Two stores, kept apart because the licence treats them differently:

- ``vendor/<name>/``: the vendor's data as fetched. Re-fetchable while the subscription runs and
  required to be expunged within the licence's window once it ends, so it carries a marker file the
  nightly backup honours (a copy in the backup would outlive the purge).
- ``results/``: runs, trials and their results. Derived from the vendor's data, so it never leaves
  the home either, but it is ours to keep and is backed up.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import home as _home

PACKAGE = "backtest"

#: A directory holding this file is licensed vendor data: never backed up, removed by `purge`.
#: The orchestrator's backup reads the same name (orchestrator/backup.py); change both or neither.
LICENSED_MARKER = ".licensed-vendor-data"


def data_dir() -> Path:
    return _home.data_dir(PACKAGE, env="BACKTEST_DATA_DIR")


def vendor_dir(settings: dict) -> Path:
    """Where the vendor's data lives: `store.dir` when set, else `<data>/vendor/<vendor name>`."""
    configured = (settings.get("store") or {}).get("dir")
    if configured:
        return Path(configured).expanduser()
    return data_dir() / "vendor" / settings["vendor"]["name"]


def results_dir() -> Path:
    return data_dir() / "results"


def results_db() -> Path:
    return results_dir() / "backtest.db"


def ensure_vendor_dir(settings: dict) -> Path:
    """Create the vendor directory with its marker. The marker goes in first, so no file the vendor
    supplied can ever sit in an unmarked directory."""
    path = vendor_dir(settings)
    path.mkdir(parents=True, exist_ok=True)
    marker = path / LICENSED_MARKER
    if not marker.exists():
        marker.write_text(
            f"Licensed {settings['vendor']['name']} data. Personal use only, never redistributed; "
            "expunge within the licence window after the subscription ends "
            "(python -m cherrypick.backtest purge).\n",
            encoding="utf-8",
        )
    return path
