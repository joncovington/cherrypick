"""`python -m cherrypick.core.changes` — every modification under test in one module, as JSON.

    python -m cherrypick.core.changes --module M [--session YYYY-MM-DD] [--declares JSON]

`--declares` is the backtest's `read declared` items for this module (the automations' declared ideas
and settings). Read-only over the module's ledger and the advisor's store. A failure is
`{"ok": false, "error": ...}` on stdout and exit code 1, never a traceback.
"""

from __future__ import annotations

import argparse
import json

from cherrypick.core import changes as _changes
from cherrypick.core import compare as _compare


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m cherrypick.core.changes", description=__doc__.splitlines()[0]
    )
    ap.add_argument("--module", required=True, choices=sorted(_compare.MODULE_SCHEMAS))
    ap.add_argument("--session", help="as of this session (default today)")
    ap.add_argument("--declares", help="the backtest's declared items for this module, as JSON")
    args = ap.parse_args(argv)
    try:
        doc = _changes.changes(
            args.module, declares=_changes.load_declares(args.declares), session=args.session
        )
    except Exception as exc:  # noqa: BLE001 -- a bridge reports, it never tracebacks
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, **doc}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
