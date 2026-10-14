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
