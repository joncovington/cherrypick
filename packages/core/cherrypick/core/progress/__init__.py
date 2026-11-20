"""cherrypick.core.progress -- how far a long job has got, written by the job and read by anyone.

A validation, a replay check, a backtest run or a chain fetch can run for an hour. Until 2026-10-26
the only one the console could measure was a chain fetch, and only by `queue-list` starting a
counting subprocess per row on every poll; the first MEIC validation ran an hour with an empty log
and no way to tell how far it had got (progress-queue-arms-plan (archived), phase 1).

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
fraction and ETA across nested phases, the innermost counted phase's rate (``8.2 s/path``),
the ETA of only the step under way where a job counts unlike stages in a phase whose unit is
``steps`` (`steps_after` says how many follow),
``stalled`` (alive, no update for `STALL_SECONDS`) and
``orphaned`` (the process is gone and never wrote a final state).

**Per symbol.** A phase may split its count by key (`phase(..., by={"SPX": 120, "QQQ": 80})`,
`advance(..., key="SPX")`): the view's `by_symbol` is each key's done, total and share, read from the
outermost phase that splits, and the line names the key being worked on with its own count. Every
fetch and study reports both the whole and each symbol.

Telemetry, never a dependency: a write that fails is skipped, and nothing here raises into the job.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core.jsonio import write_json_atomic

ENV = "CHERRYPICK_PROGRESS_FILE"
STALL_SECONDS = 600
RETAIN_DAYS = 30
_THROTTLE_S = 1.0

__all__ = [
    "ENV",
    "Reporter",
    "counts_by",
    "progress_dir",
    "read_all",
    "read_job",
    "prune",
    "summary_line",
    "track",
]


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

    def phase(
        self, name: str, *, total: int | None = None, unit: str = "", by: Mapping[str, int] | None = None
    ) -> None:
        """Open (or reset) a phase. A phase opened after others nests inside them. `by` splits its
        total by key (a symbol, mostly): `{key: total}`, counted by `advance(..., key=)`."""
        found = self._find(name)
        entry: dict[str, Any] = {
            "name": name,
            "done": 0,
            "total": total,
            "unit": unit,
            "current": None,
            "started_at": self._clock(),
        }
        if by is not None:
            entry["by"] = {str(k): {"done": 0, "total": int(v)} for k, v in by.items()}
        if found is None:
            self.doc["phases"].append(entry)
        else:
            found.pop("skipped", None)  # a reopened phase has skipped nothing yet
            found.pop("by", None)
            found.update(entry)
        self._write(force=True)

    def advance(
        self, name: str, n: int = 1, *, current: Any = None, skipped: bool = False, key: Any = None
    ) -> None:
        """Count `n` items of a phase done. `skipped` marks items done without work (a resumed job's
        finished sessions): they count toward the share done, never toward the rate or the ETA. `key`
        counts them toward that key of the phase's `by` as well (a key it was not opened with is
        added, with no total), and names it as the key being worked on."""
        found = self._find(name)
        if found is None:
            self.phase(name)
            found = self._find(name)
        found["done"] = int(found["done"]) + n
        if skipped:
            found["skipped"] = int(found.get("skipped") or 0) + n
        if key is not None:
            slot = found.setdefault("by", {}).setdefault(str(key), {"done": 0, "total": None})
            slot["done"] = int(slot["done"]) + n
            found["key"] = str(key)
        if current is not None:
            found["current"] = str(current)
        self._write()

    def split(self, name: str, keys: Iterable[Any], *, unit: str = "", done: Iterable[Any] = ()) -> None:
        """Open a phase over items whose keys are `keys` (one per item: its symbol), split by key, and
        count the items already done (`done`, their keys) as skipped: a resumed job's view starts where
        it stopped, overall and per key."""
        keys = list(keys)
        self.phase(name, total=len(keys), unit=unit, by=counts_by(keys))
        for k, n in counts_by(done).items():
            self.advance(name, n, skipped=True, key=k)

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


def _rate(phases: list[dict], now: float) -> dict | None:
    """The innermost phase that has counted something: its average seconds per item since it
    opened. An average, not the last item's time, so request jitter does not move it; a phase
    reopened for each outer item restarts its clock, so there it is that item's average. Items the
    job skipped (`advance(..., skipped=True)`, a resume's finished sessions) are left out: until
    2026-11-06 they counted, and a resumed run read 4 s a session where it took 57."""
    for p in reversed(phases):
        worked, started = _worked(p), p.get("started_at")
        if worked > 0 and started is not None and now > started:
            return {"phase": p["name"], "seconds_each": round((now - started) / worked, 2)}
    return None


def _worked(p: dict) -> int:
    """A phase's items done by work, its skipped ones aside."""
    return max(0, int(p.get("done") or 0) - int(p.get("skipped") or 0))


STEPS = "steps"


def _steps_after(phases: list[dict]) -> int | None:
    """Steps still to come after the one under way, where the job counts its stages as `steps`
    (unlike stages: a fetch, then its VIX pass); None when it does not."""
    for p in phases:
        if p.get("unit") == STEPS and p.get("total"):
            return max(0, int(p["total"]) - int(p.get("done") or 0) - 1)
    return None


def _step_eta(phases: list[dict], now: float) -> float | None:
    """Seconds left in the step under way: its innermost counted phase's pace over the items it
    worked. The steps are unlike, so the pace of one says nothing of the next."""
    for p in reversed(phases):
        if p.get("unit") == STEPS:
            return None
        total, worked, begun = p.get("total"), _worked(p), p.get("started_at")
        if total and worked > 0 and begun is not None and now > begun:
            return (now - begun) / worked * max(0, int(total) - int(p.get("done") or 0))
    return None


def _eta(phases: list[dict], share: float, started: float, now: float) -> float | None:
    """Seconds left. In a job counted in `steps`, only the step under way (`_step_eta`); across
    other nested phases, the job's elapsed time scaled by its share left; where a phase skipped
    items, that phase's own pace over the items it worked, so a resume does not read as nearly
    finished."""
    if _steps_after(phases) is not None:
        return _step_eta(phases, now)
    for p in phases:
        skipped, total = int(p.get("skipped") or 0), p.get("total")
        if skipped and total:
            worked, begun = _worked(p), p.get("started_at")
            if worked <= 0 or begun is None or now <= begun:
                return None
            return (now - begun) / worked * max(0, int(total) - int(p.get("done") or 0))
    return (now - started) * (1 - share) / share


def counts_by(keys: Iterable[Any]) -> dict[str, int]:
    """`{key: how many}` over a job's items, in first-seen order: the `by` a phase opens with."""
    out: dict[str, int] = {}
    for k in keys:
        out[str(k)] = out.get(str(k), 0) + 1
    return out


def track(
    items: Iterable[Any],
    *,
    job: str,
    phase: str,
    unit: str = "",
    key: Callable[[Any], Any] | None = None,
    current: Callable[[Any], Any] | None = None,
    reporter: Reporter | None = None,
):
    """Yield `items`, counting each done once the loop body has run, overall and (with `key`, the
    item's symbol) per symbol. A study wraps its main loop in this one call:

        for row in progress.track(rows, job="item89-study", phase="trades", key=lambda r: r["fund"]):
            ...

    Without `reporter` it writes its own job file and finishes it: `done` when the loop runs to its
    end, `failed` (noting how far it got) when it stops before. Telemetry only: what the loop
    computes never depends on it."""
    items = list(items)
    rep = reporter or Reporter(job)
    if key is None:
        rep.phase(phase, total=len(items), unit=unit)
    else:
        rep.split(phase, (key(i) for i in items), unit=unit)
    try:
        for item in items:
            yield item
            rep.advance(
                phase,
                key=None if key is None else key(item),
                current=None if current is None else current(item),
            )
    except GeneratorExit:
        # The loop stopped before its last item: a `break`, or the body raised (a generator cannot
        # tell the two apart). Either way the job did not finish what it counted toward.
        if reporter is None:
            done = next((p["done"] for p in rep.doc["phases"] if p["name"] == phase), 0)
            rep.note(f"stopped at {done}/{len(items)}")
            rep.finish("failed")
        raise
    except BaseException:
        if reporter is None:
            rep.finish("failed")
        raise
    if reporter is None:
        rep.finish("done")


def _by_symbol(phases: list[dict]) -> dict[str, dict] | None:
    """Each key's done, total and share, from the outermost phase that splits its count; None when
    none does."""
    for p in phases:
        by = p.get("by")
        if by:
            return {
                k: {
                    "done": int(v.get("done") or 0),
                    "total": v.get("total"),
                    "share": round(min(1.0, int(v.get("done") or 0) / v["total"]), 4)
                    if v.get("total")
                    else None,
                }
                for k, v in by.items()
            }
    return None


def _per_item(name: str) -> str:
    """'paths' -> 'path', 'symbol-days' -> 'symbol-day': phase names are plural nouns."""
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


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
        eta = _eta(phases, share, started, now)
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
        # Set where `eta_s` covers only the step under way: how many steps follow it.
        "steps_after": _steps_after(phases) if eta is not None else None,
        "rate": _rate(phases, now) if state == "running" else None,
        "by_symbol": _by_symbol(phases),
        "note": doc.get("note"),
    }
    view["line"] = summary_line(view)
    return view


def summary_line(view: dict) -> str:
    """ "decisions 2/3 sources · sessions 3/4 · 2026-10-15 · 41.0 s/session · ~01:12 remaining"."""
    parts = []
    for p in view.get("phases") or []:
        count = f"{p.get('done', 0)}/{p['total']}" if p.get("total") else str(p.get("done", 0))
        part = f"{p['name']} {count}" + (f" {p['unit']}" if p.get("unit") else "")
        key = p.get("key")
        slot = (p.get("by") or {}).get(key) if key else None
        if slot:
            part += (
                f" [{key} {slot['done']}/{slot['total']}]"
                if slot.get("total")
                else f" [{key} {slot['done']}]"
            )
        if p.get("current"):
            part += f" ({p['current']})"
        parts.append(part)
    rate = view.get("rate")
    if rate and rate.get("seconds_each"):
        each, name = rate["seconds_each"], rate["phase"]
        parts.append(f"{each:.1f} s/{_per_item(name)}" if each >= 1 else f"{1 / each:.1f} {name}/s")
    if view.get("eta_s") is not None:
        mins = round(view["eta_s"] / 60)
        left = f"~{mins // 60:02d}:{mins % 60:02d} remaining" if mins >= 1 else "<00:01 remaining"
        after = view.get("steps_after")
        if after is not None:
            left += " in this step" + (
                f", then {after} more step{'s' if after != 1 else ''}" if after else ""
            )
        parts.append(left)
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
