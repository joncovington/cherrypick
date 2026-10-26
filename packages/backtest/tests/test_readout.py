"""The console's read surface: what `read overview` and `read run` carry, and that a bad run id is
refused rather than opened."""

from __future__ import annotations

import json

from test_fetch import DAY, FakeClient, _rows, _settings

from cherrypick.backtest import __main__ as cli
from cherrypick.backtest import fetch, paths, readout


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
