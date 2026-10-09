"""Daemon stop/start/restart handed to the supervisor, which may act where the caller may not.

Under the Windows service (`winservice`) every daemon the suite keeps alive -- the streamer, the gex
recorder -- is started from the service's session 0, and a process there cannot be stopped from the
desktop even by the same user: on 2026-10-09 `run.py restart gex-recorder` failed with Access is
denied, so new GEX code could not reach the recorder, and the failed restart left the daemon held.
The supervisor runs in that session, so it can. This module is the hand-off: the CLI writes a
request, the supervisor's next pass takes it and runs the same verb as a `--direct` child (which
inherits the service's rights), and the child writes the result the CLI is waiting for.

Only `restart`, `stop` and `start` of a daemon the config declares are ever acted on: a file in
`state/` must never be a way to make the service run an arbitrary command.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from . import config as cfgmod
from .util import atomic_write_json, read_json

VERBS = ("restart", "stop", "start")
RESULT_SUFFIX = ".result.json"
WAIT_S = 120.0  # a restart waits up to 30 s to stop and 30 s to start, after the next pass takes it
POLL_S = 0.5


def requests_dir() -> Path:
    return cfgmod.STATE_DIR / "daemon_requests"


def result_path(req_id: str) -> Path:
    return requests_dir() / f"{req_id}{RESULT_SUFFIX}"


def request(verb: str, name: str) -> str:
    """Queue `verb` for daemon `name`; returns the request id the result is filed under."""
    req_id = uuid.uuid4().hex[:12]
    atomic_write_json(
        requests_dir() / f"{req_id}.json",
        {"id": req_id, "verb": verb, "name": name, "requested_at": time.time()},
    )
    return req_id


def await_result(req_id: str, *, timeout: float = WAIT_S, sleep=time.sleep) -> dict[str, Any]:
    """The result the supervisor's child filed, or a refusal naming the wait. A request still unclaimed
    at the deadline is withdrawn, so a supervisor that comes back later does not act on a stale one."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = read_json(result_path(req_id), default=None)
        if rec:
            result_path(req_id).unlink(missing_ok=True)
            return rec
        sleep(POLL_S)
    pending = requests_dir() / f"{req_id}.json"
    withdrawn = pending.exists()
    pending.unlink(missing_ok=True)
    return {
        "ok": False,
        "request": req_id,
        "error": (
            f"the supervisor did not {'take' if withdrawn else 'finish'} the request within {int(timeout)} s"
            + ("; withdrawn" if withdrawn else "; it may still complete -- check `run.py ps`")
        ),
    }


def take(known: set[str]) -> list[dict[str, Any]]:
    """Every queued request, removed from the queue. A valid one is returned for the caller to run; an
    invalid one (unknown verb or name, unreadable) is answered here with a refusal and never run."""
    out = []
    folder = requests_dir()
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(RESULT_SUFFIX):
            continue
        req = read_json(path, default=None) or {}
        path.unlink(missing_ok=True)
        req_id = str(req.get("id") or path.stem)
        verb, name = req.get("verb"), req.get("name")
        if verb in VERBS and name in known:
            out.append({"id": req_id, "verb": verb, "name": name})
        else:
            atomic_write_json(
                result_path(req_id),
                {
                    "ok": False,
                    "error": f"refused: {verb!r} of {name!r} is not a daemon action this suite runs",
                },
            )
    return out
