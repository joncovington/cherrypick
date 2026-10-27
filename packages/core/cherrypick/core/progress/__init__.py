"""cherrypick.core.progress -- how far a long job has got, written by the job and read by anyone.

A validation, a replay check, a backtest run or a chain fetch can run for an hour. Until 2026-10-26
the only one the console could measure was a chain fetch, and only by `queue-list` starting a
counting subprocess per row on every poll; the first MEIC validation ran an hour with an empty log
and no way to tell how far it had got (docs/progress-queue-arms-plan.md, phase 1).

**One file per job** under ``state/progress/``, written atomically and at most once a second:

    {"job": "q3", "pid": 32784, "parent": null, "started_at": ..., "updated_at": ...,
     "state": "running" | "done" | "failed",
     "phases": [{"name": "decisions", "done": 1, "total": 3, "unit": "sources",
                 "current": "vendor", "started_at": ...}, ...],
     "note": null}

**Where the file goes.** A queued task gets ``CHERRYPICK_PROGRESS_FILE`` from the queue. A job
started by hand writes ``<job>-<pid>.json`` and sets the variable itself. Either way a child
process inherits the variable and, finding the file is not its own, writes a SIBLING
(``<stem>.<pid>.json``) naming its parent: two processes never write one file. The reader joins a
job's own phases with its live children's, outermost first, so a validation reads
"decisions 2/3 · sessions 3/4 · 2026-10-15".

**Reading it** costs file reads only. The view adds what a job cannot say about itself: the overall
fraction and ETA across nested phases, ``stalled`` (alive, no update for `STALL_SECONDS`) and
``orphaned`` (the process is gone and never wrote a final state).

Telemetry, never a dependency: a write that fails is skipped, and nothing here raises into the job.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core.jsonio import write_json_atomic

ENV = "CHERRYPICK_PROGRESS_FILE"
STALL_SECONDS = 600
RETAIN_DAYS = 30
_THROTTLE_S = 1.0

__all__ = ["ENV", "Reporter", "progress_dir", "read_all", "read_job", "prune", "summary_line"]


def progress_dir() -> Path:
    return _home.state_dir() / "progress"


def _pid_alive(pid: Any) -> bool:
    try:
        from cherrypick.core.looplock import pid_alive

        return bool(pid_alive(int(pid)))
    except Exception:  # noqa: BLE001 -- unknown reads as alive: never call a live job orphaned on a guess
        return True


# --------------------------------------------------------------------------- writing
class Reporter:
    """A job's progress file. Phases are opened outermost first; `advance` moves the named one.

        with Reporter("backtest-validate") as rep:
            rep.phase("decisions", total=3, unit="sources")
            for source in ...:
                rep.advance("decisions", current=source)  # before or after the work, as it reads best

    Leaving the `with` block writes the final state: `failed` on an exception, `done` otherwise."""

    def __init__(
        self,
        job: str,
        *,
        path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        throttle_s: float = _THROTTLE_S,
    ) -> None:
        self._clock = clock
        self._throttle = throttle_s
        self._last_write = 0.0
        self.parent: str | None = None
        given = path or os.environ.get(ENV)
        if given:
            target = Path(given)
            owner = _owner_pid(target)
            if owner is not None and owner != os.getpid() and _pid_alive(owner):
                # Someone else's file: report beside it, never into it.
                self.parent = target.stem
                target = target.with_name(f"{target.stem}.{os.getpid()}.json")
        else:
            target = progress_dir() / f"{job}-{os.getpid()}.json"
        self.path = target
        now = self._clock()
        self.doc: dict[str, Any] = {
            "job": job,
            "pid": os.getpid(),
            "parent": self.parent,
            "started_at": now,
            "updated_at": now,
            "state": "running",
            "phases": [],
            "note": None,
        }
        # A child started from here reports beside this file.
        root = self.path if self.parent is None else target.with_name(f"{self.parent}.json")
        os.environ[ENV] = str(root)
        self._write(force=True)

    # -- phases
    def _find(self, name: str) -> dict | None:
        return next((p for p in self.doc["phases"] if p["name"] == name), None)

    def phase(self, name: str, *, total: int | None = None, unit: str = "") -> None:
        """Open (or reset) a phase. A phase opened after others nests inside them."""
        found = self._find(name)
        entry = {
            "name": name,
            "done": 0,
            "total": total,
            "unit": unit,
            "current": None,
            "started_at": self._clock(),
        }
        if found is None:
            self.doc["phases"].append(entry)
        else:
            found.update(entry)
        self._write(force=True)

    def advance(self, name: str, n: int = 1, *, current: Any = None) -> None:
        found = self._find(name)
        if found is None:
            self.phase(name)
            found = self._find(name)
        found["done"] = int(found["done"]) + n
        if current is not None:
            found["current"] = str(current)
        self._write()

    def at(self, name: str, current: Any) -> None:
        """Name what a phase is working on without counting it done."""
        found = self._find(name)
        if found is None:
            self.phase(name)
            found = self._find(name)
        found["current"] = str(current)
        self._write()

    def close_phase(self, name: str) -> None:
        self.doc["phases"] = [p for p in self.doc["phases"] if p["name"] != name]
        self._write(force=True)

    def note(self, text: str | None) -> None:
        self.doc["note"] = text
        self._write(force=True)

    def finish(self, state: str = "done") -> None:
        self.doc["state"] = state
        self._write(force=True)

    def __enter__(self) -> Reporter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.finish("failed")
        elif self.doc["state"] == "running":  # a state the job already wrote (`failed`) stands
            self.finish("done")

    # -- the file
    def _write(self, *, force: bool = False) -> None:
        now = self._clock()
        if not force and now - self._last_write < self._throttle:
            return
        self.doc["updated_at"] = now
        try:
            write_json_atomic(self.path, self.doc, indent=None)
            self._last_write = now
        except Exception:  # noqa: BLE001 -- progress is telemetry: it never costs the job
            pass


def _owner_pid(path: Path) -> int | None:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("pid"))
    except Exception:  # noqa: BLE001 -- no file, or not ours to parse: nobody owns it
        return None


# --------------------------------------------------------------------------- reading
def _load(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _fraction(phases: list[dict]) -> float | None:
    """The overall share done across nested phases: (done + share of the next level) / total, from
    the innermost counted level out. None when no level has a total."""
    share = None
    for p in reversed(phases):
        total = p.get("total")
        if not total:
            continue
        inner = share or 0.0
        share = min(1.0, (min(int(p.get("done") or 0), int(total)) + inner) / float(total))
    return share


def _view(doc: dict, children: list[dict], now: float) -> dict:
    live = [c for c in children if c.get("state") == "running" and _pid_alive(c.get("pid"))]
    phases = list(doc.get("phases") or [])
    if live:
        phases += list(max(live, key=lambda c: c.get("updated_at") or 0).get("phases") or [])
    updated = max([doc.get("updated_at") or 0] + [c.get("updated_at") or 0 for c in live])
    state = doc.get("state") or "running"
    if state == "running":
        if not _pid_alive(doc.get("pid")):
            state = "orphaned"
        elif now - updated > STALL_SECONDS:
            state = "stalled"
    share = _fraction(phases)
    started = doc.get("started_at") or now
    eta = None
    if share and 0 < share < 1 and state == "running":
        eta = (now - started) * (1 - share) / share
    view = {
        "job": doc.get("job"),
        "pid": doc.get("pid"),
        "state": state,
        "started_at": started,
        "updated_at": updated,
        "age_s": round(now - updated, 1),
        "phases": phases,
        "fraction": None if share is None else round(share, 4),
        "eta_s": None if eta is None else round(eta),
        "note": doc.get("note"),
    }
    view["line"] = summary_line(view)
    return view


def summary_line(view: dict) -> str:
    """ "decisions 2/3 sources · sessions 3/4 · 2026-10-15 · ~12 min left"."""
    parts = []
    for p in view.get("phases") or []:
        count = f"{p.get('done', 0)}/{p['total']}" if p.get("total") else str(p.get("done", 0))
        part = f"{p['name']} {count}" + (f" {p['unit']}" if p.get("unit") else "")
        if p.get("current"):
            part += f" ({p['current']})"
        parts.append(part)
    if view.get("eta_s") is not None:
        mins = view["eta_s"] / 60
        parts.append(f"~{mins:.0f} min left" if mins >= 1 else "<1 min left")
    if view.get("state") in ("stalled", "orphaned"):
        parts.append(view["state"])
    return " · ".join(parts)


def read_all(directory: Path | None = None, *, now: float | None = None) -> list[dict]:
    """Every job's view, newest first: a root file joined with its children's."""
    directory = directory or progress_dir()
    now = time.time() if now is None else now
    if not directory.exists():
        return []
    roots: dict[str, dict] = {}
    kids: dict[str, list[dict]] = {}
    for path in directory.glob("*.json"):
        doc = _load(path)
        if doc is None:
            continue
        if doc.get("parent"):
            kids.setdefault(str(doc["parent"]), []).append(doc)
        else:
            doc["_stem"] = path.stem
            roots[path.stem] = doc
    views = [{**_view(doc, kids.get(stem, []), now), "file": stem} for stem, doc in roots.items()]
    return sorted(views, key=lambda v: v.get("started_at") or 0, reverse=True)


def read_job(stem: str, directory: Path | None = None, *, now: float | None = None) -> dict | None:
    """One job's view by its file stem (a queued task's is `q<id>`), or None."""
    return next((v for v in read_all(directory, now=now) if v.get("file") == stem), None)


def prune(directory: Path | None = None, *, days: int = RETAIN_DAYS, now: float | None = None) -> int:
    """Delete finished (or orphaned) files older than `days`. Returns how many went."""
    directory = directory or progress_dir()
    now = time.time() if now is None else now
    gone = 0
    for path in directory.glob("*.json") if directory.exists() else []:
        doc = _load(path)
        stamp = (doc or {}).get("updated_at") or path.stat().st_mtime
        finished = doc is None or doc.get("state") != "running" or not _pid_alive(doc.get("pid"))
        if finished and now - float(stamp) > days * 86400:
            try:
                path.unlink()
                gone += 1
            except OSError:
                pass
    return gone
