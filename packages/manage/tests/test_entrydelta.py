"""The delta at entry recorded from the first full snapshot after a call is bought (entrydelta.py), and
the PMCC view preferring it to the close-based solve. Synthetic names, prices and times."""

import json
from types import SimpleNamespace

import pytest
from test_pmcc import HIST, LONG, SESSION, SHORT, evaluate, fill, legs, snapshot, view_of
from test_wheel import tx

from cherrypick.manage import __main__ as cli
from cherrypick.manage import entrydelta, pmcc
from cherrypick.manage import rules as _rules

RULES = _rules.effective(None)[0]
FILLED = f"{SESSION}T19:49:00Z"
PRICE = fill(40, 0.30, day=SESSION)  # about 0.85 delta with XYZ at 50
LONG_ID = f"1-{LONG}-Buy to Open"


def bought_today(price=PRICE):
    """The long bought this session, a short sold against it in the same order."""
    return [
        tx(1, FILLED, "Buy to Open", LONG, 1, price),
        tx(1, FILLED, "Sell to Open", SHORT, 1, 1.20),
    ]


def snap_at(generated_at, rows=None, spot=50.0, extra=None):
    """A full snapshot taken at `generated_at`, with no close yet for the session."""
    base = {"generated_at": generated_at, "broker_greeks": {LONG: {"delta": 0.86}}}
    return snapshot(
        rows or bought_today(), legs(long_price=PRICE), spot=spot, hist=HIST, extra={**base, **(extra or {})}
    )


def test_a_call_bought_today_is_recorded_from_the_snapshot_after_its_fill():
    snap = snap_at(f"{SESSION}T13:50:00-0600")  # 19:50Z, a minute after the fill
    recs = entrydelta.new_records(snap, RULES, set())
    assert list(recs) == [LONG_ID]  # the call bought; never the call sold
    rec = recs[LONG_ID]
    assert rec["gap_seconds"] == 60.0 and rec["spot"] == 50.0 and rec["price"] == PRICE
    assert 0.80 < rec["delta"] < 0.90
    assert rec["delta_low"] < rec["delta"] < rec["delta_high"]
    assert rec["delta_high"] - rec["delta_low"] < 0.02  # a minute's move barely moves a deep call
    assert rec["broker_delta"] == 0.86  # kept beside as a check, never used


def test_the_record_decides_a_pmcc_the_close_could_not_yet():
    snap = snap_at(f"{SESSION}T13:50:00-0600")
    v = view_of(evaluate(snap))
    assert v["recognition"] == "unknown" and v["long"]["delta_at_entry"] is None  # no close for today

    snap["entry_deltas"] = entrydelta.new_records(snap, RULES, set())
    v = view_of(evaluate(snap))
    assert v["recognition"] == "pmcc" and v["recognized"]
    assert v["recognized_by"].startswith(
        f"entered at {v['long']['delta_at_entry']:.2f} delta (at least 0.70)"
    )
    (f,) = v["long"]["entry_fills"]
    assert f["basis"] == "recorded" and f["gap_seconds"] == 60.0 and f["close"] is None


@pytest.mark.parametrize(
    "generated_at, why",
    [
        (f"{SESSION}T13:56:00-0600", "over five minutes after the fill"),
        (f"{SESSION}T12:40:00-0600", "before the fill"),
        (f"{SESSION}T13:50:00", "a time with no zone"),
    ],
)
def test_a_snapshot_too_late_or_too_early_records_nothing(generated_at, why):
    assert entrydelta.new_records(snap_at(generated_at), RULES, set()) == {}, why


def test_only_the_sessions_own_fills_are_recorded():
    rows = [tx(1, "2026-10-09T19:49:00Z", "Buy to Open", LONG, 1, PRICE)]
    assert entrydelta.new_records(snap_at(f"{SESSION}T13:50:00-0600", rows), RULES, set()) == {}


def test_no_underlying_price_records_nothing():
    snap = snap_at(f"{SESSION}T13:50:00-0600")
    snap["metrics"]["XYZ"]["last"] = None
    assert entrydelta.new_records(snap, RULES, set()) == {}


def test_a_band_across_the_floor_is_said():
    entry = {
        "delta": 0.71,
        "fills": [
            {"basis": "recorded", "delta": 0.71, "delta_low": 0.69, "delta_high": 0.73, "gap_seconds": 240.0}
        ],
    }
    assert (
        pmcc.entry_band(entry, 0.70)
        == "close to the floor: 0.69-0.73 at the underlying's likely move over the 240 s to the snapshot"
    )
    entry["fills"][0].update(delta_low=0.705, delta_high=0.715)
    assert pmcc.entry_band(entry, 0.70) is None
    # A close-based delta has no band.
    assert pmcc.entry_band({"delta": 0.71, "fills": [{"basis": "close", "delta": 0.71}]}, 0.70) is None


def test_the_store_is_written_once_and_never_rewritten(tmp_path):
    path = tmp_path / "entry-deltas.json"
    snap = snap_at(f"{SESSION}T13:50:00-0600")
    first = entrydelta.record(snap, RULES, path)
    assert list(first) == [LONG_ID]
    # A later snapshot at another price carries the stored record, not a new solve.
    later = snap_at(f"{SESSION}T13:51:00-0600", spot=52.0)
    again = entrydelta.record(later, RULES, path)
    assert again == first
    assert json.loads(path.read_text(encoding="utf-8"))["records"] == first


def test_a_damaged_store_is_left_alone(tmp_path):
    path = tmp_path / "entry-deltas.json"
    path.write_text("{not json", encoding="utf-8")
    out = entrydelta.record(snap_at(f"{SESSION}T13:50:00-0600"), RULES, path)
    assert list(out) == [LONG_ID]  # this snapshot still carries what it could solve
    assert path.read_text(encoding="utf-8") == "{not json"


def test_evaluate_records_once_and_the_stored_snapshot_carries_it(tmp_path, monkeypatch):
    """The CLI's full-snapshot path records; the stored snapshot carries the record, so a re-run of it
    reads the same delta even after the store is gone."""
    monkeypatch.setenv("MANAGE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli._stream_request, "register", lambda result: None)
    path = tmp_path / "in.json"
    path.write_text(json.dumps(snap_at(f"{SESSION}T13:50:00-0600")), encoding="utf-8")
    cli.cmd_evaluate(SimpleNamespace(snapshot=str(path), rules=None))
    stored = json.loads((tmp_path / f"snapshot-{SESSION}.json").read_text(encoding="utf-8"))
    assert list(stored["entry_deltas"]) == [LONG_ID]
    assert (
        json.loads((tmp_path / "entry-deltas.json").read_text(encoding="utf-8"))["records"]
        == stored["entry_deltas"]
    )
    (tmp_path / "entry-deltas.json").unlink()
    cli.cmd_reevaluate(SimpleNamespace(session=SESSION, rules=None))
    doc = json.loads((tmp_path / f"eval-{SESSION}.json").read_text(encoding="utf-8"))
    (v,) = doc["accounts"][0]["pmcc"]["positions"]
    assert v["recognition"] == "pmcc" and v["long"]["entry_fills"][0]["basis"] == "recorded"
    assert not (tmp_path / "entry-deltas.json").exists()  # a re-run records nothing


def test_recording_can_be_turned_off():
    rules = _rules.effective({"pmcc": {"entry_record": {"enabled": False}}})[0]
    assert entrydelta.new_records(snap_at(f"{SESSION}T13:50:00-0600"), rules, set()) == {}
