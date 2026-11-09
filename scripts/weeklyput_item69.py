"""Item 69, item 57's unhedged arm in paper (docs/edge-hypotheses-plan.md): does paper track the replay?

    python scripts/weeklyput_item69.py track [--ledger PATH] [--out FILE]   # paper against item 57's replay

`packages/weeklyput` sells item 57's put in paper. This reads its ledger (read-only) and, for every
finished paper trade, replays the same name and expiry with item 57's own `trade_pair` over the
chain-path store's end-of-day quotes (fetched by the research queue after `index_put_weekly_item57.py
plan --through DATE`). Per trade it reports whether both picked the same strike, the paper credit
against the replay's, and paper's net in bp against the replay's unhedged net in bp; per cycle the
equal-weight means of both. The gap is execution (a close-of-session fill against an end-of-day quote,
the stream window, the posted close against the expiry forward), never the rule.

A trade the store does not yet hold is listed as `not_replayed` with the reason, never dropped. No
figure is judged here: item 69's rule reads this file at its declared count of cycles.

Writes under the backtest data dir (results/research/item69/), never into the repo: the replay's
inputs are licensed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from datetime import date
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "index_put_weekly_item57", Path(__file__).with_name("index_put_weekly_item57.py")
)
i57 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("index_put_weekly_item57", i57)
_spec.loader.exec_module(i57)

#: The module's name for each of item 57's names.
STORE_NAME = {"SPX": "SPXW", "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM"}

#: Replay corrections to this study (docs/conventions/working-rules.md). None yet.
REPLAY_CORRECTIONS: list[dict] = []


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def compare(paper: dict, replay: dict | None) -> dict:
    """One trade: paper against the replay's unhedged trade on the same name and expiry. Pure."""
    row = {
        "position_id": paper["position_id"],
        "symbol": paper["symbol"],
        "expiration": paper["expiration"],
        "paper": {
            "strike": paper["strike"],
            "credit": paper["entry_value"],
            "net": paper["net_pnl"],
            "net_bp": paper["net_bp"],
            "settle_price": paper["settle_price"],
        },
    }
    if replay is None or "drop" in replay:
        return {**row, "replayed": False, "reason": (replay or {}).get("drop", "no_path")}
    strikes = [k for k, _ in replay["strikes"]]
    return {
        **row,
        "replayed": True,
        "replay": {
            "strike": strikes[0],
            "credit": replay["premium"],
            "net": replay["net"],
            "net_bp": replay["net_bp"],
        },
        "same_strike": strikes[0] == paper["strike"],
        "credit_gap": round(paper["entry_value"] - replay["premium"], 2),
        "net_bp_gap": round(paper["net_bp"] - replay["net_bp"], 4),
    }


def summarise(rows: list[dict]) -> dict:
    done = [r for r in rows if r["replayed"]]
    by_cycle: dict[str, dict[str, list[float]]] = {}
    for r in done:
        c = by_cycle.setdefault(r["expiration"], {"paper": [], "replay": []})
        c["paper"].append(r["paper"]["net_bp"])
        c["replay"].append(r["replay"]["net_bp"])
    cycles = {
        e: {"paper_bp": _mean(v["paper"]), "replay_bp": _mean(v["replay"]), "names": len(v["paper"])}
        for e, v in sorted(by_cycle.items())
    }
    return {
        "trades": len(rows),
        "replayed": len(done),
        "not_replayed": len(rows) - len(done),
        "same_strike_share": (sum(r["same_strike"] for r in done) / len(done)) if done else None,
        "mean_net_bp_gap": _mean(r["net_bp_gap"] for r in done),
        "mean_credit_gap": _mean(r["credit_gap"] for r in done),
        "cycles": cycles,
        "paper_cycle_mean_bp": _mean(c["paper_bp"] for c in cycles.values()),
        "replay_cycle_mean_bp": _mean(c["replay_bp"] for c in cycles.values()),
    }


def _paper_trades(ledger: str) -> list[dict]:
    from cherrypick.weeklyput import db

    conn = db.connect_ro(ledger)
    try:
        return [
            p for p in db.positions(conn, "unhedged") if p["status"] == "closed" and p["net_bp"] is not None
        ]
    finally:
        conn.close()


def _open_store():
    """Item 57's chain-path store, or None on a machine without it (or without the backtest package,
    which locates it): every trade is then listed `no_chain_store`, never dropped."""
    try:
        if not i57.i46._chain_store_path().exists():
            return None
    except ImportError:
        return None
    return i57.i46._chain_store()


def track(ledger: str | None = None) -> dict:
    from cherrypick.weeklyput import db

    ledger = db.resolve_path(ledger)
    trades = _paper_trades(ledger)
    rows = []
    store = _open_store()
    try:
        for p in trades:
            name = STORE_NAME.get(p["symbol"])
            expiry = date.fromisoformat(p["expiration"])
            got = i57._sessions_for(store, name, expiry) if (store is not None and name) else None
            if got is None:
                rows.append(compare(p, {"drop": "no_path" if store is not None else "no_chain_store"}))
                continue
            sessions, fwds = got
            pair = i57.trade_pair(name, sessions, fwds, expiry, i57.LEG)
            rows.append(compare(p, pair.get("unhedged", pair)))
    finally:
        if store is not None:
            store.close()
    return {
        "item": "item69",
        "step": "track",
        "judged": False,
        "ledger": ledger,
        "summary": summarise(rows),
        "trades": rows,
        "corrections": REPLAY_CORRECTIONS,
    }


def _out_dir() -> Path:
    from cherrypick.core import home

    d = home.data_dir("backtest") / "results" / "research" / "item69"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_track(args) -> int:
    doc = track(args.ledger)
    path = Path(args.out) if args.out else _out_dir() / "tracking.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    s = doc["summary"]
    print(json.dumps({"written": str(path), "trades": s["trades"], "replayed": s["replayed"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("track")
    p.add_argument("--ledger", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_track)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
