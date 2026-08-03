"""Run the scout web app. Loopback binding only — refused, not warned about, mirroring flies'
server-refuses principle: this module carries mutating routes (watchlist, dry-run, staged tickets),
so it must never be laxer than the suite's other localhost surfaces.

Single process, ``workers=1`` is mandatory: Windows spawn semantics, the single-writer SQLite cache,
and (from M7) the in-process quote poller all assume exactly one process.
"""

from __future__ import annotations

from . import config as _config

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def app_factory():
    """Entry point for uvicorn's ``factory=True`` — imported by string, called with no arguments."""
    from .app import create_app

    return create_app(_config.load())


def serve(cfg: dict | None = None, host: str | None = None, port: int | None = None) -> dict:
    cfg = cfg or _config.load()
    scfg = cfg.get("serve", {})
    host = host or scfg.get("host", "127.0.0.1")
    port = int(port or scfg.get("port", 5057))
    if host not in _LOOPBACK_HOSTS:
        return {"ok": False, "error": f"scout binds loopback only (127.0.0.1/localhost), not {host!r}"}

    import uvicorn

    uvicorn.run("cherrypick.scout.serve:app_factory", host=host, port=port, factory=True, workers=1)
    return {"ok": True}
