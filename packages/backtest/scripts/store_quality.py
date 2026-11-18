"""Write a quality report for each chain store (`cherrypick.backtest.storequality`, #1083).

    python packages/backtest/scripts/store_quality.py run [--store eod_chains.db ...]

Reads the stores read-only and writes `results/quality/<store>.json` under the backtest results
directory (derived from licensed data: never into the repo). Run it as a research-queue row after a
fetch (`quality/run`), or by hand; the largest store takes some minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from cherrypick.core import progress

from cherrypick.backtest import paths, settings, storequality


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("run")
    p.add_argument("--store", nargs="*", default=None, help=f"default: {', '.join(storequality.STORES)}")
    args = ap.parse_args(argv)
    vendor = paths.vendor_dir(settings.load())
    out_dir = paths.results_dir() / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    present = [s for s in args.store or list(storequality.STORES) if (vendor / s).exists()]
    for store in progress.track(
        present, job="store_quality-run", phase="stores", unit="stores", key=lambda s: s.removesuffix(".db")
    ):
        src = vendor / store
        print(f"{store}: reading", file=sys.stderr)
        doc = storequality.report(src, store)
        doc["read_at"] = datetime.now().isoformat(timespec="seconds")
        dest = out_dir / f"{store.removesuffix('.db')}.json"
        dest.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        written.append(str(dest))
    summary = {"written": written}
    (out_dir / "latest.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
