"""Resolve futures product codes to DXLink streamer symbols, and write the map the regime recorder
reads.

Why this is a script and not part of `packages/gex`: the recorder is a credential-free, network-free
stream-cache consumer, and it stays that way. Contract resolution needs the broker's instruments
endpoint, so it lives out here behind the same fence as `eod_narrative.py` and
`advisor_checkpoint.py` — a scheduled process, never imported by a loop, whose failure costs a
refresh and nothing else.

**Never assemble a futures symbol by hand.** The 2026-08-24 entitlement probe guessed
`/VXU26:XCFE`, saw nothing, and would have concluded "CFE is not entitled" — the wrong answer from a
wrong guess. The MIC is `XCBF`, and the only authority for that is the endpoint. So this asks and
records what it is told, including the exchange suffix.

Read-only against the broker: it lists instruments and places nothing. Best-effort: a failure leaves
the previous map exactly where it was, and a map that goes stale makes the recorder drop its futures
readings rather than sample a rolled-off contract.

    python scripts/refresh_futures_contracts.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

# Product codes to resolve, and how many contracts of each the map should carry.
#
# VX takes two because the ROLL YIELD is a relationship between consecutive expirations — the
# VX1/VX2 spread and the VIX-to-VX1 basis are the object `packages/curve` harvests through VXX, and
# one contract cannot express either. ZN takes one because the rates read only needs a liquid
# tenor, not a curve.
PRODUCTS = {"VX": 2, "ZN": 1}

OUT_PATH_NAME = "futures_contracts.json"


def _out_path() -> Path:
    return _home.state_dir() / OUT_PATH_NAME


async def resolve(session) -> dict:
    from tastytrade.instruments import Future

    out: dict[str, list[dict]] = {}
    for code, want in PRODUCTS.items():
        contracts = await Future.get(session, product_codes=[code])
        rows = []
        for f in sorted(contracts, key=lambda c: c.expiration_date):
            rows.append(
                {
                    "symbol": f.symbol,
                    "streamer_symbol": f.streamer_symbol,
                    "expiration": f.expiration_date.isoformat(),
                    "active_month": bool(getattr(f, "active_month", False)),
                }
            )
        # VX wants consecutive expirations (a curve); ZN wants the ACTIVE month, which is not always
        # the nearest — on 2026-08-24 September still traded while December was the active contract,
        # because liquidity leaves a Treasury future ahead of first notice. Taking "nearest" there
        # would record the illiquid tail of an expiring contract as though it were the rates market.
        if code == "ZN":
            active = [r for r in rows if r["active_month"]]
            rows = (active or rows)[:want]
        else:
            rows = rows[:want]
        out[code] = rows
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="resolve and print, write nothing")
    args = ap.parse_args(argv)

    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1
    try:
        session = SessionManager(store).get_session()
        contracts = asyncio.run(resolve(session))
    except Exception as exc:  # noqa: BLE001 — a refresh failure must leave the old map alone
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1

    payload = {
        "refreshed_at": datetime.now(UTC).isoformat(),
        "source": "tastytrade instruments endpoint (Future.get product_codes)",
        "contracts": contracts,
    }
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    path = _out_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees a partial map
    print(json.dumps({"ok": True, "path": str(path), "contracts": contracts}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
