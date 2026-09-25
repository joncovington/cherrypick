"""The argv the supervisor runs this module's fee reconciler with must be one its parser accepts.

The orchestrator's config example ships modules.flies.paper.fee_reconcile_argv, and a user's config is
copied from it. bwb's reconciler shipped without the --symbol that argv passes, so every scheduled
run died in argparse (exit 2) before reconciling anything -- and the orchestrator's own test only
checked the module path, not that the module accepts its arguments. This parses the shipped argv
through the module's real parser.
"""

import json
from pathlib import Path

from cherrypick.flies import fee_reconcile

EXAMPLE = Path(__file__).resolve().parents[2] / "orchestrator" / "config.example.json"


def test_the_shipped_fee_reconcile_argv_is_one_this_module_accepts():
    argv = json.loads(EXAMPLE.read_text(encoding="utf-8"))["modules"]["flies"]["paper"]["fee_reconcile_argv"]
    assert argv[:2] == ["-m", "cherrypick.flies.fee_reconcile"], argv
    args = fee_reconcile.build_parser().parse_args(argv[2:])  # SystemExit(2) is the failure
    assert args.symbol == "SPX"
