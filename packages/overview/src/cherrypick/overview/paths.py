"""Data-home path resolution for cherrypick-overview.

A thin facade over :mod:`cherrypick.core.home`, the suite-wide resolver, exactly as every other
module has. Overview writes only into its own home (``~/.cherrypick/data/overview`` by default, or
``OVERVIEW_DATA_DIR``); everything else it touches -- the shared stream cache and the GEX history --
it reads read-only and writes to none of them.

Portability guardrail: never hardcode an absolute path -- the home derives from ``Path.home()`` or
the documented overrides.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import home as _home

PACKAGE = "overview"


def data_dir() -> Path:
    """Overview's own store: where the daily fact packs and renders land."""
    return _home.data_dir(PACKAGE, env="OVERVIEW_DATA_DIR")


def logs_dir() -> Path:
    return _home.logs_dir(PACKAGE, env="OVERVIEW_LOGS_DIR")


def facts_path(session: str) -> Path:
    """The morning fact pack for one session. `session` is an ISO date."""
    return data_dir() / f"morning-{session}.json"


def render_path(session: str) -> Path:
    return data_dir() / f"morning-{session}.md"


def note_path(session: str) -> Path:
    """Where the narrative lands -- beside the facts, never inside them, so a missing or failed
    note can never damage the record it describes."""
    return data_dir() / f"morning-{session}.note.md"


def stream_cache_db() -> Path:
    """The shared stream cache -- read-only here; the streamer is its single producer."""
    return _home.data_dir("marketdata") / "stream_cache.db"


def gex_history_db() -> Path:
    """The GEX engine's regime history -- read-only here; the recorder is its single writer."""
    return _home.data_dir("gex") / "gex_history.db"


def meic_paper_db() -> Path:
    """MEIC's paper ledger, read-only, used only as a VIX fallback via its market_context table."""
    return _home.data_dir("meic") / "paper_trades.db"
