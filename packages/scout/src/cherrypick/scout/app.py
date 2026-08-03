"""The scout FastAPI app: lifespan (cache DB + logging), security middleware, static shell, routes.

Single process is mandatory (Windows spawn + single-writer SQLite + in-process poller) — see
``serve.py``, which is the only place this is actually run with ``workers=1``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from cherrypick.core import logs as _logs
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config as _config
from .api import calendar as _calendar_api
from .api import symbol as _symbol_api
from .api import watchlist as _watchlist_api
from .security import SecurityMiddleware, new_csrf_token
from .services import cache as _cache
from .services.session import BrokerSession

STATIC_DIR = Path(__file__).resolve().parent / "static"

logger = logging.getLogger("cherrypick.scout")


def _make_lifespan(cfg: dict):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _logs.configure(logger, _config.log_path())
        app.state.cache_db = _cache.open_db(_config.cache_db_path())
        logger.info("scout starting on %s:%s", cfg["serve"]["host"], cfg["serve"]["port"])
        try:
            yield
        finally:
            app.state.cache_db.close()
            logger.info("scout stopped")

    return lifespan


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or _config.load()
    port = int(cfg.get("serve", {}).get("port", 5057))

    app = FastAPI(title="cherrypick-scout", lifespan=_make_lifespan(cfg))
    app.state.cfg = cfg
    app.state.csrf_token = new_csrf_token()
    app.state.watchlist_path = _config.watchlist_path()
    app.state.broker_session = BrokerSession()

    app.add_middleware(SecurityMiddleware, port=port, csrf_token=app.state.csrf_token)

    app.include_router(_watchlist_api.router)
    app.include_router(_calendar_api.router)
    app.include_router(_symbol_api.router)

    app.mount("/static/vendor", StaticFiles(directory=str(STATIC_DIR / "vendor")), name="vendor")
    app.mount("/static/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
    app.mount("/static/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace("__CSRF__", app.state.csrf_token)
        return HTMLResponse(page)

    @app.get("/favicon.ico", include_in_schema=False, response_model=None)
    def favicon():
        icon = STATIC_DIR / "favicon.ico"
        if icon.exists():
            return FileResponse(icon)
        return HTMLResponse("", status_code=204)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app
