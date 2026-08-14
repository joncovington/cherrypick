"""Schedule semantics for the supervisor's pure core (jobspec.py).

These pin the behaviors the OS scheduler used to give for free and the ones it never could:
ET/DST-correct fire times, per-job windows and trading-day gates, fire-once daily/monthly stamps,
conservative catchup after sleep/hibernate, and interval jobs that never burst-catch-up.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import jobspec
from cherrypick.orchestrator.jobspec import JobSpec

ET = ZoneInfo("America/New_York")


def et(y, mo, d, h, mi, s=0, fold=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET, fold=fold)


def interval_spec(**kw):
    base = dict(id="j", argv=("py", "x"), kind=jobspec.KIND_INTERVAL, interval_seconds=600)
    base.update(kw)
    return JobSpec(**base)


def daily_spec(**kw):
    base = dict(id="j", argv=("py", "x"), kind=jobspec.KIND_DAILY, at_et="15:45", catchup_minutes=30)
    base.update(kw)
    return JobSpec(**base)


# --------------------------------------------------------------------------- interval jobs
def test_interval_fires_immediately_on_first_evaluation():
    fire, reason, patch = jobspec.should_start(interval_spec(), {}, et(2026, 8, 10, 12, 0))
    assert fire and reason == "due"
    assert patch["next_run_epoch"] == pytest.approx(et(2026, 8, 10, 12, 0).timestamp() + 600)


def test_interval_not_due_before_next_run():
    now = et(2026, 8, 10, 12, 0)
    state = {"next_run_epoch": now.timestamp() + 1}
    fire, reason, patch = jobspec.should_start(interval_spec(), state, now)
    assert not fire and reason == "not due" and patch == {}


def test_interval_after_hibernate_fires_once_not_a_burst():
    """A machine asleep through N intervals gets ONE immediate fire; next_run resumes from now."""
    spec = interval_spec()
    slept_past = et(2026, 8, 10, 9, 0).timestamp()  # next_run long gone
    now = et(2026, 8, 10, 14, 0)
    fire, _, patch = jobspec.should_start(spec, {"next_run_epoch": slept_past}, now)
    assert fire
    # the patch schedules from NOW, not from the missed slot — no catch-up burst is possible
    assert patch["next_run_epoch"] == pytest.approx(now.timestamp() + 600)


def test_interval_window_gates_and_reenters():
    spec = interval_spec(window_start="09:00", window_end="16:00")
    fire, reason, _ = jobspec.should_start(spec, {}, et(2026, 8, 10, 8, 59))
    assert not fire and reason == "outside window"
    fire, _, _ = jobspec.should_start(spec, {}, et(2026, 8, 10, 9, 0))
    assert fire  # fires immediately at window entry


def test_interval_window_invert():
    spec = interval_spec(window_start="09:30", window_end="16:00", window_invert=True)
    assert not jobspec.should_start(spec, {}, et(2026, 8, 10, 12, 0))[0]
    assert jobspec.should_start(spec, {}, et(2026, 8, 10, 16, 20))[0]  # settlement time
    assert jobspec.should_start(spec, {}, et(2026, 8, 10, 7, 0))[0]


def test_trading_days_only_blocks_weekend_and_holiday():
    spec = interval_spec(trading_days_only=True)
    sat = et(2026, 8, 8, 12, 0)
    assert not jobspec.should_start(spec, {}, sat)[0]
    holiday = et(2026, 7, 3, 12, 0)  # July 4th observed 2026-07-03 (Friday)
    fire, reason, _ = jobspec.should_start(spec, {}, holiday, holidays={"2026-07-03"})
    assert not fire and reason == "not a trading day"


def test_disabled_spec_never_fires():
    spec = interval_spec(enabled=False, enabled_reason="disabled in config")
    fire, reason, _ = jobspec.should_start(spec, {}, et(2026, 8, 10, 12, 0))
    assert not fire and reason == "disabled in config"


# --------------------------------------------------------------------------- daily jobs
def test_daily_fires_at_time_and_stamps_day():
    now = et(2026, 8, 10, 15, 45)
    fire, _, patch = jobspec.should_start(daily_spec(), {}, now)
    assert fire and patch == {"last_fire_day": "2026-08-10", "missed": None}


def test_daily_before_time_waits():
    fire, reason, _ = jobspec.should_start(daily_spec(), {}, et(2026, 8, 10, 15, 44))
    assert not fire and "before 15:45" in reason


def test_daily_fires_only_once_per_day():
    state = {"last_fire_day": "2026-08-10"}
    assert not jobspec.should_start(daily_spec(), state, et(2026, 8, 10, 15, 50))[0]


def test_daily_late_inside_catchup_fires():
    fire, _, patch = jobspec.should_start(daily_spec(), {}, et(2026, 8, 10, 16, 10))
    assert fire and patch["last_fire_day"] == "2026-08-10"


def test_daily_past_catchup_is_recorded_missed_not_fired():
    """Asleep at 15:45, awake at 17:00 with a 30m catchup: the occurrence is skipped and stamped,
    so it stops being evaluated — a 15:45 earnings entry fired at 17:00 would trade a dead session."""
    now = et(2026, 8, 10, 17, 0)
    fire, reason, patch = jobspec.should_start(daily_spec(), {}, now)
    assert not fire and "missed" in reason
    assert patch["last_fire_day"] == "2026-08-10" and patch["missed"] == now.isoformat()
    # and the stamp prevents re-evaluation for the rest of the day
    assert not jobspec.should_start(daily_spec(), patch, et(2026, 8, 10, 18, 0))[0]


def test_daily_next_day_fires_again():
    state = {"last_fire_day": "2026-08-10"}
    assert jobspec.should_start(daily_spec(), state, et(2026, 8, 11, 15, 45))[0]


def test_daily_dst_fall_back_fires_exactly_once():
    """2026-11-01: 01:00–02:00 ET repeats. A 01:30 job fires on the first occurrence; the repeated
    wall-clock hour cannot double-fire because the stamp is per ET calendar date."""
    spec = daily_spec(at_et="01:30", catchup_minutes=120)
    first = et(2026, 11, 1, 1, 30, fold=0)
    fire, _, patch = jobspec.should_start(spec, {}, first)
    assert fire
    second = et(2026, 11, 1, 1, 30, fold=1)  # same wall clock, one real hour later
    assert not jobspec.should_start(spec, patch, second)[0]


def test_daily_dst_spring_forward_0330_unaffected():
    """2026-03-08: 02:00–03:00 ET does not exist. The suite's earliest job (log-archive 03:30)
    schedules normally on that day."""
    spec = daily_spec(at_et="03:30", catchup_minutes=120)
    assert not jobspec.should_start(spec, {}, et(2026, 3, 8, 3, 29))[0]
    assert jobspec.should_start(spec, {}, et(2026, 3, 8, 3, 30))[0]


# --------------------------------------------------------------------------- monthly jobs
def monthly_spec(**kw):
    base = dict(
        id="j",
        argv=("py", "x"),
        kind=jobspec.KIND_MONTHLY,
        at_et="03:30",
        day_of_month=1,
        catchup_minutes=7 * 24 * 60,
    )
    base.update(kw)
    return JobSpec(**base)


def test_monthly_fires_on_day_and_stamps_month():
    fire, _, patch = jobspec.should_start(monthly_spec(), {}, et(2026, 9, 1, 3, 30))
    assert fire and patch["last_fire_month"] == "2026-09"


def test_monthly_before_day_waits_and_once_per_month():
    assert not jobspec.should_start(monthly_spec(day_of_month=5), {}, et(2026, 9, 4, 12, 0))[0]
    state = {"last_fire_month": "2026-09"}
    assert not jobspec.should_start(monthly_spec(), state, et(2026, 9, 2, 3, 30))[0]


def test_monthly_missed_day_fires_within_week_catchup():
    """Machine off over the 1st: the archive still fires days later (idempotent, finished months
    only), but not weeks later."""
    fire, _, patch = jobspec.should_start(monthly_spec(), {}, et(2026, 9, 4, 12, 0))
    assert fire and patch["last_fire_month"] == "2026-09"
    fire, reason, patch = jobspec.should_start(monthly_spec(), {}, et(2026, 9, 20, 12, 0))
    assert not fire and "missed" in reason and patch["last_fire_month"] == "2026-09"


# --------------------------------------------------------------------------- resident + arm record
def test_resident_should_run_window_and_trading_day():
    spec = JobSpec(
        id="r",
        argv=("py", "x"),
        kind=jobspec.KIND_RESIDENT,
        interval_seconds=15,
        window_start="09:30",
        window_end="16:00",
        trading_days_only=True,
    )
    assert jobspec.resident_should_run(spec, et(2026, 8, 10, 10, 0))[0]
    assert not jobspec.resident_should_run(spec, et(2026, 8, 10, 16, 1))[0]
    assert not jobspec.resident_should_run(spec, et(2026, 8, 8, 10, 0))[0]  # Saturday


def test_arm_record_valid_today_and_expiry():
    live = {"disarm_time": "17:00", "disarm_grace_minutes": 30}
    now = et(2026, 8, 10, 10, 0)
    ok, why = jobspec.arm_record_valid({"date": "2026-08-10"}, live, now)
    assert ok and "armed for 2026-08-10" in why
    assert not jobspec.arm_record_valid(None, live, now)[0]
    assert not jobspec.arm_record_valid({"date": "2026-08-09"}, live, now)[0]
    # past disarm + grace the record no longer enables the job (the watchdog backstop CRITICALs)
    late = et(2026, 8, 10, 17, 30)
    ok, why = jobspec.arm_record_valid({"date": "2026-08-10"}, live, late)
    assert not ok and "past disarm" in why
    # the record's own disarm_time wins over config
    ok, _ = jobspec.arm_record_valid({"date": "2026-08-10", "disarm_time": "18:30"}, live, late)
    assert ok


# --------------------------------------------------------------------------- derivation
def suite_cfg(**overrides):
    cfg = {
        "timezone": "America/New_York",
        "modules": {
            "meic": {
                "enabled": True,
                "path": "../meic",
                "paper": {
                    "kind": "self_healing",
                    "task_name": "cherrypick-meic-paper-loop",
                    "once_argv": ["-m", "cherrypick.meic.paper_loop", "--once", "--force"],
                    "tick_interval_seconds": 60,
                    "log": "paper_loop.log",
                },
            },
            "flies": {
                "enabled": True,
                "path": "../flies",
                "live": {
                    "task_name": "cherrypick-flies-live-loop",
                    "disarm_time": "17:00",
                    "disarm_grace_minutes": 30,
                },
                "paper": {
                    "kind": "self_healing",
                    "task_name": "cherrypick-flies-paper-loop",
                    "once_argv": ["-m", "cherrypick.flies.paper_loop", "--once"],
                    "tick_interval_seconds": 15,
                    "log": "flies_paper.log",
                },
            },
            "earnings": {
                "enabled": True,
                "path": "../earnings",
                "paper": {
                    "kind": "cherrypick_scheduled",
                    "entry_task_name": "cherrypick-earnings-paper-entry",
                    "exit_task_name": "cherrypick-earnings-paper-exit",
                    "entry_time": "15:45",
                    "exit_time": "09:45",
                    "dolt_service": {"task_name": "cherrypick-earnings-dolt", "interval_minutes": 5},
                },
            },
        },
        "watchdog": {"task_name": "cherrypick-watchdog", "interval_minutes": 10},
        "trade_notify": {"task_name": "cherrypick-trade-notify", "interval_seconds": 30},
        "follow_feed": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


def derive(cfg, now=None, arm_records=None):
    return jobspec.derive_jobs(
        cfg,
        pythonw="pythonw",
        launcher="run.py",
        now=now or et(2026, 8, 10, 12, 0),
        arm_records=arm_records,
    )


def test_derive_full_suite_job_table():
    jobs, errors = derive(suite_cfg())
    assert errors == {}
    by_id = {j.id: j for j in jobs}
    assert set(by_id) == {
        "watchdog",
        "streamer-health",
        "trade-notify",
        "follow-notify",
        "lossdog-notify",
        "desk-notify",
        "console",
        "meic-paper",
        "flies-paper",
        "flies-paper-offsession",
        "flies-live",
        "earnings-entry",
        "earnings-exit",
        "earnings-dolt",
        "symbol-watch",
        "reconcile",
        "log-archive",
        "review-provisional",
        "review-final",
        "review-narrative",
        "advisor-am",
        "advisor-midday",
        "advisor-pm",
        "advisor-deep",
    }
    assert by_id["watchdog"].interval_seconds == 600
    assert by_id["trade-notify"].interval_seconds == 30
    # streamer-health: whole-session window, trading days, on by default (replaces preopen)
    sh = by_id["streamer-health"]
    assert sh.enabled and sh.interval_seconds == 60
    assert (sh.window_start, sh.window_end, sh.trading_days_only) == ("09:00", "16:00", True)


def test_derive_meic_tick_strips_force():
    """The scheduled tick must never carry --force — it bypasses the RTH AND trading-day gates
    (the Saturday-settlement lesson)."""
    jobs, _ = derive(suite_cfg())
    meic = next(j for j in jobs if j.id == "meic-paper")
    assert "--force" not in meic.argv
    assert meic.argv == ("pythonw", "-m", "cherrypick.meic.paper_loop", "--once")
    assert meic.kind == jobspec.KIND_INTERVAL and meic.interval_seconds == 60


def test_derive_flies_subminute_becomes_resident_plus_offsession():
    jobs, _ = derive(suite_cfg())
    by_id = {j.id: j for j in jobs}
    res = by_id["flies-paper"]
    assert res.kind == jobspec.KIND_RESIDENT
    assert res.argv == ("pythonw", "-m", "cherrypick.flies.paper_loop", "--interval", "15")
    assert (res.window_start, res.window_end, res.trading_days_only) == ("09:30", "16:00", True)
    assert res.silence_file and res.silence_file.endswith("flies_paper.log")
    off = by_id["flies-paper-offsession"]
    assert off.kind == jobspec.KIND_INTERVAL and off.interval_seconds == 60
    assert off.window_invert and off.argv[-1] == "--once"


def test_derive_flies_live_disabled_without_arm_record():
    jobs, _ = derive(suite_cfg())
    live = next(j for j in jobs if j.id == "flies-live")
    assert not live.enabled and "not armed" in live.enabled_reason
    assert "live" in live.tags


def test_derive_flies_live_enabled_with_todays_arm_record():
    now = et(2026, 8, 10, 10, 0)
    jobs, _ = derive(suite_cfg(), now=now, arm_records={"flies": {"date": "2026-08-10"}})
    live = next(j for j in jobs if j.id == "flies-live")
    assert live.enabled and live.interval_seconds == 60
    assert live.argv == ("pythonw", "-m", "cherrypick.flies.live_loop", "--once", "--live")


def test_derive_disabled_optins_included_disabled():
    """Off-by-choice jobs stay visible (doctor's healthy-disabled distinction), never omitted."""
    jobs, _ = derive(suite_cfg())
    follow = next(j for j in jobs if j.id == "follow-notify")
    assert not follow.enabled and "disabled in config" in follow.enabled_reason
    sw = next(j for j in jobs if j.id == "symbol-watch")
    assert not sw.enabled


def test_derive_one_bad_block_disables_one_job_only():
    cfg = suite_cfg()
    cfg["modules"]["earnings"]["paper"]["entry_time"] = None  # breaks earnings-entry derivation
    jobs, errors = derive(cfg)
    ids = {j.id for j in jobs}
    assert "earnings-entry" in errors
    assert "earnings-exit" in ids and "watchdog" in ids and "meic-paper" in ids


def test_derive_legacy_interval_minutes_honored_for_trade_notify():
    cfg = suite_cfg()
    cfg["trade_notify"] = {"task_name": "cherrypick-trade-notify", "interval_minutes": 2}
    jobs, _ = derive(cfg)
    tn = next(j for j in jobs if j.id == "trade-notify")
    assert tn.interval_seconds == 120


# --------------------------------------------------------------------------- console (read surface)
def _built_console(tmp_path):
    """A console checkout that looks built (server/dist/index.js present)."""
    root = tmp_path / "console"
    (root / "server" / "dist").mkdir(parents=True)
    (root / "server" / "dist" / "index.js").write_text("", encoding="utf-8")
    (root / "run.py").write_text("", encoding="utf-8")
    return root


def test_console_is_a_resident_with_no_window_at_all(tmp_path):
    """Every other resident is session-scoped. This one must not be: a read surface you can only open
    during RTH cannot be used to read the session that just ended, or last week's."""
    cfg = suite_cfg(console={"path": str(_built_console(tmp_path))})
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    assert con.kind == jobspec.KIND_RESIDENT
    assert con.window_start is None and con.window_end is None
    assert con.trading_days_only is False
    # Midnight on a Sunday is exactly the case a windowed job would refuse.
    assert jobspec.resident_should_run(con, et(2026, 8, 9, 0, 30)) == (True, "")


def test_console_watches_a_heartbeat_file_not_a_log(tmp_path):
    """Node stays alive with a wedged event loop, so process-liveness would never restart it. The
    server rewrites this file on a timer; a stale mtime is the wedge signal."""
    cfg = suite_cfg(console={"path": str(_built_console(tmp_path))})
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    assert con.silence_file is not None and con.silence_file.endswith("console.heartbeat")
    assert con.silence_seconds == 60


def test_console_runs_through_its_own_launcher_from_its_own_root(tmp_path):
    root = _built_console(tmp_path)
    cfg = suite_cfg(console={"path": str(root)})
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    # run.py locates and execs the Node server, so the supervisor needs no Node-specific handling.
    assert con.argv == ("pythonw", str(root / "run.py"), "dashboard", "--serve")
    assert con.cwd == str(root)


def test_console_unbuilt_is_disabled_with_a_reason_not_a_crash_loop(tmp_path):
    """A checkout that never ran `pnpm build` would otherwise respawn and die on the backoff curve
    forever. Report it the way every other off-by-choice job is reported."""
    empty = tmp_path / "console"
    empty.mkdir()
    cfg = suite_cfg(console={"path": str(empty)})
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    assert not con.enabled and "not built" in con.enabled_reason


def test_console_can_be_turned_off_and_stays_visible(tmp_path):
    cfg = suite_cfg(console={"enabled": False, "path": str(_built_console(tmp_path))})
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    assert not con.enabled and "disabled in config" in con.enabled_reason


def test_console_dev_backoff_seconds_overrides_the_default_cap(tmp_path):
    """Opt-in dev knob: a checkout under active iteration gets restarted by hand far more often than
    the supervisor's normal 10-minute crash-backoff cap was ever sized for."""
    cfg = suite_cfg(
        console={"path": str(_built_console(tmp_path)), "dev_backoff_seconds": 20}
    )
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    assert con.backoff_cap_seconds == 20


def test_console_backoff_cap_is_unset_by_default(tmp_path):
    """Unset means the supervisor's own default cap -- every other job's behavior, untouched."""
    cfg = suite_cfg(console={"path": str(_built_console(tmp_path))})
    con = next(j for j in derive(cfg)[0] if j.id == "console")
    assert con.backoff_cap_seconds is None


def test_review_runs_two_passes_on_by_default():
    """Two passes because the modules do not finish together: the provisional one captures the 0DTE
    books complete with earnings still carrying overnight, the final one closes that session out the
    next morning. The narrative is only ever written against a final set."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    prov, final = by_id["review-provisional"], by_id["review-final"]
    assert prov.enabled and final.enabled
    assert (prov.at_et, final.at_et) == ("16:30", "10:15")
    assert "--final" in final.argv and "--final" not in prov.argv


def test_review_can_be_turned_off():
    cfg = suite_cfg()
    cfg["review"] = {"enabled": False}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert not by_id["review-provisional"].enabled
    assert "disabled in config (review)" in by_id["review-provisional"].enabled_reason


def test_the_narrative_is_off_by_default_and_tagged_ai():
    """It shells out to Claude Code, which the suite does not otherwise depend on — so it stays off
    until `claude` is on PATH and someone turns it on."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    job = by_id["review-narrative"]
    assert not job.enabled
    assert "review.narrative" in job.enabled_reason
    assert "ai" in job.tags


def test_every_derived_run_py_job_actually_parses():
    """The guard that was missing: a derived argv the CLI's parser rejects.

    `review-provisional`/`review-final` passed `--final`/`--provisional`, which the parser had never
    been taught, so both jobs exited 2 at argparse every single day and no fact set was built. The
    job table looked perfectly healthy — enabled, on schedule, firing — because nothing checked that
    the command it fires is a command the CLI accepts.
    """
    import contextlib
    import io

    from cherrypick import cli

    cfg = suite_cfg()
    cfg["review"] = {"narrative": True}
    cfg["advisor"] = {"enabled": True}
    parser = cli.build_parser()

    offenders = []
    for job in derive(cfg)[0]:
        argv = list(job.argv)
        # ONLY this package's own launcher. The console job also runs a `run.py` -- its own, with a
        # completely different command set -- and a module's argv belongs to that module.
        if argv[1:2] != ["run.py"]:
            continue
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                parser.parse_args(argv[2:])
            except SystemExit:
                offenders.append(f"{job.id}: {' '.join(argv[2:])}")
    assert not offenders, f"derived argv the CLI would reject at the parser: {offenders}"


def test_the_advisor_derives_four_slots_off_by_default():
    """Three light checkpoints and one deep run, all AI-tagged and trading-days-only, all disabled
    until someone turns the advisor on."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    slots = ["advisor-am", "advisor-midday", "advisor-pm", "advisor-deep"]
    for job_id in slots:
        job = by_id[job_id]
        assert not job.enabled
        assert "disabled in config (advisor)" in job.enabled_reason
        assert "ai" in job.tags
        assert job.trading_days_only
        assert job.kind == "daily"
        assert "advisor_checkpoint.py" in " ".join(job.argv)

    assert [by_id[s].at_et for s in slots] == ["10:30", "12:30", "14:30", "17:00"]


def test_the_scheduled_scripts_resolve_to_files_that_exist():
    """`scripts/` sits at the repo root, two levels above the launcher. A path that points at a
    directory which has never existed spawns nothing and reports success — which is exactly how the
    narrative's broken path went unnoticed until the advisor was about to inherit it."""
    import os

    from cherrypick.orchestrator.jobspec import _advisor_script, _narrative_script

    launcher = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py"
    )
    assert os.path.exists(_narrative_script(launcher))
    assert os.path.exists(_advisor_script(launcher))


def test_the_deep_slot_runs_after_the_reviews_provisional_pass():
    """The deep pack carries that fact set. Running before it would carry yesterday's."""
    cfg = suite_cfg()
    cfg["advisor"] = {"enabled": True}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert by_id["advisor-deep"].enabled
    assert by_id["advisor-deep"].at_et > by_id["review-provisional"].at_et


def test_the_light_slots_carry_the_light_model_and_the_deep_slot_the_deep_one():
    """Model names live in config and travel on argv — no model id appears in this suite's code."""
    cfg = suite_cfg()
    cfg["advisor"] = {"enabled": True, "light_model": "haiku", "deep_model": "opus",
                      "modules": {"flies": {"enabled": True}}}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert "haiku" in by_id["advisor-am"].argv
    assert "opus" in by_id["advisor-deep"].argv
    # Only the modules the suite enabled for the advisor are passed through.
    modules = by_id["advisor-am"].argv[by_id["advisor-am"].argv.index("--modules") + 1]
    assert set(modules.split(",")) == {"meic", "flies", "earnings"}


def test_a_missed_light_checkpoint_goes_stale_but_a_missed_deep_run_does_not():
    """A light slot describes the session as it stands; the deep slot issues tomorrow's advice, so
    it stays worth firing until late evening."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    assert by_id["advisor-am"].catchup_minutes == 45
    assert by_id["advisor-deep"].catchup_minutes == 300


def test_the_narrative_runs_after_the_final_pass_never_with_it():
    """It must only ever see a finalised session: a note written against numbers that will still
    move records something that never happened."""
    cfg = suite_cfg()
    cfg["review"] = {"narrative": True}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert by_id["review-narrative"].enabled
    assert by_id["review-narrative"].at_et > by_id["review-final"].at_et
    assert by_id["review-narrative"].trading_days_only
