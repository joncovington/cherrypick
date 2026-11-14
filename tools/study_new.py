"""Write a new research study's skeleton: its script and its test file (#1071).

    python tools/study_new.py item78 dividend_spread

writes `packages/backtest/scripts/dividend_spread_item78.py` and
`packages/backtest/tests/test_dividend_spread_item78_script.py`, and refuses if either exists. The
script carries everything the process now asks of a study, empty where the declaration decides:

- `REPLAY_CORRECTIONS = ()`, the correction log (`orchestrator/studyversion.py`);
- `RULE`, the pass rule as data (`cherrypick.backtest.study.rule`), every value `None` until the
  declaration fills it, so the study refuses to judge anything before then;
- `CHAIN`, the queue steps (plan, fetch the paths it requests, study), queued with
  `run.py queue-chain --script <path>`;
- `plan` and `study` subcommands, the study writing the kit's verdict block (one run, cut at the
  rule's splits) beside the licence note.

It writes code only. The declaration (the plan section and the register row) is written by hand, as
before; this tool never touches either, and never reads licensed data.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path("packages/backtest/scripts")
TESTS = Path("packages/backtest/tests")
ITEM = re.compile(r"^item(\d+)$")
NAME = re.compile(r"^[a-z][a-z0-9_]*[a-z0-9]$")

SCRIPT = '''"""Item {n}: TITLE (one line, as the plan's heading).

The declaration is research/edge-hypotheses-plan.md section {n}; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

- `plan`: write the chain-path requests this study needs (`{{results}}/path_requests.json`).
- `study`: one run over the whole history, judged by the declared RULE (the study kit's three bars,
  cut at its splits), written to `{{results}}/results.json` as the kit's verdict block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, filled by the declaration before any run. Every None must become a value:
#: the kit refuses a rule with one, so nothing is judged until the declaration says how.
RULE = {{
    "t_bar": None,
    "min_n": None,
    "holdout_min_n": None,
    "tail": {{"per_year": None, "resamples": None, "seed": {n}}},
    "delta": None,
    "splits": [],
}}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
CHAIN = (
    {{"step": "plan", "kind": "script", "args": ["plan"], "output": "{{results}}/path_requests.json"}},
    {{
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {{"requests": "{{results}}/path_requests.json"}},
    }},
    {{"step": "study", "kind": "script", "args": ["study"], "output": "{{results}}/results.json"}},
)


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item{n}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def requests() -> list[dict]:
    """The chain paths the study reads: one request per expiry path (see `fetch-chain-paths`)."""
    raise NotImplementedError("item {n}: list the paths the declaration needs")


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{{db}}?mode=ro", uri=True)


def trades(inputs: Inputs, conn) -> dict[str, list[dict]]:
    """Each arm's trades from one run over the whole history, each with its entry `date`, `net`,
    `bp`, `days`, `rate` and, when the delta bar applies, `financed` (see the kit's `rule`). Read
    the store only through `inputs` (`inputs.sessions`, `inputs.chain`): the paths this study asked
    for, never another study's."""
    raise NotImplementedError("item {n}: replay the declared rule")


def cmd_plan(args) -> int:
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({{"requests": requests()}}, indent=1), encoding="utf-8")
    print(json.dumps({{"written": str(path)}}))
    return 0


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    conn = chain_store()
    try:
        block = kit_rule.evaluate(RULE, trades(inputs, conn))
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {{"licence": "derived from licensed vendor data: never commit or publish", "item": {n}, **block}}
    doc["inputs"] = read
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({{"written": str(path), "verdict": doc["verdict"]}}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\\n\\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
'''

TEST = '''"""Item {n}'s script ({stem}.py): invented trades only, the store never read."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "{stem}", Path(__file__).resolve().parents[1] / "scripts" / "{stem}.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_the_declared_rule_is_complete():
    # Fails until the declaration fills every value of RULE.
    kit_rule.check_rule(m.RULE)


def test_the_chain_plans_fetches_then_studies():
    assert [s["step"] for s in m.CHAIN] == ["plan", "fetch-chains", "study"]


@pytest.mark.skip(reason="item {n}: write the replay's tests on invented trades")
def test_the_replay():
    raise AssertionError
'''


def paths_for(item: str, name: str) -> tuple[Path, Path]:
    stem = f"{name}_{item}"
    return SCRIPTS / f"{stem}.py", TESTS / f"test_{stem}_script.py"


def write(item: str, name: str, *, repo: Path = REPO) -> list[Path]:
    m = ITEM.match(item)
    if not m:
        raise SystemExit(f"{item!r}: the study is itemNN")
    if not NAME.match(name) or name.endswith(f"_{item}"):
        raise SystemExit(f"{name!r}: a lowercase snake_case name, without the item number")
    script, test = paths_for(item, name)
    existing = [p for p in (script, test) if (repo / p).exists()]
    if existing:
        raise SystemExit(f"already there: {', '.join(str(p) for p in existing)}")
    n, stem = m.group(1), script.stem
    (repo / script).write_text(SCRIPT.format(n=n), encoding="utf-8")
    (repo / test).write_text(TEST.format(n=n, stem=stem), encoding="utf-8")
    return [script, test]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("item", help="the study, itemNN (take NN from `cherrypick hypotheses`' next_id)")
    ap.add_argument("name", help="what it studies, snake_case (dividend_spread)")
    args = ap.parse_args(argv)
    for p in write(args.item, args.name):
        print(p.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
