"""Standalone streamer: run the shared core engine to populate this module's own stream cache.

This is what makes cherrypick-gex self-contained — it no longer needs MEIC's streamer running. It drives
`cherrypick.core.streamer.ChainStreamer` (the same engine MEIC will migrate onto) with this module's own
tastytrade OAuth session and writes to this module's own `stream_cache.db`, which `provider.py` then
reads read-only. No open-position policy, no ORB, no HTTP API — just stream the configured underlyings'
chains + ATM/GEX windows.

Credentials come from the OS keyring under the same service the rest of the suite uses ("meicagent",
with a read-only fallback to the pre-rename "tastytrade-mcp"), so no re-entry is needed on a box that
already has the suite's tastytrade OAuth stored.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from cherrypick.core.auth import CredentialStore, SessionManager  # noqa: E402
from cherrypick.core.streamer import ChainStreamer  # noqa: E402

_SERVICE = "meicagent"
_LEGACY = "tastytrade-mcp"


def make_session_factory():
    """A thread-local tastytrade session factory backed by the suite's keyring credentials.

    thread_local: the engine's DXLink loop and any REST call must each get a session bound to their own
    event loop (tastytrade's Session holds a loop-bound httpx client) — see cherrypick.core.auth.
    """
    store = CredentialStore(_SERVICE, legacy_service_names=(_LEGACY,))
    return SessionManager(store, thread_local=True).get_session


def build_streamer(cfg: dict, symbols: list[str] | None = None) -> ChainStreamer:
    syms = [s.upper() for s in (symbols or cfg.get("symbols") or ["SPX"])]
    scfg = cfg.get("streamer", {}) or {}
    return ChainStreamer(
        session_factory=make_session_factory(),
        db_path=cfg["stream_cache_db"],
        symbols=syms,
        window_strike_count=int(scfg.get("window_strike_count", 20)),
        logger=logging.getLogger("cherrypick-gex.streamer"),
    )


def run(cfg: dict, symbols: list[str] | None = None) -> None:
    """Run the streamer in the foreground until Ctrl-C / SIGTERM."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    streamer = build_streamer(cfg, symbols)
    print(f"cherrypick-gex streamer: {streamer.symbols} -> {cfg['stream_cache_db']}  (Ctrl-C to stop)")
    streamer.run()
