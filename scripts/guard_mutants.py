"""Show that the suite's guards can still fail.

"A guard has to be shown to fail" (the root CLAUDE.md): a green check that cannot fire reads as
coverage and is worse than none. This runs each guard's test twice -- clean, where it must pass,
and under its mutant (`guard_mutants_plugin.MUTANTS`), where it must fail on an assertion. A
mutant whose tests still pass SURVIVED: the guard no longer catches the regression it was written
for. That, a clean run failing, or a mutant that cannot even be applied (its target renamed) is a
WARNING through the suite's notifier; a clean run says nothing.

A guard can only stop failing when code changes -- the tests build their own fixtures, so no
market data reaches them -- so the trigger is a change, not the calendar:
  * CI runs it on every push (the `guards` job; `scripts/ci-local.sh` runs it locally);
  * the `guard-mutants` supervisor job runs it before the bell with `--if-changed`, which skips
    when the checkout this machine trades from (HEAD, every uncommitted change, the interpreter)
    matches the last PASSING run -- so an uncommitted edit is re-checked before entries open, and
    a quiet morning costs one `git` call.

Read-only: it runs tests, which build their fixtures in temporary homes, and patches code only
inside each test process. Writes one summary to `state/guard-mutants.last.json`.

    python scripts/guard_mutants.py [--only ID ...] [--if-changed] [--json] [--list-packages]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import guard_mutants_plugin as _plugin  # noqa: E402 -- the table lives beside the plugin that applies it

# pytest's exit codes: 0 all passed, 1 some failed; anything else is an error, not a verdict.
PASSED, FAILED = 0, 1
TIMEOUT_S = 600


def _python() -> str:
    """A console interpreter for the child pytest: the supervisor launches this under pythonw."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        sibling = exe.with_name("python.exe")
        if sibling.exists():
            return str(sibling)
    return str(exe)


def run_pytest(package: str, tests: tuple[str, ...], mutant_id: str | None = None) -> tuple[int, str]:
    """Run `tests` in packages/<package>, optionally under a mutant. Returns (exit code, output)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(HERE), env.get("PYTHONPATH", "")]))
    env.pop(_plugin.ENV, None)
    if mutant_id:
        env[_plugin.ENV] = mutant_id
    argv = [_python(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "guard_mutants_plugin", *tests]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            argv,
            cwd=ROOT / "packages" / package,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {TIMEOUT_S}s"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _tail(output: str, n: int = 6) -> str:
    return "\n".join(output.splitlines()[-n:])


def killed_by_an_assertion(output: str) -> bool:
    """A mutant counts as killed only when a guard ASSERTED against it. A test that crashed under the
    mutant (a TypeError, an import error inside the test) proves the code changed, not that the
    guard noticed -- the very distinction this check exists to keep."""
    return "AssertionError" in output or "\nE       assert " in output or "\nE   assert " in output


def check(mutants) -> dict:
    """Baseline per package, then every mutant. Pure orchestration over `run_pytest`."""
    results: list[dict] = []
    baseline: dict[str, dict] = {}
    by_package: dict[str, list] = {}
    for m in mutants:
        by_package.setdefault(m.package, []).append(m)
    for package, ms in by_package.items():
        tests = tuple(dict.fromkeys(t for m in ms for t in m.tests))
        code, out = run_pytest(package, tests)
        baseline[package] = {
            "ok": code == PASSED,
            "exit": code,
            **({} if code == PASSED else {"tail": _tail(out)}),
        }
        for m in ms:
            row = {"id": m.id, "breaks": m.breaks, "package": package}
            try:
                _plugin.resolve(m)
            except Exception as exc:  # noqa: BLE001 -- a vanished target is the finding
                results.append({**row, "verdict": "error", "detail": f"cannot apply: {exc}"})
                continue
            if code != PASSED:
                results.append({**row, "verdict": "error", "detail": "the clean run already fails"})
                continue
            mcode, mout = run_pytest(package, m.tests, m.id)
            if mcode == FAILED and killed_by_an_assertion(mout):
                results.append({**row, "verdict": "killed"})
            elif mcode == FAILED:
                results.append(
                    {**row, "verdict": "error", "detail": f"failed without an assertion: {_tail(mout)}"}
                )
            elif mcode == PASSED:
                results.append({**row, "verdict": "survived", "detail": _tail(mout)})
            else:
                results.append({**row, "verdict": "error", "detail": f"pytest exit {mcode}: {_tail(mout)}"})
    ok = all(b["ok"] for b in baseline.values()) and all(r["verdict"] == "killed" for r in results)
    return {"ok": ok, "baseline": baseline, "mutants": results}


def _state_path() -> Path:
    from cherrypick.core import home as _home

    return _home.state_dir() / "guard-mutants.last.json"


def checkout_fingerprint() -> str | None:
    """What a guard verdict depends on: HEAD, every uncommitted change to tracked files, every
    untracked file (by name, size and mtime), and the interpreter. None when git cannot answer --
    the caller then runs rather than trusting a verdict it cannot match."""
    import hashlib

    def git(*args) -> bytes:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, check=True, timeout=60, creationflags=flags
        ).stdout

    try:
        digest = hashlib.sha256()
        digest.update(git("rev-parse", "HEAD"))
        digest.update(git("diff", "HEAD", "--binary"))
        for name in sorted(git("ls-files", "--others", "--exclude-standard").decode().splitlines()):
            st = (ROOT / name).stat()
            digest.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    except (OSError, subprocess.SubprocessError):
        return None
    digest.update(sys.version.encode())
    return digest.hexdigest()


def _last_report() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- no record is "never passed", which means run
        return {}


def _warn(report: dict) -> None:
    bad = [r for r in report["mutants"] if r["verdict"] != "killed"]
    lines = [
        f"{r['id']}: {r['verdict']} -- {r['breaks']}" + (f"\n  {r['detail']}" if r.get("detail") else "")
        for r in bad
    ]
    lines += [f"baseline {p}: exit {b['exit']}" for p, b in report["baseline"].items() if not b["ok"]]
    message = "\n".join(lines) + "\nRun: python scripts/guard_mutants.py"
    print(f"WARNING: guard check\n{message}", file=sys.stderr)
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify(
            "WARNING", "guard_mutants", "A guard can no longer fail", message
        )
    except Exception:  # noqa: BLE001 -- a machine without the notifier loses only the notification
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", metavar="ID", help="run just these mutants")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument(
        "--if-changed",
        action="store_true",
        help="skip when the checkout matches the last passing run (the scheduled pre-bell job)",
    )
    ap.add_argument(
        "--list-packages", action="store_true", help="print the packages the mutants test, for CI's install"
    )
    args = ap.parse_args(argv)
    if args.list_packages:
        print(" ".join(dict.fromkeys(m.package for m in _plugin.MUTANTS)))
        return 0
    mutants = [m for m in _plugin.MUTANTS if not args.only or m.id in args.only]
    if args.only and len(mutants) != len(set(args.only)):
        print(f"unknown mutant id; known: {', '.join(_plugin.BY_ID)}", file=sys.stderr)
        return 2
    fingerprint = checkout_fingerprint()
    if args.if_changed and fingerprint is not None and not args.only:
        last = _last_report()
        if last.get("ok") and last.get("fingerprint") == fingerprint:
            print(f"unchanged since the passing run at {last.get('ran_at')}; skipped")
            return 0
    started = time.monotonic()
    report = check(mutants)
    report["ran_at"] = datetime.now(UTC).isoformat()
    report["seconds"] = round(time.monotonic() - started, 1)
    # A partial run (`--only`) proves only its own mutants, so it never vouches for the checkout.
    report["fingerprint"] = fingerprint if not args.only else None
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 -- the verdict matters more than the record of it
        pass
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report["mutants"]:
            print(f"{r['verdict']:9} {r['id']:28} {r['breaks']}")
        print(f"{'OK' if report['ok'] else 'FAILED'} in {report['seconds']}s")
    if not report["ok"]:
        _warn(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
