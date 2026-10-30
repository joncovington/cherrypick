"""The console's read surface: what `read overview` and `read run` carry, and that a bad run id is
refused rather than opened."""

from __future__ import annotations

import json

from test_fetch import DAY, FakeClient, _rows, _settings

from cherrypick.backtest import __main__ as cli
from cherrypick.backtest import fetch, paths, readout, results


def test_the_overview_lists_each_cadence_store_and_the_licence(capsys):
    s = _settings()
    fetch.fetch(s, FakeClient({DAY: _rows(DAY)}), "SPX", DAY, DAY, log=lambda _m: None)
    out = readout.overview(s)
    assert out["ok"] and out["license"]["active"] is True
    (st,) = out["stores"]
    assert (st["cadence"], st["complete"], st["span"]) == (15, 1, [DAY, DAY])
    assert out["runs"] == [] and out["validation"] == []


def test_the_latest_validation_per_store_is_reported(capsys):
    s = _settings()
    vdir = paths.results_dir() / "validation"
    vdir.mkdir(parents=True)
    vendor_db = str(paths.vendor_dir(s) / "SPX-60s.db")
    doc = {
        "vendor_db": vendor_db,
        "dates": ["d"],
        "decisions": {"unlocks_history": True, "sessions_compared": 11},
    }
    (vdir / "SPX-a.json").write_text(
        json.dumps({**doc, "decisions": {"unlocks_history": False}}), encoding="utf-8"
    )
    (vdir / "SPX-b.json").write_text(json.dumps(doc), encoding="utf-8")
    import os
    import time

    os.utime(vdir / "SPX-a.json", (time.time() - 100, time.time() - 100))
    (v,) = readout.overview(s)["validation"]
    assert v["cadence"] == 60 and v["unlocks_history"] is True and v["sessions_compared"] == 11


def test_a_run_report_is_read_by_id_and_a_bad_id_is_refused(capsys):
    runs = paths.results_dir() / "runs"
    runs.mkdir(parents=True)
    (runs / "20261017-120000-abcdef.json").write_text(json.dumps({"run_id": "x"}), encoding="utf-8")
    out = readout.run_report("20261017-120000-abcdef")
    assert out["ok"] and out["report"] == {"run_id": "x"}
    assert readout.run_report("../../etc/passwd")["ok"] is False
    assert cli.main(["read", "run", "--id", "nope"]) == 1


def test_read_stores_names_every_declared_store_and_where_they_live(capsys):
    from cherrypick.backtest import stores

    s = _settings()
    out = readout.store_locations(s)
    assert out["ok"] and out["dir"] == str(paths.vendor_dir(s))
    assert out["files"] == [e["file"] for e in stores.STORES]


def test_provisional_looks_count_distinct_specs_without_recording_a_trial(capsys):
    # A provisional run records no trial, so after many looks the trial count still reads zero.
    # The overview says how many distinct specs those looks ran, per module; a done run is not a look.
    s = _settings()
    conn = results.connect()
    for run_id, status, module in (
        ("p1", "provisional", "meic"),
        ("p2", "provisional", "meic"),
        ("d1", "done", "flies"),
    ):
        conn.execute(
            "INSERT INTO runs (run_id, created_at, module, arms, window_name, status, refused) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, 1.0, module, "[]", "design", status, "{}"),
        )
    rows = [
        ("p1", "control", "h-control"),
        ("p1", "d20", "h-d20"),
        ("p2", "control", "h-control"),
        ("p2", "d30", "h-d30"),
        ("d1", "control", "h-fc"),
    ]
    for run_id, arm, h in rows:
        for day in ("2026-01-02", "2026-01-05"):
            conn.execute(
                "INSERT INTO session_results (run_id, arm, spec_hash, session, net) VALUES (?,?,?,?,?)",
                (run_id, arm, h, day, 1.0),
            )
    conn.commit()
    conn.close()
    out = readout.overview(s)
    assert out["provisional_looks"] == {"meic": 3}
    assert out["trials"] == {}
