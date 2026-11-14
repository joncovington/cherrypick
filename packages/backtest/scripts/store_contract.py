"""Write or check the stores' contract (`cherrypick.backtest.storecontract`, #1084).

    python packages/backtest/scripts/store_contract.py write   # from the stores on disk
    python packages/backtest/scripts/store_contract.py check   # exit 1 when a store differs

Reads table definitions and coded columns' spellings only, never a price; the contract it writes is
committed.
"""

from __future__ import annotations

import argparse
import json
import sys

from cherrypick.backtest import paths, settings, storecontract


def _vendor():
    return paths.vendor_dir(settings.load())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("write", "check"))
    args = ap.parse_args(argv)
    actual = storecontract.build(_vendor())
    if args.step == "write":
        storecontract.CONTRACT_PATH.write_text(json.dumps(actual, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"written": str(storecontract.CONTRACT_PATH), "stores": sorted(actual)}))
        return 0
    diffs = storecontract.differences(storecontract.load(), actual)
    print(json.dumps({"ok": not diffs, "differences": diffs}))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
