"""Where this package is allowed to write, proven by snapshotting the tree around it.

The advisor reads six packages' databases and every deployed config. The claim that it writes to
none of them is the load-bearing one — it is what makes "read-only over every module" true rather
than intended. So: seed a home that looks like a real one, run everything the advisor can do, and
diff the whole tree. Anything created or modified outside `data/advisor/**` and `state/advice/*.json`
fails the test, whatever produced it.

The walk is the real one — factpack, admit, enact — driven through the CLI, against a home seeded
with every module's databases and configs. Anything the package can do, it does here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import fakes

from cherrypick.advisor import store

SESSION = fakes.anchor_session()  # clock-derived: a literal date expires and reds the suite

ADMITTED_REPLY = {
    "observations": ["control is taking fewer stops than width-5"],
    "flags": [{"module": "meic", "severity": "info", "text": "gex flipped negative at 14:40"}],
    "proposals": [
        {
            "kind": "bounded_adjustment",
            "module": "meic",
            "sessions": 15,
            "hypothesis": "a wider stop trigger survives midday chop",
            "params": [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "wider"}],
        },
        {
            "kind": "creative",
            "module": "flies",
            "title": "a 15-wide wing arm",
            "spec_json": {"arm": "width-15"},
        },
    ],
}


def _snapshot(root: Path) -> dict[str, tuple[int, float]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def _seed_home(home: Path) -> Path:
    """A home that looks like a machine which has been trading: real ledgers with rows in them, a
    module that accepts advice, live ledgers beside the paper ones, and orchestrator state."""
    fakes.seed_suite(home, SESSION)
    fakes.write_config(home, "meic", fakes.advice_block({"stop_trigger_ratio": {"min": 0.85, "max": 0.95}}))
    fakes.write_config(home, "flies", {"live": {"enabled": False}})
    fakes.write_suite_config(home, {"enabled": True, "modules": {"meic": {"enabled": True}}})
    state = home / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "console.heartbeat").write_text("", encoding="utf-8")
    (state / "desk").mkdir(exist_ok=True)
    (state / "desk" / "journal.jsonl").write_text('{"event": "refused"}\n', encoding="utf-8")

    raw = home / "reply.txt"
    raw.write_text("Here is what I see:\n" + json.dumps(ADMITTED_REPLY), encoding="utf-8")
    return raw


def _cli(*argv: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "cherrypick.advisor", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"{argv}: {proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout)


def _run_everything(raw: Path) -> None:
    """The whole deterministic pipeline, through the CLI the script and console actually call."""
    _cli("init-db")
    _cli("factpack", "--slot", "deep", "--session", SESSION)
    admitted = _cli("admit", "--slot", "deep", "--session", SESSION, "--raw", str(raw), "--model", "opus")
    assert admitted["admitted"], admitted
    _cli("enact", "--session", SESSION)
    _cli("verdicts", "--session", SESSION)
    _cli("status", "--session", SESSION)

    conn = store.connect()
    experiment = store.experiments(conn, status="active")[0]["id"]
    conn.close()
    _cli("kill", experiment)
    _cli("dismiss", str(admitted["admitted"][-1]["proposal_id"]))


def _allowed(rel: str) -> bool:
    parts = Path(rel).parts
    if parts[:2] == ("data", "advisor"):
        return True
    return parts[:1] == ("state",) and parts[1:2] == ("advice",)


def test_nothing_outside_the_advisors_own_two_places_is_touched(tmp_home):
    raw = _seed_home(tmp_home)
    before = _snapshot(tmp_home)

    _run_everything(raw)

    after = _snapshot(tmp_home)
    changed = [rel for rel, stat in after.items() if before.get(rel) not in (None, stat)]
    created = [rel for rel in after if rel not in before]
    offenders = sorted({rel for rel in changed + created if not _allowed(rel)})
    assert not offenders, (
        "the advisor wrote outside data/advisor/** and state/advice/*.json — it is read-only over "
        f"every other package: {offenders}"
    )
    assert any(rel.startswith(str(Path("data") / "advisor")) for rel in created), (
        "nothing was written at all — the snapshot would pass vacuously"
    )
    assert any(rel.startswith(str(Path("state") / "advice")) for rel in created), (
        "no advice artifact was issued — the enact half of the walk did not happen"
    )


def test_deleting_nothing_is_also_part_of_read_only(tmp_home):
    raw = _seed_home(tmp_home)
    before = set(_snapshot(tmp_home))
    _run_everything(raw)
    assert before <= set(_snapshot(tmp_home)), "a file the advisor does not own disappeared"
