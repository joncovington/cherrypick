"""Stable trade IDs (tradeids.py): assigned once from a trade's opening, kept through rolls by its orders
and through a lost history by its legs, merged never split, and the same on every re-run of a snapshot.
Synthetic names, orders and dates."""

import json
from types import SimpleNamespace

from test_pmcc import evaluate, legs, snapshot
from test_pmcc import history as pmcc_history

from cherrypick.manage import __main__ as cli
from cherrypick.manage import tradeids

LEGS = [{"symbol": "XYZ   261120C00055000"}, {"symbol": "XYZ   261120C00050000"}]


def chain(*orders):
    """A chain of (order id, day) pairs, oldest first."""
    return {"orders": [{"order_id": o, "executed_at": f"{d}T15:00:00Z"} for o, d in orders]}


def store(**ids):
    return {"ids": ids}


def test_a_new_trade_is_named_by_its_name_and_opening_day():
    a = tradeids.Assigner(None)
    first = a.assign("XYZ", LEGS, chain((11, "2026-04-01")))
    second = a.assign("XYZ", [{"symbol": "XYZ   261120P00040000"}], chain((12, "2026-04-01")))
    assert first["trade_id"] == "XYZ-260401-1" and not first["provisional"]
    assert second["trade_id"] == "XYZ-260401-2"  # opened the same day on the same name
    assert tradeids.is_trade_id(first["trade_id"])


def test_a_roll_keeps_the_id_and_a_new_trade_on_the_name_does_not_move_it():
    known = store(
        **{"XYZ-260401-1": {"underlying": "XYZ", "opened": "2026-04-01", "orders": ["11"], "legs": []}}
    )
    a = tradeids.Assigner(known)
    newer = a.assign("XYZ", [{"symbol": "XYZ   261218P00040000"}], chain((20, "2026-03-01")))
    rolled = a.assign("XYZ", LEGS, chain((11, "2026-04-01"), (15, "2026-06-01")))
    assert rolled["trade_id"] == "XYZ-260401-1" and rolled["orders"] == ["11", "15"]
    assert newer["trade_id"] == "XYZ-260301-1"  # its own ID, wherever it falls in the list


def test_a_trade_whose_history_is_lost_keeps_its_id_by_its_legs():
    known = store(
        **{
            "XYZ-250101-1": {
                "underlying": "XYZ",
                "opened": "2025-01-01",
                "orders": ["3"],
                "legs": sorted(" ".join(x["symbol"].split()) for x in LEGS),
            }
        }
    )
    kept = tradeids.Assigner(known).assign("XYZ", LEGS, None)  # no chain: its opening left the window
    assert kept["trade_id"] == "XYZ-250101-1" and not kept["provisional"]
    other = tradeids.Assigner(known).assign("XYZ", LEGS[:1], None)
    assert other["provisional"] and other["trade_id"].startswith("XYZ-open-")


def test_a_join_keeps_the_older_id_and_marks_the_other_merged():
    known = store(
        **{
            "XYZ-260401-1": {"underlying": "XYZ", "opened": "2026-04-01", "orders": ["11"], "legs": []},
            "XYZ-260925-1": {"underlying": "XYZ", "opened": "2026-09-25", "orders": ["12"], "legs": []},
        }
    )
    ident = tradeids.Assigner(known).assign("XYZ", LEGS, chain((11, "2026-04-01"), (12, "2026-09-25")))
    assert ident["trade_id"] == "XYZ-260401-1" and ident["merged"] == ["XYZ-260925-1"]
    after = tradeids.updated(known, [{"trade_id": "XYZ-260401-1", "trade_ident": ident}], "2026-10-13")
    assert after["ids"]["XYZ-260925-1"]["merged_into"] == "XYZ-260401-1"
    assert after["ids"]["XYZ-260401-1"]["orders"] == ["11", "12"]


def test_the_store_only_grows_and_a_damaged_one_is_left_alone(tmp_path):
    path = tmp_path / "trade-ids.json"
    ident = tradeids.Assigner(None).assign("XYZ", LEGS, chain((11, "2026-04-01")))
    assert tradeids.record(
        path, {"ids": {}}, [{"trade_id": ident["trade_id"], "trade_ident": ident}], "2026-04-01"
    )
    first = json.loads(path.read_text(encoding="utf-8"))["ids"]
    assert first["XYZ-260401-1"]["first_seen"] == "2026-04-01"
    # A later run that no longer sees the trade removes nothing.
    assert tradeids.record(path, {"ids": first}, [], "2026-04-02")
    assert json.loads(path.read_text(encoding="utf-8"))["ids"] == first
    path.write_text("{not json", encoding="utf-8")
    assert not tradeids.record(
        path, {"ids": {}}, [{"trade_id": ident["trade_id"], "trade_ident": ident}], "x"
    )
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_trades_own_override_is_read_over_the_names():
    snap = snapshot(pmcc_history(), legs())
    over = {
        "positions": {"XYZ": {"note": "the name"}},
        "trades": {"XYZ-260401-1": {"intent": "hedge", "note": "this trade"}},
    }
    (t,) = evaluate(snap, over)["trades"]
    assert t["trade_id"] == "XYZ-260401-1" and t["intent"] == "hedge" and t["note"] == "this trade"
    (t,) = evaluate(snap, {"positions": {"XYZ": {"note": "the name"}}})["trades"]
    assert t["note"] == "the name" and t["intent"] is None


def test_the_cli_records_ids_once_and_a_rerun_of_the_snapshot_gives_the_same(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli._stream_request, "register", lambda result: None)
    path = tmp_path / "in.json"
    snap = snapshot(pmcc_history(), legs())
    path.write_text(json.dumps(snap), encoding="utf-8")
    cli.cmd_evaluate(SimpleNamespace(snapshot=str(path), rules=None))
    stored = json.loads((tmp_path / "trade-ids.json").read_text(encoding="utf-8"))["ids"]
    assert list(stored) == ["XYZ-260401-1"]
    session = snap["session"]

    def ids_now():
        doc = json.loads((tmp_path / f"eval-{session}.json").read_text(encoding="utf-8"))
        return [t["trade_id"] for a in doc["accounts"] for t in a["trades"]]

    assert ids_now() == ["XYZ-260401-1"]
    (tmp_path / "trade-ids.json").unlink()  # the stored snapshot carries what it needs
    cli.cmd_reevaluate(SimpleNamespace(session=session, rules=None))
    assert ids_now() == ["XYZ-260401-1"]
    assert not (tmp_path / "trade-ids.json").exists()  # a re-run writes no IDs
