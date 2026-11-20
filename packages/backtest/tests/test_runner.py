"""The runner's rules, with the replay and the ledger read faked: what may run when, that every design
evaluation is a trial counted once per spec, and that a holdout is spent once."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from test_fetch import FakeClient, _rows

from cherrypick.backtest import fetch, paths, results, runner, settings, store, vixfetch

DAYS = ["2025-03-04", "2025-03-05", "2026-10-14", "2026-10-15"]
#: After the holdout window: a run from here reads only the fixture's last two sessions and spends nothing.
FORWARD = "2026-10-13"


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

    report = runner.run(
        s, ["range-high"], "all", start=FORWARD, replay=_replay, read_rows=same, log=lambda _m: None
    )
    assert report["arms"]["range-high"]["no_exposure"] is True
    assert _run(s, "all", start=FORWARD)["arms"]["range-high"]["no_exposure"] is False


def test_history_runs_as_recorded_and_every_run_is_a_trial(world):
    """No check against the recorder stands before a run: with no report of any kind on disk, the
    design window and a forward run both run, each arm a trial, and neither is marked."""
    s, _ = world
    assert not (paths.results_dir() / "validation").exists()
    report = _run(s, "all", start=FORWARD)
    assert report["arms"]["control"]["summary"]["sessions"] == 2  # 10-14, 10-15
    # The report carries what decided each arm's trades, hashed to the recorded spec_hash.
    spec = report["arms"]["control"]["spec"]
    assert spec["arm"] == "control" and runner.spec_hash(spec) == report["arms"]["control"]["spec_hash"]
    assert report["arms"]["range-high"]["vs_control"] is not None
    assert not {"provisional", "provisional_note"} & set(report)
    conn = results.connect()
    assert results.trial_count(conn, "flies") == 2
    _run(s, "design")
    assert {r[0] for r in conn.execute("SELECT status FROM runs")} == {"done"}
    assert "validation" not in runner.WINDOWS


def test_design_runs_are_trials_counted_once_per_spec(world):
    s, home = world
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
    with pytest.raises(runner.RunRefused, match="spend-holdout"):
        _run(s, "holdout")
    # The fixture's sessions sit before the holdout window, so it spends on an empty span; the rule
    # under test is the spending, not the span.
    _run(s, "holdout", spend_holdout=True)
    with pytest.raises(runner.RunRefused, match="already spent"):
        _run(s, "holdout", spend_holdout=True)


def test_control_never_spends_the_holdout(world):
    """Control runs beside every arm and is never judged, so a later study's holdout is not refused
    because an earlier one ran control there (item 24, 2026-10-29). The judged arm still is."""
    s, home = world
    report = _run(s, "holdout", spend_holdout=True)
    conn = results.connect()
    assert not results.holdout_spent(conn, "flies", report["arms"]["control"]["spec_hash"])
    cfg = json.loads((home / "config" / "flies.json").read_text())
    cfg["arms"]["range-high"]["gate"] = "y"
    (home / "config" / "flies.json").write_text(json.dumps(cfg), encoding="utf-8")
    again = _run(s, "holdout", spend_holdout=True)
    assert again["arms"]["control"]["spec_hash"] == report["arms"]["control"]["spec_hash"]
    with pytest.raises(runner.RunRefused, match=r"already spent for \['range-high'\]"):
        _run(s, "holdout", spend_holdout=True)


def test_an_unknown_arm_or_a_missing_config_is_refused(world):
    s, home = world
    with pytest.raises(runner.RunRefused, match="no arm"):
        runner.run(s, ["nope"], "all", start=FORWARD, replay=_replay, read_rows=_read, log=lambda _m: None)
    (home / "config" / "flies.json").unlink()
    with pytest.raises(runner.RunRefused, match="no flies config"):
        _run(s, "all", start=FORWARD)


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


def test_meic_runs_from_a_snapshot_of_both_its_config_files(meic_world):
    s, _ = meic_world
    MEIC_CALLS.clear()
    report = _meic_run(s, "all", start=FORWARD)
    assert report["module"] == "meic" and report["fill_model"] == runner.MEIC_FILL_MODEL
    assert MEIC_CALLS and MEIC_CALLS[0]["arms"] == ["control", "bp-10k"]
    assert MEIC_CALLS[0]["base"]["stop_trigger_ratio"] == 0.95
    assert "bp-10k" in MEIC_CALLS[0]["registry"]["profiles"]
    assert report["arms"]["bp-10k"]["vs_control"] is not None and "flies_config" not in report


def test_a_meic_trial_is_keyed_on_the_merged_params_not_the_comments(meic_world):
    s, home = meic_world
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
    before = _meic_run(s, "all", start=FORWARD)["data_fingerprint"]
    flies_before = _run(s, "all", start=FORWARD)["data_fingerprint"]
    assert _meic_run(s, "all", start=FORWARD)["data_fingerprint"] == before  # same inputs, same stamp
    _vix_rows(s, "2026-10-14", [16.0, 16.4])
    after = _meic_run(s, "all", start=FORWARD)
    assert after["data_fingerprint"] != before
    assert after["data_inputs"]["sessions_with_regime_rows"] == 1
    _vix_rows(s, "2026-10-14", [16.0, 18.5])  # a refetch that changes a value moves it again
    assert _meic_run(s, "all", start=FORWARD)["data_fingerprint"] != after["data_fingerprint"]
    assert _run(s, "all", start=FORWARD)["data_fingerprint"] == flies_before
    conn = store.connect(s, "SPX")
    try:
        assert flies_before == runner._fingerprint(conn, "SPX", ["2026-10-14", "2026-10-15"])
    finally:
        conn.close()


def test_the_meic_fingerprint_moves_when_a_cboe_file_changes(meic_world):
    s, _ = meic_world
    absent = _meic_run(s, "all", start=FORWARD)["data_fingerprint"]
    _cboe([("10/13/2026", 17.0, 17.5), ("10/14/2026", 17.2, 18.0), ("10/15/2026", 18.1, 18.3)])
    first = _meic_run(s, "all", start=FORWARD)
    assert first["data_fingerprint"] != absent
    vix_file = first["data_inputs"]["cboe"][0]
    assert vix_file["file"] == "VIX.csv" and vix_file["rows"] == 3
    _cboe([("10/13/2026", 17.0, 17.5), ("10/14/2026", 19.9, 18.0), ("10/15/2026", 18.1, 18.3)])
    corrected = _meic_run(s, "all", start=FORWARD)["data_fingerprint"]
    assert corrected != first["data_fingerprint"]
    # The nightly refresh appending a session after the window is not a change to what the run read.
    _cboe(
        [("10/13/2026", 17.0, 17.5), ("10/14/2026", 19.9, 18.0), ("10/15/2026", 18.1, 18.3),
         ("10/16/2026", 20.0, 21.0)]
    )  # fmt: skip
    assert _meic_run(s, "all", start=FORWARD)["data_fingerprint"] == corrected


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
        s, ["bp-10k"], "all", start=FORWARD, module="meic", replay=replay, read_rows=_meic_read, workers=1,
        log=lambda _m: None,
    )  # fmt: skip
    assert report["vix_fallback_sessions"] == 1
    assert report["vix_sources"]["by_session"]["2026-10-14"]["source"] == "intraday"
    assert report["vix_sources"]["by_session"]["2026-10-15"]["source"] == "mixed"
    assert "1 of 2" in report["vix_note"]
    sources["2026-10-14"] = {"cboe_daily_open": 300}
    report = runner.run(
        s, ["bp-10k"], "all", start=FORWARD, module="meic", replay=replay, read_rows=_meic_read, workers=1,
        log=lambda _m: None,
    )  # fmt: skip
    assert report["vix_fallback_sessions"] == 2
    assert report["vix_sources"]["sessions"]["cboe_fallback"] == 1
    assert "vix_sources" not in _run(s, "all", start=FORWARD)  # flies reports no VIX source


def test_meic_refusals(meic_world):
    s, home = meic_world
    with pytest.raises(runner.RunRefused, match="MEIC has no arm"):
        runner.run(
            s, ["nope"], "all", start=FORWARD, module="meic", replay=_meic_replay, read_rows=_meic_read
        )
    with pytest.raises(runner.RunRefused, match="advised arms are flies"):
        _meic_run(s, "design", extra_arms={"advised:x": {}})
    with pytest.raises(runner.RunRefused, match="module must be"):
        runner.run(s, [], "all", start=FORWARD, module="bwb")
    (home / "config" / "meic.risk.json").unlink()
    with pytest.raises(runner.RunRefused, match="no MEIC registry config"):
        _meic_run(s, "all", start=FORWARD)


# ------------------------------------------------------------------------------------------- resuming


class Interrupted(BaseException):
    """A reboot mid-run: not an Exception, so no session's refusal swallows it."""


def _interrupted_run(s, cut: str) -> list[str]:
    """Run forward from FORWARD until the replay of `cut`, which dies after writing half a ledger."""
    replayed = []

    def replay(day, out, arms, history_db, settle, config):
        replayed.append(day)
        if day == cut:
            out.write_bytes(b"half a ledger")
            raise Interrupted
        return _replay(day, out, arms, history_db, settle, config)

    with pytest.raises(Interrupted):
        runner.run(
            s,
            ["range-high"],
            "all",
            start=FORWARD,
            replay=replay,
            read_rows=_read,
            workers=1,
            log=lambda _m: None,
        )
    return replayed


STALE: list[str] = []


def _rerun(s, read_rows=None):
    read_rows = read_rows or _read
    replayed = []

    def replay(day, out, arms, history_db, settle, config):
        if out.exists():  # recorded, not asserted: the runner would take an assertion for a refusal
            STALE.append(day)
        replayed.append(day)
        return _replay(day, out, arms, history_db, settle, config)

    report = runner.run(
        s,
        ["range-high"],
        "all",
        start=FORWARD,
        replay=replay,
        read_rows=read_rows,
        workers=1,
        log=lambda _m: None,
    )
    return report, replayed


@pytest.fixture()
def clean_commit(monkeypatch):
    monkeypatch.setattr(runner, "_code_commit", lambda: "abc123")
    monkeypatch.setattr(runner, "_code_pin", lambda module: "t1,t2,t3")
    monkeypatch.setattr(runner.looplock, "pid_alive", lambda pid: False)  # the interrupted owner is gone


def _run_dirs() -> set[str]:
    return {p.name for p in (paths.results_dir() / "runs").iterdir() if p.is_dir() and p.name != "pending"}


def test_an_interrupted_run_resumes_from_its_finished_sessions(world, clean_commit):
    s, _ = world
    assert _interrupted_run(s, "2026-10-15") == ["2026-10-14", "2026-10-15"]
    STALE.clear()
    report, replayed = _rerun(s)
    assert STALE == [], "a ledger the interruption cut short is discarded before its replay"
    assert replayed == ["2026-10-15"], "the finished session is read back, not replayed"
    assert report["resumed"] == {"sessions_reused": 1}
    assert _run_dirs() == {report["run_id"]}, "the resumed run keeps the interrupted run's id and directory"
    assert not list((paths.results_dir() / "runs" / "pending").iterdir()), "a recorded run is never resumed"
    whole = _run(s, "all", start=FORWARD)
    assert "resumed" not in whole
    assert report["arms"] == whole["arms"], "a resumed run reports what an uninterrupted one does"


def test_a_resumed_run_reports_its_reused_sessions_as_skipped(world, clean_commit, monkeypatch):
    """2026-11-06: counted as worked, item 44's 493 reused sessions made its card read 4 s a session
    and 18 minutes left. The runner marks them skipped, so the rate and the time left are the work's."""
    s, _ = world
    _interrupted_run(s, "2026-10-15")
    advanced = []
    real = runner.core_progress.Reporter

    class Recording(real):
        def advance(self, name, n=1, *, current=None, skipped=False, key=None):
            advanced.append((current, skipped))
            return super().advance(name, n, current=current, skipped=skipped, key=key)

    monkeypatch.setattr(runner.core_progress, "Reporter", Recording)
    _rerun(s)
    assert advanced == [("2026-10-14", True), ("2026-10-15", False)]


def test_a_run_is_not_resumed_when_any_input_changed(world, clean_commit):
    s, home = world
    _interrupted_run(s, "2026-10-15")
    cfg = json.loads((home / "config" / "flies.json").read_text())
    cfg["arms"]["range-high"]["gate"] = "y"
    (home / "config" / "flies.json").write_text(json.dumps(cfg), encoding="utf-8")
    report, replayed = _rerun(s)
    assert "resumed" not in report and replayed == ["2026-10-14", "2026-10-15"]


def test_a_merge_outside_the_replay_s_code_still_resumes_and_one_inside_does_not(
    world, clean_commit, monkeypatch
):
    """Item 41's design run lost 147 sessions to a reboot after two merges that touched neither the
    runner, core nor MEIC: the key pinned the repo's commit."""
    s, _ = world
    _interrupted_run(s, "2026-10-15")
    monkeypatch.setattr(runner, "_code_commit", lambda: "def456")  # a console page merged meanwhile
    report, replayed = _rerun(s)
    assert report["resumed"] == {"sessions_reused": 1} and replayed == ["2026-10-15"]
    assert report["code_commit"] == "def456", "the run still records the commit it finished on"
    _interrupted_run(s, "2026-10-15")
    monkeypatch.setattr(runner, "_code_pin", lambda module: "t1,t2,t9")  # the replay's code changed
    report, replayed = _rerun(s, read_rows=_read_changed)
    assert "resumed" not in report, "a change that moves a result starts afresh"
    assert replayed == ["2026-10-14", "2026-10-14", "2026-10-15"], "the check's replay, then every session"


def _read_changed(path):
    """The replay's result moved: a session re-replayed by the check reads back differently."""
    rows = _read(path)
    return [{**r, "net_pnl": r["net_pnl"] + 1.0} for r in rows] if path.parent.name == "verify" else rows


def test_a_code_change_that_replays_identically_is_taken_over_after_the_check(
    world, clean_commit, monkeypatch
):
    """2026-11-05: #877 changed only how the runner starts replays, yet item 44 lost 469 sessions to
    the code pin. A run on other code is taken over when its sampled sessions replay identically."""
    s, _ = world
    _interrupted_run(s, "2026-10-15")
    monkeypatch.setattr(runner, "_code_pin", lambda module: "t1,t2,t9")
    report, replayed = _rerun(s)
    assert replayed == ["2026-10-14", "2026-10-15"], "the sampled finished session, then the one left"
    resumed = report["resumed"]
    assert resumed["sessions_reused"] == 1
    assert resumed["code_from"] == "t1,t2,t3" and resumed["code_to"] == "t1,t2,t9"
    assert resumed["verified"]["identical"] is True
    assert resumed["verified"]["sessions"] == [{"session": "2026-10-14", "identical": True}]
    assert _run_dirs() == {report["run_id"]}
    assert not (paths.results_dir() / "runs" / report["run_id"] / "verify").exists(), "the check's copies go"


def test_a_run_file_from_before_the_change_is_found_by_its_commit_s_code(world, clean_commit, monkeypatch):
    """Run files written before 2026-11-06 are named by a key that holds the code. Item 44's 469
    sessions sit behind one: it is found by recomputing that key under each recent commit's code."""
    s, _ = world
    seen = {}
    real_claim = runner._claim

    def spy(parts, code, module, verify):
        seen["parts"] = parts
        return real_claim(parts, code, module, verify)

    monkeypatch.setattr(runner, "_claim", spy)
    _interrupted_run(s, "2026-10-15")
    monkeypatch.setattr(runner, "_claim", real_claim)
    pending = paths.results_dir() / "runs" / "pending"
    (spec_file,) = list(pending.iterdir())
    run_id = json.loads(spec_file.read_text())["run_id"]
    spec_file.unlink()  # as the old runner wrote it: named by the whole key, code and all, no code inside
    legacy = runner._resume_key({**seen["parts"], "code": "t1,t2,t3"})
    (pending / f"{legacy}.json").write_text(json.dumps({"run_id": run_id, "pid": 1}), encoding="utf-8")
    monkeypatch.setattr(runner, "_code_pin", lambda module: "t1,t2,t9")
    monkeypatch.setattr(runner, "_recent_pins", lambda module, limit=400: ["t1,t2,t9", "t1,t2,t3"])
    report, _ = _rerun(s)
    assert report["run_id"] == run_id and report["resumed"]["code_from"] == "t1,t2,t3"
    assert not (pending / f"{legacy}.json").exists(), "the old file is superseded"


def test_the_check_samples_the_ends_and_between_and_ignores_labels():
    days = [f"2024-01-{d:02d}" for d in range(1, 21)]
    sample = runner.verify_sample(days)
    assert len(sample) == 5 and sample[0] == days[0] and sample[-1] == days[-1]
    assert runner.verify_sample(days[:3]) == days[:3]
    a = [{"arm": "x", "net_pnl": 1.0, "ic_order_id": "PAPER-x-1"}]
    assert runner.same_rows(a, [{**a[0], "ic_order_id": "PAPER-x-2"}]), "an order id is a label"
    assert not runner.same_rows(a, [{**a[0], "net_pnl": 1.01}])
    assert not runner.same_rows(a, a + a)


def _pin_switches_after_the_first_session(monkeypatch):
    """The checkout's code moves once the run's first session is finished, as a merge pulled mid-run
    reaches every replay after it (item 44's design run, 2026-11-06)."""

    def pin(module):
        done = list((paths.results_dir() / "runs").glob("*/2026-10-14.ok.json"))
        return "t1,t2,t9" if done else "t1,t2,t3"

    monkeypatch.setattr(runner, "_code_pin", pin)


def test_a_code_change_mid_run_that_replays_identically_is_recorded(world, clean_commit, monkeypatch):
    s, _ = world
    _pin_switches_after_the_first_session(monkeypatch)
    report = runner.run(
        s,
        ["range-high"],
        "all",
        start=FORWARD,
        replay=_replay,
        read_rows=_read,
        workers=1,
        log=lambda _m: None,
    )
    (change,) = report["code_changes"]
    assert change["before_session"] == "2026-10-15"
    assert (change["code_from"], change["code_to"]) == ("t1,t2,t3", "t1,t2,t9")
    assert change["verified"]["identical"] is True
    assert change["verified"]["sessions"] == [{"session": "2026-10-14", "identical": True}]
    assert set(report["arms"]) == {"control", "range-high"}


def test_a_code_change_mid_run_that_replays_differently_stops_the_run(world, clean_commit, monkeypatch):
    s, _ = world
    _pin_switches_after_the_first_session(monkeypatch)
    with pytest.raises(runner.RunRefused, match="changed mid-run"):
        runner.run(
            s, ["range-high"], "all", start=FORWARD, replay=_replay, read_rows=_read_changed, workers=1,
            log=lambda _m: None,
        )  # fmt: skip
    (run_dir,) = [p for p in (paths.results_dir() / "runs").iterdir() if p.is_dir() and p.name != "pending"]
    assert (run_dir / "2026-10-14.ok.json").is_file(), "the finished session is kept"
    assert not (run_dir / "2026-10-15.ok.json").exists(), "nothing replays on the code that differs"
    assert list((paths.results_dir() / "runs" / "pending").iterdir()), "the run stays resumable"


def test_the_recent_pins_are_this_repo_s_committed_trees():
    pins = runner._recent_pins("meic", limit=5)
    assert pins and pins[0] == runner._code_pin("meic").split("+")[0]
    assert all(len(p.split(",")) == 3 for p in pins)


def test_the_pin_is_the_runner_s_core_s_and_the_module_s_committed_trees():
    pin = runner._code_pin("meic")
    assert pin is not None and len(pin.split("+")[0].split(",")) == 3
    assert runner._code_pin("flies").split(",")[:2] == pin.split(",")[:2], "the runner and core are shared"
    assert runner._code_pin("no-such-module") is None, "a tree that cannot be read pins nothing"


def test_a_dirty_checkout_or_a_live_owner_is_never_resumed(world, clean_commit, monkeypatch):
    s, _ = world
    monkeypatch.setattr(runner, "_code_pin", lambda module: "t1,t2,t3+dirty")
    _interrupted_run(s, "2026-10-15")
    report, replayed = _rerun(s)
    assert "resumed" not in report and len(replayed) == 2
    monkeypatch.setattr(runner, "_code_pin", lambda module: "t1,t2,t3")
    _interrupted_run(s, "2026-10-15")
    monkeypatch.setattr(runner.looplock, "pid_alive", lambda pid: True)  # its owner still runs
    report, replayed = _rerun(s)
    assert "resumed" not in report and len(replayed) == 2


def test_a_run_over_all_of_history_spends_the_holdout_its_span_reaches_and_counts_a_trial(world):
    # #1077: one run over the whole history, cut at a study's declared splits afterwards.
    s, _ = world
    with pytest.raises(runner.RunRefused, match="spend-holdout"):
        _run(s, "all")
    report = _run(s, "all", spend_holdout=True)
    assert report["arms"]["range-high"]["summary"]["sessions"] > 0
    conn = results.connect()
    assert results.trial_count(conn, "flies") >= 1
    assert results.holdout_spent(conn, "flies", report["arms"]["range-high"]["spec_hash"])
    assert not results.holdout_spent(conn, "flies", report["arms"]["control"]["spec_hash"])
    with pytest.raises(runner.RunRefused, match="already spent"):
        _run(s, "all", spend_holdout=True)


def test_a_forward_run_wholly_after_the_holdout_spends_nothing(world):
    # A forward retest reads only sessions after the holdout window: it needs no spend, and a later
    # run over all of history can still spend it.
    s, _ = world
    after = date.fromisoformat(s["windows"]["holdout"][1]) + timedelta(days=1)
    report = _run(s, "all", start=after.isoformat())
    conn = results.connect()
    assert not results.holdout_spent(conn, "flies", report["arms"]["range-high"]["spec_hash"])
    _run(s, "all", spend_holdout=True)


def test_reads_holdout_is_any_overlap_with_its_dates(world):
    s, _ = world
    lo, hi = s["windows"]["holdout"]
    past = (date.fromisoformat(hi) + timedelta(days=1)).isoformat()
    assert runner.reads_holdout(s, "2020-01-01", lo) and runner.reads_holdout(s, hi, "2030-01-01")
    assert not runner.reads_holdout(s, "2020-01-01", "2020-12-31")
    assert not runner.reads_holdout(s, past, "2030-01-01")
