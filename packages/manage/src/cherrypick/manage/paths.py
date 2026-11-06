"""Data-home path resolution for cherrypick-manage.

A thin facade over :mod:`cherrypick.core.home`. Manage writes only into its own home
(``~/.cherrypick/data/manage`` by default, or ``MANAGE_DATA_DIR``).
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import home as _home

PACKAGE = "manage"


def data_dir() -> Path:
    return _home.data_dir(PACKAGE, env="MANAGE_DATA_DIR")


def eval_path(session: str) -> Path:
    """One session's evaluation -- the only thing any surface reads."""
    return data_dir() / f"eval-{session}.json"


def snapshot_path(session: str) -> Path:
    """The broker snapshot the evaluation was made from, kept so it can be re-run under other rules."""
    return data_dir() / f"snapshot-{session}.json"


def entry_deltas_path() -> Path:
    """Each call's delta at entry, recorded once from the first full snapshot after its fill
    (entrydelta.py); never rewritten."""
    return data_dir() / "entry-deltas.json"


def trade_ids_path() -> Path:
    """Each trade's stable ID and the orders and legs it has held (tradeids.py); added to, never
    rewritten."""
    return data_dir() / "trade-ids.json"


def marks_path(session: str) -> Path:
    """The newest quotes the session's snapshot was moved to (`remark`), while that snapshot stands."""
    return data_dir() / f"marks-{session}.json"
