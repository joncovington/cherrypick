"""Data-home path resolution for cherrypick-review.

A thin facade over :mod:`cherrypick.core.home`, the suite-wide resolver, exactly as every other
module has. Review writes only into its own home (``~/.cherrypick/data/review`` by default, or
``REVIEW_DATA_DIR``); it reads every other module's ledger read-only and writes to none of them.

Portability guardrail: never hardcode an absolute path -- the home derives from ``Path.home()`` or
the documented overrides.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import home as _home

PACKAGE = "review"


def data_dir() -> Path:
    """Review's own store: where the daily fact sets and renders land."""
    return _home.data_dir(PACKAGE, env="REVIEW_DATA_DIR")


def logs_dir() -> Path:
    return _home.logs_dir(PACKAGE, env="REVIEW_LOGS_DIR")


def facts_path(session: str) -> Path:
    """The fact set for one session. `session` is an ISO date."""
    return data_dir() / f"eod-{session}.json"


def render_path(session: str) -> Path:
    return data_dir() / f"eod-{session}.md"


def note_path(session: str) -> Path:
    """Where the narrative lands -- beside the facts, never inside them, so a missing or failed
    note can never damage the record it describes."""
    return data_dir() / f"eod-{session}.note.md"


def module_db(module: str, filename: str = "paper_trades.db") -> Path:
    """A module's ledger, resolved through the same home every module writes it to."""
    return _home.data_dir(module) / filename
