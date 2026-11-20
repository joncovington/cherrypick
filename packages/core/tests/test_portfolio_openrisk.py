"""core.portfolio.openrisk: max loss held at once across arms.

Guards, each broken on purpose while this was written: overlapping trades add and sequential ones
do not (a close at the instant of an open comes first); a position held overnight counts from the
next session's open; a trade with no max loss is named, never counted as zero, and makes the
reading a floor; a backtest trade settled at expiry is held to the close.
"""

from __future__ import annotations

from cherrypick.core.portfolio import openrisk as O


def bt(session, entry_minute, exit_minute, risk, exit_session=None):
    return O.backtest_interval(
        {"session": session, "entry_minute": entry_minute, "exit_session": exit_session or session,
         "exit_minute": exit_minute, "risk": risk}
    )  # fmt: skip


def test_overlapping_trades_add_and_a_close_at_an_open_comes_first():
    a = [bt("2026-09-14", 600, 840, 500.0)]  # 10:00-14:00
    b = [bt("2026-09-14", 780, 900, 300.0)]  # 13:00-15:00: overlaps a for an hour
    c = [bt("2026-09-14", 840, 870, 1000.0)]  # opens the minute a closes
    out = O.open_risk({"a": a, "b": b, "c": c})
    [day] = out["worst_sessions"]
    assert day["peak"] == 1300.0  # b + c; a had closed
    assert day["by_arm"] == {"b": 300.0, "c": 1000.0}
    assert out["peaks"]["max"] == 1300.0 and out["floor"] is False


def test_a_position_held_overnight_counts_from_the_next_open():
    multi = [bt("2026-09-14", 900, 600, 800.0, exit_session="2026-09-15")]
    late = [bt("2026-09-15", 590, 620, 100.0)]
    out = O.open_risk({"m": multi, "l": late})
    by = {d["session"]: d for d in out["worst_sessions"]}
    assert by["2026-09-15"]["peak"] == 900.0
    assert out["sessions"] == 2


def test_a_weekend_carries_into_monday():
    out = O.open_risk({"w": [bt("2026-09-18", 900, 600, 400.0, exit_session="2026-09-21")]})
    assert [d["session"] for d in out["worst_sessions"]] == ["2026-09-18", "2026-09-21"]


def test_a_trade_with_no_max_loss_is_named_not_zero():
    out = O.open_risk({"a": [bt("2026-09-14", 600, 700, 200.0)], "f": [bt("2026-09-14", 600, 700, None)]})
    assert out["unmeasured"] == {"f": 1} and out["measured"] == {"a": 1}
    assert out["floor"] is True and out["peaks"]["max"] == 200.0


def test_a_settled_backtest_trade_is_held_to_the_close():
    iv = bt("2026-09-14", 600, None, 100.0)
    assert iv["end"].hour == 16 and iv["end"].minute == 0


def test_a_ledger_record_reads_its_times_and_capital():
    iv = O.ledger_interval(
        {
            "entry_time": "2026-11-17 15:26:44-05:00",
            "exit_time": "2026-11-17T16:00:57-05:00",
            "session": "2026-11-17",
            "capital": 412.5,
        }
    )
    assert (iv["start"].hour, iv["end"].hour, iv["risk"], iv["whole_session"]) == (15, 16, 412.5, False)
    bare = O.ledger_interval({"session": "2026-11-17", "entry_session": "2026-11-16", "capital": 10})
    assert bare["whole_session"] and bare["start"].hour == 9 and bare["end"].day == 17


def test_the_cli_reads_open_risk_from_backtest_trades(tmp_path):
    import json
    from argparse import Namespace

    from cherrypick.core.portfolio import __main__ as cli

    day = "2026-09-14"
    a = {"arm": "a", "session": day, "entry_minute": 600, "exit_session": day, "exit_minute": 700}
    b = {"arm": "b", "session": day, "entry_minute": 650, "exit_session": day, "exit_minute": 720}
    trades = [{**a, "risk": 400.0, "net": {"verdict": 10.0}}, {**b, "net": {"verdict": -5.0}}]  # b: no risk
    doc = {"name": "builder-x", "arms": {"a": {}, "b": {}}, "sessions": [day, day], "trades": trades}
    path = tmp_path / "builder-x.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    manifest = [{"key": k, "kind": "backtest", "path": str(path), "arm": k} for k in ("a", "b")]
    args = Namespace(
        manifest=json.dumps(manifest), start=day, end=None, period="day", weights="booked", account=20_000.0
    )
    out = cli.cmd_read(args)["open_risk"]
    assert out["peaks"]["max"] == 400.0 and out["of_account"]["max"] == 0.02
    assert out["unmeasured"] == {"b": 1} and out["floor"] is True


def test_shares_of_the_account_and_the_distribution():
    rows = {"a": [bt(f"2026-09-{d:02d}", 600, 700, r) for d, r in [(14, 100.0), (15, 300.0), (16, 200.0)]]}
    out = O.open_risk(rows, account=10_000)
    assert out["peaks"] == {"median": 200.0, "p95": 300.0, "max": 300.0}
    assert out["of_account"]["max"] == 0.03
