"""Re-run finished studies at the current code and say whether each result came out the same (#1088).

A change to shared study code (the kit, a script other scripts load, core) can move a recorded result
without any test noticing: CI has no licensed data. This re-runs each named study by the command its
last finished queue row ran, from the checkout this script sits in, and compares every result file
with the one recorded:

1. the study's result folder is copied aside;
2. the study runs (its script writes where it always writes);
3. each JSON file the study wrote is compared field by field with its recorded copy, run-time fields
   ignored (`VOLATILE`);
4. the folder is put back exactly as it was, whatever happened, and the re-run's files are kept beside
   it under `reproduce/<stamp>/<study>/` for a look.

A study is `identical`, `differs` (with the JSON paths that moved, never their values), `expected`
(it differs, and its script's correction log grew since the recorded run: the stamp says which
version wrote the result), `inputs_changed` (it differs, and its own `inputs` fingerprint moved: the
paths it declared hold other data now, so its code is not what changed; the study kit's `Inputs`),
`failed` (its run exited non-zero, with its last line) or `skipped` (why).
The report goes under the backtest results directory, like every result here, and the script exits
1 when any study differs or failed, so a research-queue row notifies.

**Before merging a change to shared code**, run it from the PR's worktree (its packages first on
`PYTHONPATH`), so the studies run on the new code against results the old code wrote. As a queue row
after the merge (`reproduce/run`) it confirms the live checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from cherrypick.core import home

REPO = Path(__file__).resolve().parents[3]
SCRIPT_HOMES = ("packages/backtest/scripts", "scripts")
#: The standing sample: every study that runs on shared chain code (59, 63, 64, 74), the kit's user
#: (77), and one of each other shape (57 weekly paths, 70 a yield screen, 76 the ladder). About 20
#: minutes together.
SAMPLE = ("item57", "item59", "item63", "item64", "item70", "item74", "item76", "item77")
#: Fields a run writes from its clock or its own path, never from its data.
VOLATILE = frozenset({"at", "written", "generated", "generated_at", "created_at", "ran_at", "run_at",
                      "started_at", "finished_at", "elapsed_s", "seconds", "code_note"})  # fmt: skip
MAX_PATHS = 20


def results_dir() -> Path:
    from cherrypick.backtest import paths

    return paths.results_dir() / "research"


def queue_db() -> Path:
    return home.state_dir() / "research_queue.db"


def last_study_rows(study: str, db: Path) -> list[dict]:
    """The command of each `study*` step's newest finished row: a study with design and holdout
    steps re-runs both, in the order they were queued."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, step, args FROM tasks WHERE study = ? AND kind = 'script' AND status = 'done' "
            "AND step LIKE 'study%' ORDER BY id",
            (study,),
        ).fetchall()
    finally:
        conn.close()
    newest: dict[str, dict] = {}
    for r in rows:
        newest[r["step"]] = {"id": r["id"], "step": r["step"], **json.loads(r["args"])}
    return sorted(newest.values(), key=lambda r: r["id"])


def script_path(rel: str, repo: Path | None = None) -> Path | None:
    """The script a row named, found where it lives now (studies moved to packages/backtest/scripts
    on 2026-11-08, after older rows were queued)."""
    repo = repo or REPO
    p = repo / rel
    if p.is_file():
        return p
    for d in SCRIPT_HOMES:
        q = repo / d / Path(rel).name
        if q.is_file():
            return q
    return None


def diff_paths(a, b, path: str = "") -> list[str]:
    """The JSON paths where `a` and `b` differ, `VOLATILE` keys ignored. Floats compare exactly: the
    same code on the same data writes the same digits."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k in VOLATILE:
                continue
            p = f"{path}.{k}" if path else str(k)
            if k not in a or k not in b:
                out.append(p)
            else:
                out += diff_paths(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}[len]"]
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += diff_paths(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [path or "."]


def _json_files(d: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(d)): p
        for p in d.rglob("*.json")
        if not p.name.endswith(".version.json") and "reproduce" not in p.parts
    }


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def compare_folders(before: Path, after: Path, since: float) -> dict:
    """Compare the JSON files the run wrote (modified at or after `since`) with their recorded copies."""
    written = {k: p for k, p in _json_files(after).items() if p.stat().st_mtime >= since}
    if not written:
        return {"status": "skipped", "why": "the run wrote no JSON file"}
    moved: dict[str, list[str]] = {}
    for name, p in written.items():
        old = before / name
        if not old.is_file():
            moved[name] = ["(new file)"]
            continue
        d = diff_paths(_load(old), _load(p))
        if d:
            moved[name] = d[:MAX_PATHS] + ([f"... {len(d) - MAX_PATHS} more"] if len(d) > MAX_PATHS else [])
    return {"status": "differs" if moved else "identical", "files": sorted(written), "moved": moved}


def _corrections(script: Path) -> int | None:
    try:
        from cherrypick.orchestrator import studyversion
    except ImportError:
        return None
    try:
        return len(studyversion.corrections(script))
    except Exception:  # noqa: BLE001 -- a malformed log is reported elsewhere; here it only reads "unknown"
        return None


def _recorded_version(folder: Path) -> int | None:
    versions = []
    for p in folder.glob("*.version.json"):
        v = (_load(p) or {}).get("version")
        if isinstance(v, int):
            versions.append(v)
    return min(versions) if versions else None


def _last_line(text: str) -> str | None:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:300] if lines else None


def reproduce(
    study: str, *, db: Path, out_root: Path, run=subprocess.run, python: str = sys.executable
) -> dict:
    """Re-run one study and compare; its result folder is always restored."""
    rows = last_study_rows(study, db)
    if not rows:
        return {"status": "skipped", "why": "no finished study row in the queue"}
    folder = results_dir() / study
    if not folder.is_dir():
        return {"status": "skipped", "why": f"no result folder {folder}"}
    scripts = [script_path(r["path"]) for r in rows]
    if any(s is None for s in scripts):
        return {"status": "skipped", "why": "a script its rows ran is no longer in the repo"}
    keep = Path(tempfile.mkdtemp(prefix=f"reproduce-{study}-"))
    shutil.copytree(folder, keep / "before")
    since = datetime.now().timestamp() - 1
    out = {"rows": [r["id"] for r in rows]}
    try:
        for r, script in zip(rows, scripts, strict=True):
            argv = [python, str(script), *r.get("args", [])]
            done = run(argv, cwd=str(REPO), capture_output=True, text=True, env=dict(os.environ))
            if done.returncode != 0:
                said = _last_line(done.stderr) or _last_line(done.stdout)
                return {**out, "status": "failed", "step": r["step"], "exit": done.returncode, "said": said}
        out.update(compare_folders(keep / "before", folder, since))
        if out["status"] == "differs":
            now, recorded = _corrections(scripts[-1]), _recorded_version(keep / "before")
            moved = [p for ps in out["moved"].values() for p in ps]
            if now is not None and recorded is not None and now > recorded:
                out.update(status="expected", why=f"its correction log grew from version {recorded} to {now}")
            elif any(p.startswith("inputs.") for p in moved):
                # The study's own `inputs` fingerprint (the kit's `Inputs`) moved: its declared paths
                # hold more or other data than when it ran, so the code is not what changed.
                out.update(status="inputs_changed", why="the inputs it declared hold different data now")
        dest = out_root / study
        dest.mkdir(parents=True, exist_ok=True)
        for name in out.get("files", []):
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / name, dest / name)
        return out
    finally:
        shutil.rmtree(folder)
        shutil.copytree(keep / "before", folder)
        shutil.rmtree(keep, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("run")
    p.add_argument(
        "--studies", nargs="*", default=None, help=f"default: the standing sample {', '.join(SAMPLE)}"
    )
    p.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = results_dir() / "reproduce" / stamp
    out_root.mkdir(parents=True, exist_ok=True)
    studies = args.studies or list(SAMPLE)
    report = {"at": stamp, "repo": str(REPO), "studies": {}}
    for s in studies:
        print(f"{s}: running", file=sys.stderr)
        report["studies"][s] = reproduce(s, db=queue_db(), out_root=out_root)
        print(f"{s}: {report['studies'][s]['status']}", file=sys.stderr)
    counts: dict[str, int] = {}
    for r in report["studies"].values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    report["counts"] = counts
    path = Path(args.out) if args.out else results_dir() / "reproduce" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "counts": counts}))
    return 1 if counts.get("differs") or counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
