"""Lazy tastytrade OAuth session management.

Thin shim over cherrypit-core's shared ``SessionManager`` (see ``cherrypit.auth``). Process-global
(``thread_local=False``): EarningsAgent has no persistent streamer daemon — every invocation is a
short-lived ``tt.py`` subprocess, so one process-wide cached session suffices. The ``get_session`` /
``reset_session`` names are preserved so existing call sites are unchanged.
"""

from __future__ import annotations

from cherrypit.auth import SessionManager

import credentials

_manager = SessionManager(credentials.store, thread_local=False)

get_session = _manager.get_session
reset_session = _manager.reset_session
