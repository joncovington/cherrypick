"""The settings-server invariant, ported to FastAPI middleware.

Scout is a research surface, not a settings editor, but it carries a handful of narrow mutating
routes (watchlist save, order dry-run, staged-ticket save/delete) — so it inherits the same posture
`cherrypick.orchestrator.settings_serve` uses for its one mutating HTTP surface, rather than the
plain loopback-binding-only rule every read-only dashboard in the suite gets away with: a malicious
webpage can fetch ``http://127.0.0.1:<port>`` from inside a user's browser, and DNS rebinding can
defeat same-origin, so binding alone is not enough once a route writes anything.

Every request (GET included) must carry a loopback ``Host`` header naming this exact port — else 403.
Every mutating request (POST/PUT/PATCH/DELETE) must additionally carry the per-process CSRF token
baked into the page, an ``application/json`` content type (which forces a cross-origin preflight this
server never answers, since it sends no CORS headers), and, when an ``Origin`` header is present, the
local origin.
"""

from __future__ import annotations

import hmac
import secrets as pysecrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSRF_HEADER = "x-csrf-token"


def new_csrf_token() -> str:
    return pysecrets.token_urlsafe(32)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Loopback Host-header enforcement + CSRF/content-type/origin gating on mutating verbs.

    Constructed once per app with the bound port and the process's CSRF token, so a test can pass a
    fixed token and assert against it without reaching into global state.
    """

    def __init__(self, app, *, port: int, csrf_token: str):
        super().__init__(app)
        self._valid_hosts = {f"{h}:{port}" for h in _LOOPBACK_HOSTS} | set(_LOOPBACK_HOSTS)
        self._valid_origins = {f"http://{h}:{port}" for h in _LOOPBACK_HOSTS}
        self._csrf_token = csrf_token

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host") or ""
        if host not in self._valid_hosts:
            return JSONResponse({"ok": False, "error": "bad Host header"}, status_code=403)

        if request.method in _MUTATING_METHODS:
            token = request.headers.get(CSRF_HEADER) or ""
            if not hmac.compare_digest(token, self._csrf_token):
                return JSONResponse({"ok": False, "error": "missing or invalid CSRF token"}, status_code=403)
            ctype = (request.headers.get("content-type") or "").split(";")[0].strip()
            if ctype != "application/json":
                return JSONResponse(
                    {"ok": False, "error": "Content-Type must be application/json"}, status_code=403
                )
            origin = request.headers.get("origin")
            if origin and origin not in self._valid_origins:
                return JSONResponse({"ok": False, "error": "cross-origin POST refused"}, status_code=403)

        return await call_next(request)
