"""One trade's path (tradepath.py): each side marked at mid until its own exit, then held at its exit
price; a missing quote a gap, never a neighbour's value; read from the run's ledger and the store it
read. Synthetic sessions only, per the licence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from cherrypick.core import clock

from cherrypick.backtest import paths, store, tradepath

DAY = "2024-01-02"
RUN = "20261031-120000-abcdef"
POS = "PAPER-d16-SPX-1"


def _ts(hhmm: str) -> float:
    h, m = hhmm.split(":")
    return datetime(2024, 1, 2, int(h), int(m), tzinfo=clock.ET).timestamp()


def _stamp(hhmm: str) -> str:
    return datetime(2024, 1, 2, *map(int, hhmm.split(":")), tzinfo=clock.ET).isoformat()


TRADE = {
    "entry_time": _stamp("14:00"),
    "exit_time": _stamp("16:00"),
    "put_strike": 4700.0,
    "call_strike": 4800.0,
    "wing_width": 10.0,
    "put_wing": None,
    "call_wing": None,
    "put_credit": 1.0,
    "call_credit": 1.2,
    "quantity": 1,
    "dollar_multiplier": 100.0,
}
SIDES = [
    {"side": "call", "exit_time": _stamp("14:02"), "exit_price": 2.4, "exit_reason": "per_side_stop"},
    {"side": "put", "exit_time": _stamp("16:00"), "exit_price": 0.0, "exit_reason": "expired_settlement"},
]


def _snap(hhmm: str, spot: float | None, put: tuple[float, float] | None, call: tuple[float, float]) -> dict:
    puts = {} if put is None else {4700.0: {"mid": put[0]}, 4690.0: {"mid": put[1]}}
    return {
        "ts": _ts(hhmm),
        "underlying_price": spot,
        "puts": puts,
        "calls": {4800.0: {"mid": call[0]}, 4810.0: {"mid": call[1]}},
    }


SAMPLES = [
    _snap("13:59", 4750, (1.5, 0.5), (1.6, 0.4)),  # before entry: left out
    _snap("14:00", 4750, (1.5, 0.5), (1.6, 0.4)),
    _snap("14:01", 4790, None, (3.0, 1.0)),  # the put legs are not in the store this sample
    _snap("14:03", 4795, (0.3, 0.1), (5.0, 1.0)),  # the call side is closed: its exit price holds
]


def test_each_side_is_marked_at_mid_until_its_exit_and_held_at_its_exit_price_after():
    out = tradepath.path(TRADE, SIDES, SAMPLES, vix=[(_ts("13:00"), 14.0), (_ts("14:02"), 15.0)])
    pts = out["points"]
    assert [p["time"] for p in pts] == ["14:00", "14:01", "14:03"]
    assert (pts[0]["put_mark"], pts[0]["call_mark"]) == (1.0, 1.2)
    assert pts[0]["pnl"] == 0.0  # sold at these marks
    assert pts[2]["call_mark"] == 2.4  # the stop's own price, not the later quote
    assert pts[2]["pnl"] == round((1.0 - 0.2) * 100 + (1.2 - 2.4) * 100, 2)
    assert [p["vix"] for p in pts] == [14.0, 14.0, 15.0]  # at or before each sample
    assert out["strikes"] == {"put": [4690.0, 4700.0], "call": [4800.0, 4810.0]}
    assert [(e["side"], e["reason"]) for e in out["exits"]] == [
        ("call", "per_side_stop"),
        ("put", "expired_settlement"),
    ]


def test_a_missing_leg_quote_is_a_gap_never_a_neighbours_value():
    pts = tradepath.path(TRADE, SIDES, SAMPLES)["points"]
    assert pts[1]["put_mark"] is None and pts[1]["pnl"] is None
    assert pts[1]["call_mark"] == 2.0 and pts[1]["spot"] == 4790
    assert tradepath.path(TRADE, SIDES, SAMPLES)["gaps"] == 1


def test_a_side_wing_overrides_the_scalar_width():
    t = {**TRADE, "put_wing": 5.0}
    assert tradepath.path(t, SIDES, [])["strikes"]["put"] == [4695.0, 4700.0]


def _run(module: str = "meic") -> None:
    runs = paths.results_dir() / "runs"
    (runs / RUN).mkdir(parents=True)
    report = {"run_id": RUN, "module": module, "arms": {"d16": {}}, "cadence": 60, "store": "SPX-60s.db"}
    (runs / f"{RUN}.json").write_text(json.dumps(report), encoding="utf-8")
    conn = sqlite3.connect(runs / RUN / f"{DAY}.db")
    cols = ", ".join(f"{k}" for k in ("ic_order_id", "arm", *TRADE))
    conn.execute(f"CREATE TABLE ic_trades ({cols})")
    conn.execute(
        f"INSERT INTO ic_trades VALUES ({', '.join('?' * (2 + len(TRADE)))})", (POS, "d16", *TRADE.values())
    )
    conn.execute("CREATE TABLE ic_spread_legs (ic_order_id, side, exit_time, exit_price, exit_reason)")
    conn.executemany(
        "INSERT INTO ic_spread_legs VALUES (?,?,?,?,?)",
        [(POS, s["side"], s["exit_time"], s["exit_price"], s["exit_reason"]) for s in SIDES],
    )
    conn.commit()
    conn.close()


def _store(snaps: list[dict]) -> None:
    db = tradepath._store_for({"store": "SPX-60s.db"})
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    for stmt in store.RECORDER_DDL:
        conn.execute(stmt)
    for i, s in enumerate(snaps, start=1):
        conn.execute("INSERT INTO gex_quote_samples VALUES (?,?,?,?,?)", (i, "SPX", DAY, s["ts"], DAY))
        conn.execute(
            "INSERT INTO gex_spot_history VALUES (?,?,?,?)", ("SPX", DAY, s["ts"], s["underlying_price"])
        )
        for side, chain in ((store.SIDES["PUT"], s["puts"]), (store.SIDES["CALL"], s["calls"])):
            for strike, q in chain.items():
                conn.execute(
                    "INSERT INTO gex_quotes (sample_id, side, strike, bid, ask, mid) VALUES (?,?,?,?,?,?)",
                    (i, side, strike, q["mid"], q["mid"], q["mid"]),
                )
    conn.commit()
    conn.close()


def test_a_trade_is_read_from_the_runs_ledger_and_the_store_its_report_names():
    _run()
    _store(SAMPLES)
    out = tradepath.for_trade(RUN, "d16", DAY, POS)
    assert out["ok"] and out["store"] == "SPX-60s.db"
    assert [p["time"] for p in out["points"]] == ["14:00", "14:01", "14:03"]
    assert out["points"][2]["call_mark"] == 2.4


def test_what_cannot_be_read_is_refused_in_words():
    assert tradepath.for_trade(RUN, "d16", DAY, POS) == {"ok": False, "error": f"no run {RUN!r}"}
    _run()
    assert "no arm" in tradepath.for_trade(RUN, "other", DAY, POS)["error"]
    assert "no ledger" in tradepath.for_trade(RUN, "d16", "2024-01-03", POS)["error"]
    assert "no ledger" in tradepath.for_trade(RUN, "d16", "../x", POS)["error"]
    assert "no trade" in tradepath.for_trade(RUN, "d16", DAY, "nope")["error"]
    assert "holds no samples" in tradepath.for_trade(RUN, "d16", DAY, POS)["error"]


def test_another_modules_run_has_no_path():
    _run(module="flies")
    assert (
        tradepath.for_trade(RUN, "d16", DAY, POS)["error"] == "a trade's path is read for MEIC's condors only"
    )
