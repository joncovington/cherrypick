"""One real order, then cancel: the live round trip of the order seam, run by hand.

Places ONE deliberately unfillable SPX put credit spread through `cherrypick.core.execution.Broker`
with `live=True`, then proves the parts of the workflow a dry run cannot:

  1. the broker's copy of the live order carries the external identifier the seam stamped
     (read back through `orders_today`, the recovery read);
  2. the account streamer pushes the order's state changes;
  3. the cancel path leaves the order terminal at the broker, and the status poll agrees.

Unfillable by construction: a credit close to the spread's full width on a far out-of-the-money
spread a week out. Nobody pays that, so the order rests as Live until it is cancelled seconds
later. Buying power is held for those seconds and released on the cancel.

Refuses to run without `--confirm`, while the suite halt flag is present, or outside a window of
regular hours. It bypasses the flies module's own live gates on purpose -- the pilot stays
unarmed -- and is the discretionary, human-scheduled exception the desk exists for, except that
what is under test here is the shared seam itself, which the desk deliberately does not use.

Writes `~/.cherrypick/state/live_order_smoke-<date>.json` and prints the same report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import broker as _broker
from cherrypick.core import execution as _execution
from cherrypick.core import home as _home
from cherrypick.core.clock import now_et

WINDOW_START = (9, 40)
WINDOW_END = (15, 30)
MIN_DTE = 5
MAX_WIDTH = 10.0  # widest spread accepted; buying power held is width x 100 for ~20 seconds
OTM_FLOOR = 0.10  # short strike at least this far below spot
UNFILLABLE_GAP = 0.50  # credit asked = width - this: nobody pays near-full width for a far-OTM spread
ALERT_WAIT_S = 20.0


def _state_dir() -> Path:
    return _home.state_dir()


def _halted() -> bool:
    return _home.halt_flag_path().exists()


def _in_window(now: datetime) -> bool:
    hm = (now.hour, now.minute)
    return WINDOW_START <= hm <= WINDOW_END and now.weekday() < 5


def _pick_legs() -> tuple[dict, dict, float, float]:
    """Two adjacent cached SPXW puts, the deepest pair at most MAX_WIDTH apart and at least OTM_FLOOR
    below spot, on the furthest cached expiration at least MIN_DTE days out. Returns (short, long,
    spot, width)."""
    path = os.path.expanduser("~/.cherrypick/data/marketdata/stream_cache.db")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    spot_row = con.execute("SELECT last FROM stream_trades WHERE symbol='SPX'").fetchone()
    spot = float(spot_row[0]) if spot_row else 0.0
    if spot <= 0:
        raise SystemExit("no SPX trade in the stream cache; the streamer is not running")
    floor = (date.today() + timedelta(days=MIN_DTE)).isoformat()
    exps = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT expiration FROM stream_chain WHERE underlying_symbol='SPX' AND expiration>=? "
            "ORDER BY expiration",
            (floor,),
        )
    ]
    if not exps:
        raise SystemExit(f"no SPX expiration >= {floor} in the stream cache")
    exp = exps[-1]
    puts = {}
    for (dj,) in con.execute(
        "SELECT data_json FROM stream_chain WHERE underlying_symbol='SPX' AND expiration=?", (exp,)
    ):
        d = json.loads(dj)
        if d.get("option_type") == "P":
            puts[float(d["strike_price"])] = d
    # The cache holds only the strikes the streamer's window asked for, and deep OTM the listed
    # spacing widens to 25 points anyway. So take the DEEPEST adjacent pair the cache has that is
    # at most MAX_WIDTH apart and at least OTM_FLOOR below spot, and price off the real width.
    strikes = sorted(puts)
    pairs = [
        (lo, hi)
        for lo, hi in zip(strikes, strikes[1:], strict=False)
        if 0 < hi - lo <= MAX_WIDTH and hi <= spot * (1 - OTM_FLOOR)
    ]
    if not pairs:
        raise SystemExit(
            f"no adjacent put pair <= {MAX_WIDTH} wide below {spot * (1 - OTM_FLOOR):.0f} on {exp}"
        )
    long_k, short_k = pairs[0]
    return puts[short_k], puts[long_k], spot, short_k - long_k


def _make_broker() -> _execution.Broker:
    from cherrypick.flies import credentials as creds

    return _execution.Broker(
        get_session=creds.get_session,
        designated_account=creds.designated_account,
        live_gates=lambda: ["suite halt flag present"] if _halted() else [],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--confirm", action="store_true", help="actually place (and cancel) one live order")
    ap.add_argument("--ignore-window", action="store_true")
    args = ap.parse_args()
    now = now_et()
    report: dict = {"at": now.isoformat(), "steps": []}

    def step(name: str, **info):
        report["steps"].append({"step": name, "at": now_et().isoformat(), **info})
        print(json.dumps({"step": name, **info}, default=str), flush=True)

    if not args.confirm:
        print("refusing: pass --confirm to place one real order", file=sys.stderr)
        return 2
    if _halted():
        print("refusing: suite halt flag present", file=sys.stderr)
        return 2
    if not args.ignore_window and not _in_window(now):
        print(f"refusing: {now:%H:%M} ET is outside {WINDOW_START}-{WINDOW_END}", file=sys.stderr)
        return 2

    short, long_, spot, width = _pick_legs()
    credit = round(width - UNFILLABLE_GAP, 2)
    ext = f"smoke-{now:%Y%m%d}-{uuid.uuid4().hex[:10]}"
    spec = {
        "legs": [
            {
                "symbol": short["symbol"],
                "action": "Sell to Open",
                "quantity": 1,
                "instrument_type": "Equity Option",
            },
            {
                "symbol": long_["symbol"],
                "action": "Buy to Open",
                "quantity": 1,
                "instrument_type": "Equity Option",
            },
        ],
        "price": credit,
        "price_effect": "Credit",
        "order_type": "Limit",
        "time_in_force": "Day",
        "external_identifier": ext,
    }
    step(
        "legs",
        spot=spot,
        short=short["symbol"],
        long=long_["symbol"],
        width=width,
        credit=credit,
        external_identifier=ext,
    )

    b = _make_broker()
    b._ensure()
    step("session", user_agent=b._session._client.headers.get("user-agent"))

    # 1. place (the seam dry-runs first, then submits live)
    res = b.place(spec, live=True)
    step(
        "place",
        ok=res.get("ok"),
        order_id=res.get("order_id"),
        recovered=res.get("recovered"),
        error=res.get("error"),
        held=b.held,
    )
    order_id = res.get("order_id")
    if not res.get("ok") or not order_id:
        report["verdict"] = "FAILED at place"
        return _finish(report, 1)

    # 2. the broker's copy carries our identity (the recovery read)
    mine = [o for o in b.orders_today() if o.get("external_identifier") == ext]
    step(
        "read_back",
        found=bool(mine),
        order_ids=[str(o.get("order_id")) for o in mine],
        statuses=[o.get("status") for o in mine],
        matches_placed=any(str(o.get("order_id")) == str(order_id) for o in mine),
    )

    # 3. the account streamer pushes the cancel; subscribe first, then cancel, then collect
    async def push_then_cancel():
        waiter = asyncio.ensure_future(
            _broker.wait_for_order_alerts(b._session, b._account, {str(order_id)}, ALERT_WAIT_S)
        )
        await asyncio.sleep(2.0)  # let the socket connect and subscribe before the cancel
        cancelled = await _broker.cancel_order(b._account, b._session, order_id)
        alerts = await waiter
        return cancelled, alerts

    cancelled, alerts = b.run(push_then_cancel())
    step("cancel", **{k: cancelled.get(k) for k in ("ok", "status", "error") if k in cancelled})
    step("alerts", count=len(alerts), pushed=[{k: a.get(k) for k in ("order_id", "status")} for a in alerts])

    # 4. status agrees, and nothing is left working
    final = b.status(order_id)
    working = [o for o in b.working_orders() if str(o.get("order_id")) == str(order_id)]
    step("final", status=final.get("status"), still_working=bool(working))

    terminal = str(final.get("status") or "").lower() in {"cancelled", "canceled", "rejected", "expired"}
    ok = bool(mine) and cancelled.get("ok") and terminal and not working
    report["verdict"] = "PASS" if ok else "CHECK"
    return _finish(report, 0 if ok else 1)


def _finish(report: dict, code: int) -> int:
    out = _state_dir() / f"live_order_smoke-{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "report": str(out)}), flush=True)
    return code


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # the proactor loop's teardown noise on Windows is not a result
