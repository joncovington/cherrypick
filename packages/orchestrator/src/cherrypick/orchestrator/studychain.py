"""A study's chain, declared in its own script and queued in one go (`queue-chain`, #1070).

A study used to be queued row by row by hand: the order, every `--after` link, the script path, each
output path, and an empty requests file made first so a fetch row would be accepted before the plan
row that writes it had run. A study script now declares its chain as a module-level literal:

    CHAIN = (
        {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
        {"step": "fetch-chains", "kind": "fetch-chain-paths",
         "task_args": {"requests": "{results}/path_requests.json"}},
        {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
    )

and `queue-chain --script <path>` queues every step in order, each `--after` the one before (the first
after `--after` when given). It is read by parsing the file, never by importing it, as the correction
log is (`studyversion`). In any string, `{results}` is the study's own result folder under the
backtest data directory (`$BACKTEST_DATA_DIR`, else the home's `data/backtest`, then
`results/research/<study>`) and `{home}` the suite's home.

- A `script` step's task is the script itself with the step's `args`; any other kind takes the step's
  `task_args`, checked by the queue's allow-list like any row (`researchqueue.add`).
- A fetch whose requests or plan file is an earlier step's declared `output` gets an empty placeholder
  (`{"requests": []}` or `{"sessions": {}}`) so it can be queued; that earlier step overwrites it
  before the fetch can start, and a failed earlier step holds the fetch.
- Every step is checked before any row is added: one live row per name, and each kind's arguments. A
  chain is queued whole or not at all.

The study name is the script's `itemNN` (from `..._itemNN.py`) unless `--study` names it.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import researchqueue as rq

STEP_KEYS = frozenset({"step", "kind", "args", "task_args", "output", "priority"})
ITEM = re.compile(r"_item(\d+)\.py$")
#: The empty file a fetch accepts, by the argument naming its input.
PLACEHOLDERS = {"requests": {"requests": []}, "plan": {"sessions": {}}}


class BadChain(ValueError):
    """A CHAIN that is not a literal tuple of well-formed steps."""


def read_chain(script: Path) -> list[dict]:
    """The script's CHAIN, checked; empty when it declares none."""
    tree = ast.parse(Path(script).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CHAIN" for t in node.targets
        ):
            try:
                chain = ast.literal_eval(node.value)
            except ValueError as exc:
                raise BadChain(f"{Path(script).name}: CHAIN must be a literal") from exc
            return check(chain, Path(script).name)
    return []


def check(chain, name: str = "CHAIN") -> list[dict]:
    if not isinstance(chain, (tuple, list)) or not chain:
        raise BadChain(f"{name}: CHAIN must be a non-empty tuple of steps")
    seen = set()
    for i, s in enumerate(chain):
        if not isinstance(s, dict) or not {"step", "kind"} <= set(s) or set(s) - STEP_KEYS:
            raise BadChain(
                f"{name}: step {i + 1} needs step and kind, and only {', '.join(sorted(STEP_KEYS))}"
            )
        if s["step"] in seen:
            raise BadChain(f"{name}: step {s['step']!r} appears twice")
        seen.add(s["step"])
        if s["kind"] == "script" and "task_args" in s:
            raise BadChain(f"{name}: step {s['step']!r} is a script: its task is the script, with args")
        if s["kind"] != "script" and "args" in s:
            raise BadChain(f"{name}: step {s['step']!r} is a {s['kind']}: it takes task_args, not args")
    return [dict(s) for s in chain]


def study_of(script: Path, study: str | None = None) -> str:
    if study:
        return study
    m = ITEM.search(Path(script).name)
    if not m:
        raise BadChain(f"{Path(script).name}: no _itemNN in the name; pass --study")
    return f"item{m.group(1)}"


def results_for(study: str) -> Path:
    """The study's result folder, as the backtest package places it (`paths.results_dir`)."""
    return _home.data_dir("backtest", env="BACKTEST_DATA_DIR") / "results" / "research" / study


def _fill(value: Any, subs: dict[str, str]) -> Any:
    if isinstance(value, str):
        for k, v in subs.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [_fill(v, subs) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, subs) for k, v in value.items()}
    return value


def plan_rows(
    chain: list[dict], study: str, script_rel: str, results: Path, after: str | None = None
) -> list[dict]:
    """The rows a chain queues, in order: each one's study, step, kind, task args, output and after."""
    subs = {"results": results.as_posix(), "home": _home.home().as_posix()}
    rows, prev = [], after
    for s in chain:
        s = _fill(s, subs)
        if s["kind"] == "script":
            task = {"path": script_rel, "args": list(s.get("args", []))}
        else:
            task = dict(s.get("task_args") or {})
        rows.append(
            {
                "study": study,
                "step": s["step"],
                "kind": s["kind"],
                "args": task,
                "output": s.get("output"),
                "after": prev,
                "priority": int(s.get("priority", 0)),
            }
        )
        prev = f"{study}/{s['step']}"
    return rows


def placeholders(rows: list[dict]) -> list[tuple[Path, dict]]:
    """The input files a fetch row names that an EARLIER row of the chain declares as its output and
    that do not exist yet: each with the empty content the fetch accepts."""
    out, outputs = [], set()
    for r in rows:
        for key, empty in PLACEHOLDERS.items():
            f = r["args"].get(key) if r["kind"] != "script" else None
            if isinstance(f, str) and Path(f).as_posix() in outputs and not Path(f).exists():
                out.append((Path(f), empty))
        if r.get("output"):
            outputs.add(Path(r["output"]).as_posix())
    return out


def queue_chain(
    script_rel: str,
    *,
    study: str | None = None,
    after: str | None = None,
    dry_run: bool = False,
    path: Path | None = None,
    root: Path | None = None,
    tracked=None,
    stores=None,
    results: Path | None = None,
) -> dict:
    """Queue a script's CHAIN whole: every step checked first, then each row added in order."""
    root = root or rq.repo_root()
    script = (root / script_rel).resolve()
    chain = read_chain(script)
    if not chain:
        raise BadChain(f"{script_rel} declares no CHAIN")
    study = study_of(script, study)
    rows = plan_rows(chain, study, script_rel, results or results_for(study), after)
    holders = placeholders(rows)
    live = {
        f"{r['study']}/{r['step']}": r["status"] for r in rq.rows(path, study=study) if r["status"] in rq.LIVE
    }
    clash = [f"{r['study']}/{r['step']}" for r in rows if f"{r['study']}/{r['step']}" in live]
    if clash:
        raise rq.Refused(f"already queued or running: {', '.join(clash)}")
    if dry_run:
        return {"ok": True, "dry_run": True, "study": study, "rows": rows,
                "placeholders": [str(p) for p, _ in holders]}  # fmt: skip
    made = []
    for p, empty in holders:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(empty), encoding="utf-8")
        made.append(p)
    try:
        # Check every row's kind and arguments before adding any, so a bad step leaves nothing queued.
        for r in rows:
            rq.build_argv(r["kind"], r["args"], "python", root=root, tracked=tracked)
    except rq.Refused:
        for p in made:
            p.unlink(missing_ok=True)
        raise
    added = []
    try:
        for r in rows:
            added.append(
                rq.add(
                    r["study"],
                    r["kind"],
                    r["args"],
                    step=r["step"],
                    output=r["output"],
                    after=r["after"],
                    priority=r["priority"],
                    path=path,
                    root=root,
                    tracked=tracked,
                    stores=stores,
                )
            )
    except (rq.Refused, ValueError):
        # A step the up-front check could not see (its output named like a vendor store, say): cancel
        # what was added, so the chain is queued whole or not at all.
        for a in added:
            rq.cancel(a["id"], path)
        for p in made:
            p.unlink(missing_ok=True)
        raise
    return {"ok": True, "study": study, "added": added, "placeholders": [str(p) for p in made]}
