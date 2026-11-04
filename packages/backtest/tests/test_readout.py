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


def test_thin_keeps_the_ends_and_short_lines_whole():
    assert readout.thin([1.0, 2.0], 48) == [1.0, 2.0]
    line = [float(i) for i in range(1000)]
    out = readout.thin(line, 48)
    assert len(out) == 48 and out[0] == 0.0 and out[-1] == 999.0
    assert out == sorted(out)


def test_summaries_give_the_reports_figures_and_each_arms_running_net(capsys):
    _settings()
    rid = "20261017-120000-abcdef"
    runs = paths.results_dir() / "runs"
    runs.mkdir(parents=True)
    report = {
        "run_id": rid,
        "module": "meic",
        "window": "design",
        "provisional": True,
        "span": ["2026-01-02", "2026-01-06"],
        "arms": {
            "control": {"summary": {"mean": 5.0, "sessions": 3}, "trades": 900},
            "d10": {"summary": {"mean": -1.25, "sessions": 3}, "trades": 3},
        },
    }
    (runs / f"{rid}.json").write_text(json.dumps(report), encoding="utf-8")
    conn = results.connect()
    for arm, nets in (("control", (4.0, 6.0, 5.0)), ("d10", (-2.0, 1.0, -2.75))):
        for day, net in zip(("2026-01-02", "2026-01-05", "2026-01-06"), nets, strict=True):
            conn.execute(
                "INSERT INTO session_results (run_id, arm, spec_hash, session, net) VALUES (?,?,?,?,?)",
                (rid, arm, "h", day, net),
            )
    conn.commit()
    conn.close()
    out = readout.run_summaries([rid, "../../etc/passwd", "20261017-120000-000000", rid])
    assert out["missing"] == ["../../etc/passwd", "20261017-120000-000000"]
    run = out["runs"][rid]
    assert (run["module"], run["window"], run["provisional"]) == ("meic", "design", True)
    # The report's own figures, never recomputed from the path.
    assert run["arms"]["d10"] == {"mean": -1.25, "sessions": 3, "trades": 3, "path": [-2.0, -1.0, -3.75]}
    assert run["arms"]["control"]["path"] == [4.0, 10.0, 15.0]
    assert cli.main(["read", "summaries", "--ids", rid]) == 0
    assert json.loads(capsys.readouterr().out)["runs"][rid]["arms"]["control"]["trades"] == 900
