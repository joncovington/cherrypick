"""The runner's rules, with the replay and the ledger read faked: what may run when, that every design
evaluation is a trial counted once per spec, and that a holdout is spent once."""

from __future__ import annotations

import json

import pytest
from test_fetch import FakeClient, _rows

from cherrypick.backtest import fetch, paths, results, runner, settings, store, vixfetch

DAYS = ["2025-03-04", "2025-03-05", "2026-10-14", "2026-10-15"]


@pytest.fixture()
def world(managed_home):
    s = settings.resolve({"enabled": True})
    (managed_home / "config").mkdir(parents=True, exist_ok=True)
    flies = {
        "defaults": {"width": 5},
        "symbols": ["SPX"],
        "arms": {"control": {}, "range-high": {"gate": "x"}},
    }
    (managed_home / "config" / "flies.json").write_text(json.dumps(flies), encoding="utf-8")

    class Client(FakeClient):
        def index_closes(self, symbol, start, end):
            return {d: 6000.0 for d in DAYS if d != "2025-03-05"}  # one day with no close of its own

    fetch.fetch(s, Client({d: _rows(d) for d in DAYS}), "SPX", DAYS[0], DAYS[-1], log=lambda _m: None)
    # The day with no index close took the last-underlying fallback; remove it to test the refusal.
    conn = store.connect(s, "SPX")
    conn.execute("DELETE FROM daily_closes WHERE trade_date = '2025-03-05'")
    conn.commit()
    conn.close()
    return s, managed_home


REPLAYED_CONFIGS: list = []


def _replay(day, out, arms, history_db, settle, config):
    out.write_bytes(b"")
    REPLAYED_CONFIGS.append(json.loads(config.read_text(encoding="utf-8")))
    return {"ok": True}


def _read(path):
    day = path.stem
    return [
        {
            "arm": "control",
            "session": day,
            "net_pnl": 10.0 if day.endswith("4") else -5.0,
            "gross_pnl": 12.0,
            "cost": 2.0,
        },
        {"arm": "range-high", "session": day, "net_pnl": 4.0, "gross_pnl": 5.0, "cost": 1.0},
    ]


def _run(s, window, **kw):
    return runner.run(
        s, ["range-high"], window, replay=_replay, read_rows=_read, workers=1, log=lambda _m: None, **kw
    )


def test_an_arm_identical_to_control_is_flagged_no_exposure(world):
    s, _ = world

    def same(path):
        day = path.stem
        row = {"session": day, "net_pnl": 3.0, "gross_pnl": 4.0, "cost": 1.0}
        return [{**row, "arm": "control"}, {**row, "arm": "range-high"}]

    report = runner.run(s, ["range-high"], "validation", replay=_replay, read_rows=same, log=lambda _m: None)
    assert report["arms"]["range-high"]["no_exposure"] is True
    assert _run(s, "validation")["arms"]["range-high"]["no_exposure"] is False


def _unlock(s, ok=True):
    out = paths.results_dir() / "validation"
    out.mkdir(parents=True, exist_ok=True)
    doc = {"vendor_db": str(store.db_path(s, "SPX")), "decisions": {"unlocks_history": ok}}
    (out / "SPX-x.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_validation_window_always_runs_and_counts_no_trial(world):
    s, _ = world
    report = _run(s, "validation")
    assert report["arms"]["control"]["summary"]["sessions"] == 2  # 10-14, 10-15
    assert report["arms"]["range-high"]["vs_control"] is not None
    conn = results.connect()
    assert results.trial_count(conn, "flies") == 0


def test_history_is_locked_until_validation_unlocks_it(world):
    s, _ = world
    with pytest.raises(runner.RunRefused, match="locked"):
        _run(s, "design")
    _unlock(s, ok=False)
    with pytest.raises(runner.RunRefused, match="locked"):
        _run(s, "design")


def test_a_provisional_run_looks_early_and_counts_for_nothing(world):
    """While locked, the design window may be run provisionally: no trial, marked everywhere, and
    never the holdout or the advisor's specs."""
    s, _ = world
    report = _run(s, "design", provisional=True)
    assert report["provisional"] is True and "PROVISIONAL" in report["provisional_note"]
    conn = results.connect()
    assert results.trial_count(conn, "flies") == 0
    (status,) = conn.execute("SELECT status FROM runs").fetchone()
    assert status == "provisional"
    from cherrypick.backtest import readout

    assert readout.overview(s)["runs"][0]["provisional"] is True
    with pytest.raises(runner.RunRefused, match="provisional"):
        _run(s, "holdout", provisional=True, spend_holdout=True)
    with pytest.raises(runner.RunRefused, match="provisional"):
        _run(s, "design", provisional=True, extra_arms={"advised:x": {}})


def test_design_runs_are_trials_counted_once_per_spec(world):
    s, home = world
    _unlock(s)
    report = _run(s, "design")
    assert report["refused"] == {"2025-03-05": "no_settle_price"}
    assert report["trials_for_module"] == 2, "control and range-high"
    _run(s, "design")
    conn = results.connect()
    assert results.trial_count(conn, "flies") == 2, "the same spec again is the same trial"
    cfg = json.loads((home / "config" / "flies.json").read_text())
    cfg["arms"]["range-high"]["gate"] = "y"
    (home / "config" / "flies.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert _run(s, "design")["trials_for_module"] == 3, "a changed parameter is a new trial"


def test_the_holdout_needs_asking_for_and_is_spent_once(world):
    s, _ = world
    _unlock(s)
    with pytest.raises(runner.RunRefused, match="spend-holdout"):
        _run(s, "holdout")
    # The fixture's sessions sit before the holdout window, so it spends on an empty span; the rule
    # under test is the spending, not the span.
    _run(s, "holdout", spend_holdout=True)
    with pytest.raises(runner.RunRefused, match="already spent"):
        _run(s, "holdout", spend_holdout=True)


def test_an_unknown_arm_or_a_missing_config_is_refused(world):
    s, home = world
    with pytest.raises(runner.RunRefused, match="no arm"):
        runner.run(s, ["nope"], "validation", replay=_replay, read_rows=_read, log=lambda _m: None)
    (home / "config" / "flies.json").unlink()
    with pytest.raises(runner.RunRefused, match="no flies config"):
        _run(s, "validation")


# ---------------------------------------------------------------------------------------------- MEIC


@pytest.fixture()
def meic_world(world):
    s, home = world
    base = {"_note": "a comment", "stop_trigger_ratio": 0.95, "symbols": ["SPX"]}
    registry = {
        "profiles": {"control": {"per_side_stop_management": False}, "bp-10k": {"max_open_bp_dollars": 10000}}
    }
    (home / "config" / "meic.json").write_text(json.dumps(base), encoding="utf-8")
    (home / "config" / "meic.risk.json").write_text(json.dumps(registry), encoding="utf-8")
    return s, home


MEIC_CALLS: list = []


def _meic_replay(day, out, arms, history_db, settle, config):
    out.write_bytes(b"")
    MEIC_CALLS.append(
        {
            "arms": arms,
            "base": json.loads(config.read_text(encoding="utf-8")),
            "registry": json.loads((config.parent / "meic-risk.json").read_text(encoding="utf-8")),
        }
    )
    return {"ok": True}


def _meic_read(path):
    day = path.stem
    return [
        {"arm": "control", "session": day, "net_pnl": 8.0, "gross_pnl": 10.0, "cost": 2.0},
        {"arm": "bp-10k", "session": day, "net_pnl": 3.0, "gross_pnl": 4.0, "cost": 1.0},
    ]


def _meic_run(s, window, **kw):
    return runner.run(
        s, ["bp-10k"], window, module="meic", replay=_meic_replay, read_rows=_meic_read, workers=1,
        log=lambda _m: None, **kw,
    )  # fmt: skip


def _unlock_meic(s, ok=True):
    out = paths.results_dir() / "validation"
    out.mkdir(parents=True, exist_ok=True)
    doc = {"module": "meic", "vendor_db": str(store.db_path(s, "SPX")), "decisions": {"unlocks_history": ok}}
    (out / "meic-SPX-x.json").write_text(json.dumps(doc), encoding="utf-8")


def test_meic_runs_from_a_snapshot_of_both_its_config_files(meic_world):
    s, _ = meic_world
    MEIC_CALLS.clear()
    report = _meic_run(s, "validation")
    assert report["module"] == "meic" and report["fill_model"] == runner.MEIC_FILL_MODEL
    assert MEIC_CALLS and MEIC_CALLS[0]["arms"] == ["control", "bp-10k"]
    assert MEIC_CALLS[0]["base"]["stop_trigger_ratio"] == 0.95
    assert "bp-10k" in MEIC_CALLS[0]["registry"]["profiles"]
    assert report["arms"]["bp-10k"]["vs_control"] is not None and "flies_config" not in report


def test_each_module_unlocks_only_its_own_history(meic_world):
    """A flies validation report unlocks nothing for MEIC, and the reverse."""
    s, _ = meic_world
    _unlock(s, ok=True)
    with pytest.raises(runner.RunRefused, match="locked for meic"):
        _meic_run(s, "design")
    _unlock_meic(s, ok=True)
    _meic_run(s, "design")
    assert runner.unlocked(s, "SPX", 15, module="meic") and runner.unlocked(s, "SPX", 15, module="flies")
    _unlock(s, ok=False)
    assert runner.unlocked(s, "SPX", 15, module="meic") and not runner.unlocked(s, "SPX", 15, module="flies")


def test_a_meic_trial_is_keyed_on_the_merged_params_not_the_comments(meic_world):
    s, home = meic_world
    _unlock_meic(s)
    _meic_run(s, "design")
    conn = results.connect()
    assert results.trial_count(conn, "meic") == 2 and results.trial_count(conn, "flies") == 0
    base = json.loads((home / "config" / "meic.json").read_text(encoding="utf-8"))
    (home / "config" / "meic.json").write_text(json.dumps({**base, "_note": "reworded"}), encoding="utf-8")
    _meic_run(s, "design")
    assert results.trial_count(conn, "meic") == 2  # a comment is not a parameter
    (home / "config" / "meic.json").write_text(
        json.dumps({**base, "stop_trigger_ratio": 2.0}), encoding="utf-8"
    )
    _meic_run(s, "design")
    assert results.trial_count(conn, "meic") == 4  # an inherited base key moves every arm


def _vix_rows(s, day, values):
    conn = store.connect(s, "SPX")
    try:
        vixfetch.write_series(conn, day, [(1_700_000_000.0 + 60 * i, v) for i, v in enumerate(values)])
    finally:
        conn.close()


def _cboe(rows):
    """A synthetic Cboe daily file in the home, in the vendor-free public layout the replay reads."""
    d = runner.cboe_dir()
    d.mkdir(parents=True, exist_ok=True)
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"] + [f"{m},{o},0,0,{c}" for m, o, c in rows]
    (d / "VIX.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_meic_fingerprint_moves_when_a_session_gains_vix_rows(meic_world):
    """`run meic`, `fetch-vix`, `run meic` again: different inputs, so a different stamp. Flies reads no
    VIX, so its stamp stays the one every stored flies report carries."""
    s, _ = meic_world
    before = _meic_run(s, "validation")["data_fingerprint"]
    flies_before = _run(s, "validation")["data_fingerprint"]
    assert _meic_run(s, "validation")["data_fingerprint"] == before  # same inputs, same stamp
    _vix_rows(s, "2026-10-14", [16.0, 16.4])
    after = _meic_run(s, "validation")
    assert after["data_fingerprint"] != before
    assert after["data_inputs"]["sessions_with_regime_rows"] == 1
    _vix_rows(s, "2026-10-14", [16.0, 18.5])  # a refetch that changes a value moves it again
    assert _meic_run(s, "validation")["data_fingerprint"] != after["data_fingerprint"]
    assert _run(s, "validation")["data_fingerprint"] == flies_before
    conn = store.connect(s, "SPX")
    try:
        assert flies_before == runner._fingerprint(conn, "SPX", ["2026-10-14", "2026-10-15"])
    finally:
        conn.close()


def test_the_meic_fingerprint_moves_when_a_cboe_file_changes(meic_world):
    s, _ = meic_world
    absent = _meic_run(s, "validation")["data_fingerprint"]
    _cboe([("10/13/2026", 17.0, 17.5), ("10/14/2026", 17.2, 18.0), ("10/15/2026", 18.1, 18.3)])
    first = _meic_run(s, "validation")
    assert first["data_fingerprint"] != absent
    vix_file = first["data_inputs"]["cboe"][0]
    assert vix_file["file"] == "VIX.csv" and vix_file["rows"] == 3
    _cboe([("10/13/2026", 17.0, 17.5), ("10/14/2026", 19.9, 18.0), ("10/15/2026", 18.1, 18.3)])
    corrected = _meic_run(s, "validation")["data_fingerprint"]
    assert corrected != first["data_fingerprint"]
    # The nightly refresh appending a session after the window is not a change to what the run read.
    _cboe(
        [("10/13/2026", 17.0, 17.5), ("10/14/2026", 19.9, 18.0), ("10/15/2026", 18.1, 18.3),
         ("10/16/2026", 20.0, 21.0)]
    )  # fmt: skip
    assert _meic_run(s, "validation")["data_fingerprint"] == corrected


def test_sessions_on_the_cboe_fallback_are_counted_in_the_report(meic_world):
    s, _ = meic_world
    sources = {
        "2026-10-14": {"history_intraday": 300},
        "2026-10-15": {"history_intraday": 120, "cboe_daily_open": 180},
    }

    def replay(day, out, arms, history_db, settle, config):
        out.write_bytes(b"")
        return {"ok": True, "vix_sources": sources[day]}

    report = runner.run(
        s, ["bp-10k"], "validation", module="meic", replay=replay, read_rows=_meic_read, workers=1,
        log=lambda _m: None,
    )  # fmt: skip
    assert report["vix_fallback_sessions"] == 1
    assert report["vix_sources"]["by_session"]["2026-10-14"]["source"] == "intraday"
    assert report["vix_sources"]["by_session"]["2026-10-15"]["source"] == "mixed"
    assert "1 of 2" in report["vix_note"]
    sources["2026-10-14"] = {"cboe_daily_open": 300}
    report = runner.run(
        s, ["bp-10k"], "validation", module="meic", replay=replay, read_rows=_meic_read, workers=1,
        log=lambda _m: None,
    )  # fmt: skip
    assert report["vix_fallback_sessions"] == 2
    assert report["vix_sources"]["sessions"]["cboe_fallback"] == 1
    assert "vix_sources" not in _run(s, "validation")  # flies reports no VIX source


def test_meic_refusals(meic_world):
    s, home = meic_world
    with pytest.raises(runner.RunRefused, match="MEIC has no arm"):
        runner.run(s, ["nope"], "validation", module="meic", replay=_meic_replay, read_rows=_meic_read)
    with pytest.raises(runner.RunRefused, match="advised arms are flies"):
        _meic_run(s, "design", extra_arms={"advised:x": {}})
    with pytest.raises(runner.RunRefused, match="module must be"):
        runner.run(s, [], "validation", module="bwb")
    (home / "config" / "meic.risk.json").unlink()
    with pytest.raises(runner.RunRefused, match="no MEIC registry config"):
        _meic_run(s, "validation")
