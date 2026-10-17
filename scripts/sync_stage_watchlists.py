"""Mirror our six stage lists to private tastytrade watchlists, where a person trades from.

**Why.** The vendor's stage watchlists (Early Breakout ... Early Breakdown) go with the subscription
on 2026-10-24. `packages/technicals` `stage-lists` rebuilds them from our own stage rule, options-
tradable names only; this puts them in the broker, one watchlist each: `cherrypick early breakout`,
`cherrypick early breakdown`, and so on.

A script rather than package code, because it reaches the broker. It is a write to the account -- a
watchlist, never an order -- so it follows the universe watchlist's rules (scripts/
build_stock_universe.py `watchlist`):

- it shows its plan and writes nothing without `--apply`, and is run by hand: it is not scheduled
  (2026-10-17), so nothing writes these lists unless a person runs it;
- it only ever replaces a list it created, marked by the `cherrypick` group: a same-named list
  without it is a person's, and a replace drops every entry left out, so it is refused;
- a list our rule leaves empty today is left as it was, not emptied, and said so;
- a stage-lists file older than `MAX_AGE_DAYS` writes nothing: a stale list mirrored as current is
  worse than yesterday's.

Unlike the universe's, there is no shrink guard: a stage list turning over most of its names in a
day is the rule working, not a broken build.

    python scripts/sync_stage_watchlists.py [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from datetime import date
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _common  # noqa: E402

GROUP = "cherrypick"
PREFIX = "cherrypick "
MAX_AGE_DAYS = 4


def watchlist_name(list_name: str) -> str:
    return PREFIX + list_name.lower()


def to_tastytrade(symbol: str) -> str:
    """The store writes a share class with a dot (BRK.B); tastytrade with a slash (BRK/B)."""
    return symbol.replace(".", "/")


def plan_one(name: str, symbols: list[str], existing: dict | None) -> dict:
    """What a sync does to one watchlist: create, replace, none, skip or refuse."""
    want = [to_tastytrade(s) for s in symbols]
    if existing is not None and existing.get("group_name") != GROUP:
        return {
            "name": name,
            "action": "refuse",
            "reason": f"a list named {name!r} exists without the {GROUP!r} group",
        }
    if not want:
        return {"name": name, "action": "skip", "reason": "our rule lists no name today; left as it was"}
    if existing is None:
        return {"name": name, "action": "create", "add": want, "remove": [], "entries": want}
    have = existing.get("symbols") or []
    add, remove = sorted(set(want) - set(have)), sorted(set(have) - set(want))
    if not add and not remove and have == want:
        return {"name": name, "action": "none", "add": [], "remove": [], "entries": want}
    return {"name": name, "action": "replace", "add": add, "remove": remove, "entries": want}


def plan(doc: dict | None, existing: dict[str, dict], today: date) -> dict:
    """The whole sync: refused outright when there is no stage-lists file or it is stale."""
    if not doc:
        return {"ok": False, "reason": "no stage-lists file; run python -m cherrypick.technicals stage-lists"}
    age = (today - date.fromisoformat(doc["session"])).days
    if age > MAX_AGE_DAYS:
        return {"ok": False, "reason": f"the stage lists are for {doc['session']}, {age} days old"}
    lists = [
        plan_one(watchlist_name(n), [r["symbol"] for r in rows], existing.get(watchlist_name(n)))
        for n, rows in (doc.get("lists") or {}).items()
    ]
    return {"ok": True, "session": doc["session"], "lists": lists}


def body(name: str, entries: list[str]) -> dict:
    return {
        "name": name,
        "group-name": GROUP,
        "watchlist-entries": [{"symbol": s, "instrument-type": "Equity"} for s in entries],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to tastytrade (default: show the plan)")
    args = ap.parse_args(argv)

    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
    from cherrypick.technicals import stagelists
    from tastytrade.watchlists import PrivateWatchlist

    store = CredentialStore(SHARED_SERVICE)
    if store.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    doc = stagelists.latest()

    async def run() -> dict:
        session = SessionManager(store).get_session()
        existing = {
            w.name: {
                "group_name": w.group_name,
                "symbols": [e.get("symbol") for e in w.watchlist_entries or [] if e.get("symbol")],
            }
            for w in await PrivateWatchlist.get(session)
            if w.name.startswith(PREFIX)
        }
        p = plan(doc, existing, date.today())
        if not p["ok"]:
            return p
        for item in p["lists"]:
            item["applied"] = False
            if args.apply and item["action"] in ("create", "replace"):
                payload = body(item["name"], item["entries"])
                if item["action"] == "create":
                    await session._post("/watchlists", json=payload)
                else:
                    await session._put(f"/watchlists/{urllib.parse.quote(item['name'])}", json=payload)
                item["applied"] = True
        return p

    try:
        p = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 -- a refused or failed write changes nothing more; say so
        _common.warn(
            "market_report.stage_watchlists", "Stage watchlist sync failed", f"{type(exc).__name__}: {exc}"
        )
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    for item in p.get("lists") or []:
        item.pop("entries", None)
    refused = [i for i in p.get("lists") or [] if i["action"] == "refuse"]
    if not p["ok"] or refused:
        why = p.get("reason") or "; ".join(i["reason"] for i in refused)
        _common.warn("market_report.stage_watchlists", "Stage watchlists not synced", why)
    print(json.dumps(p, indent=1))
    return 0 if p["ok"] and not refused else 1


if __name__ == "__main__":
    sys.exit(main())
