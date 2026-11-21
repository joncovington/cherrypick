"""A module arm's backtest reference (`armref`, `read arm-reading`): the stored run whose arm has the
paper arm's settings, whatever either is called, and the paper record per session against it.
Synthetic config, runs and paper only."""

from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from cherrypick.backtest import armref, paths, runner

CONFIG = {
    "base": {"wing": 10, "_note": "a comment"},
    "registry": {"profiles": {"control": {}, "pm1400-n1": {"entries": 1}, "bp-10k": {"bp": 10000}}},
}


def _config(monkeypatch) -> None:
    """The module's current config is the synthetic one (the runner's module record is frozen)."""
    mod = dataclasses.replace(runner.MODULES["meic"], load=lambda: (CONFIG, {}))
    monkeypatch.setitem(runner.MODULES, "meic", mod)


def _spec(arm: str, **params) -> dict:
    return {
        "module": "meic",
        "arm": arm,
        "params": {"wing": 10, **params},
        "cadence": 60,
        "fill_model": runner.MEIC_FILL_MODEL,
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "results_dir", lambda: tmp_path)
    _config(monkeypatch)
    conn = sqlite3.connect(tmp_path / "backtest.db")
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, created_at REAL, module TEXT, arms TEXT, "
        "window_name TEXT, start TEXT, end TEXT, cadence INTEGER, code_commit TEXT, data_fingerprint TEXT, "
        "fill_model TEXT, status TEXT, sessions INTEGER, refused TEXT, report TEXT)"
    )
    conn.execute(
        "CREATE TABLE session_results (run_id TEXT, arm TEXT, spec_hash TEXT, session TEXT, net REAL, "
        "gross REAL, fees REAL, trades INTEGER)"
    )

    def run(run_id, created, specs, sessions):
        report = {"arms": {name: {"spec": spec} for name, spec in specs.items()}}
        conn.execute(
            "INSERT INTO runs (run_id, created_at, module, arms, window_name, start, end, status, report) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                created,
                "meic",
                json.dumps(list(specs)),
                "design",
                "2022-05-16",
                "2025-06-30",
                "done",
                json.dumps(report),
            ),
        )
        for name in specs:
            for i in range(sessions):
                conn.execute(
                    "INSERT INTO session_results (run_id, arm, spec_hash, session, net) VALUES (?,?,?,?,?)",
                    (run_id, name, "h", f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", 10.0 + (i % 7) * 5 - 15),
                )

    # The study named the arm n1; its settings are the paper arm pm1400-n1's. The shorter run is stored
    # first, so only the ordering puts the longer one ahead.
    run("r-long", 2.0, {"control": _spec("control"), "n1": _spec("n1", entries=1)}, 60)
    run("r-short", 1.0, {"n1": _spec("n1", entries=1)}, 40)
    # The same name with other settings is no match.
    run("r-other", 3.0, {"pm1400-n1": _spec("pm1400-n1", entries=3)}, 90)
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        armref,
        "paper_sessions",
        lambda module, arm, session=None: (
            [{"session": f"2026-11-{3 + i:02d}", "net": 5.0} for i in range(6)],
            "2026-11-03",
        ),
    )


def test_a_spec_matches_by_its_settings_whatever_the_arm_is_called():
    assert armref.settings_key(_spec("n1", entries=1)) == armref.settings_key(_spec("pm1400-n1", entries=1))
    assert armref.settings_key(_spec("n1", entries=1)) != armref.settings_key(_spec("n1", entries=3))


def test_the_run_with_the_arms_settings_answers_the_longest_first(store):
    out = armref.arm_reading("meic", "pm1400-n1", resamples=100)
    assert [(m["run"], m["run_arm"]) for m in out["matches"]] == [("r-long", "n1"), ("r-short", "n1")]
    assert out["chosen"]["run"] == "r-long" and out["unit"] == "session" and out["licensed"] is True
    r = out["reading"]
    assert r["reference"]["trades"] == 60 and r["paper"]["trades"] == 6
    assert r["state"] in ("inside", "below", "above")
    assert out["paper_from"] == "2026-11-03"


def test_an_arm_no_run_has_tried_has_no_reference(store):
    out = armref.arm_reading("meic", "bp-10k")
    assert out["ok"] and out["matches"] == [] and out["chosen"] is None and out["reading"] is None


def test_an_arm_the_module_does_not_declare_is_refused(store):
    assert armref.arm_reading("meic", "nope")["ok"] is False
    assert armref.arm_reading("flies", "control")["ok"] is False


def test_no_store_on_this_machine_reads_as_no_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "results_dir", lambda: tmp_path / "none")
    _config(monkeypatch)
    out = armref.arm_reading("meic", "control")
    assert out["ok"] and out["reading"] is None and out["matches"] == []
