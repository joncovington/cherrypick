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
        "capabilities": {"claude": True, "dolt": True},
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
        "desk-notify",
        "status-digest",
        "status-digest-close",
        "flies-payoff-post",
        "flies-payoff-intraday",
        "flies-intraday-agent",
        "flies-intraday-agent-live",
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
        "config-backup",
        "suite-backup",
        "futures-contracts",
        "guard-mutants",
        "earnings-dolt-pull",
        "pmcc-earnings-refresh",
        "report-edition",
        "report-edition-retry",
        "report-charts",
        "power-watch",
        "live-positions",
        "report-session-alert",
        "report-screener-measure-1",
        "report-screener-measure-2",
        "report-screener-greeks",
        "universe-measure-1",
        "universe-measure-2",
        "universe-daily",
        "universe-watchlist",
        "market-files",
        "market-files-retry",
        "technicals-land",
        "technicals-dividends",
        "technicals-index-bars",
        "technicals-iv-rank",
        "technicals-liquidity",
        "technicals-report",
        "earnings-moves",
        "fetch-headlines",
        "review-provisional",
        "review-final",
        "review-narrative",
        "morning-factpack",
        "morning-narrative",
        "advisor-deep",
        *QUIKOPTIONS_JOBS,
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
    # Silence is measured against the loop's own heartbeat, never its log. Pointing it at the log
    # made supervision a function of how talkative a module happened to be, and killed the quiet one
    # (calendars) every two minutes for four days. Pinned against the module's OWN resolver, so the
    # writer and the watcher cannot drift apart unnoticed.
    from cherrypick.core import home as _core_home

    from cherrypick.orchestrator import config as cfgmod

    assert res.silence_file == str(cfgmod.resident_heartbeat_path("flies"))
    assert res.silence_file.endswith(_core_home.heartbeat_path("flies").name)
    assert not res.silence_file.endswith(".log")
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
    cfg = suite_cfg(console={"path": str(_built_console(tmp_path)), "dev_backoff_seconds": 20})
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


def test_morning_factpack_on_by_default_narrative_off_and_tagged_ai():
    """The pack is credential-free and read-only, so it defaults on like the review; the narrative
    shells out to Claude Code (and, unlike the EOD one, does web lookups), so it stays off until
    someone turns it on. Both are pre-open jobs with a deliberately tight catch-up — a morning pack
    caught up at 11:00 describes a market that already opened."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    pack, note = by_id["morning-factpack"], by_id["morning-narrative"]
    assert pack.enabled and pack.trading_days_only
    assert (pack.at_et, note.at_et) == ("08:30", "09:00")
    assert pack.catchup_minutes == 90
    assert not note.enabled
    assert "morning.narrative" in note.enabled_reason
    assert "ai" in note.tags
    assert "morning_narrative.py" in note.argv[1]


def test_morning_can_be_turned_off():
    cfg = suite_cfg()
    cfg["morning"] = {"enabled": False}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert not by_id["morning-factpack"].enabled
    assert "disabled in config (morning)" in by_id["morning-factpack"].enabled_reason


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


def test_the_advisor_derives_only_the_deep_slot_by_default():
    """One deep run at 17:00, AI-tagged and trading-days-only, disabled until someone turns the
    advisor on.

    The default was seven light checkpoints plus deep until 2026-08-26. It is deep-only now because
    the light slots' own record did not justify them — 36 of them produced four `creative` proposals
    and one critical flag the deep slot re-derived anyway, while adding ~10% to the pack the deep
    slot has to read (packages/advisor/CLAUDE.md). Light slots are still fully supported and still
    derive when configured; the two tests below now say so explicitly rather than leaning on a
    default, which is the more honest shape for them anyway."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}

    job = by_id["advisor-deep"]
    assert not job.enabled
    assert "disabled in config (advisor)" in job.enabled_reason
    assert "ai" in job.tags
    assert job.trading_days_only
    assert job.kind == "daily"
    assert job.at_et == "17:00"
    assert "advisor_checkpoint.py" in " ".join(job.argv)

    light = [j for j in by_id if j.startswith("advisor-") and j != "advisor-deep"]
    assert light == [], f"no light checkpoint should derive by default, got {light}"


def test_configured_light_checkpoints_still_derive():
    """Dropping the DEFAULT must not drop the capability. An empty `checkpoints` derives no light
    jobs; a populated one derives them exactly as before, named by the dict form."""
    cfg = suite_cfg()
    cfg["advisor"] = {"enabled": True, "checkpoints": {"midday": "12:30", "close": "15:30"}}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert by_id["advisor-midday"].at_et == "12:30"
    assert by_id["advisor-close"].at_et == "15:30"
    assert by_id["advisor-deep"].at_et == "17:00"


def test_the_scheduled_scripts_resolve_to_files_that_exist():
    """`scripts/` sits at the repo root, two levels above the launcher. A path that points at a
    directory which has never existed spawns nothing and reports success — which is exactly how the
    narrative's broken path went unnoticed until the advisor was about to inherit it."""
    import os

    from cherrypick.orchestrator.jobspec import _advisor_script, _narrative_script

    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
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
    cfg["advisor"] = {
        "enabled": True,
        "light_model": "haiku",
        "deep_model": "opus",
        "checkpoints": {"open": "09:45"},
        "modules": {"flies": {"enabled": True}},
    }
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert "haiku" in by_id["advisor-open"].argv
    assert "opus" in by_id["advisor-deep"].argv
    # Only the modules the suite enabled for the advisor are passed through.
    modules = by_id["advisor-open"].argv[by_id["advisor-open"].argv.index("--modules") + 1]
    assert set(modules.split(",")) == {"meic", "flies", "earnings"}


def test_a_missed_light_checkpoint_goes_stale_but_a_missed_deep_run_does_not():
    """A light slot describes the session as it stands; the deep slot issues tomorrow's advice, so
    it stays worth firing until late evening."""
    cfg = suite_cfg()
    cfg["advisor"] = {"checkpoints": {"open": "09:45"}}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert by_id["advisor-open"].catchup_minutes == 45
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


def test_named_checkpoints_carry_their_own_slot_names():
    """The dict config form: {slot_name: time}. Added when the schedule was cut to one light slot —
    a single-entry LIST would run the 12:30 checkpoint under the positional name "open", and a slot
    name that lies about its own hour is the legibility failure the positional naming comment
    warns about."""
    cfg = suite_cfg()
    cfg["advisor"] = {"enabled": True, "checkpoints": {"midday": "12:30"}, "deep_at": "17:00"}
    by_id = {j.id: j for j in derive(cfg)[0]}

    assert "advisor-midday" in by_id
    assert by_id["advisor-midday"].at_et == "12:30"
    assert "--slot midday" in " ".join(by_id["advisor-midday"].argv).replace('"', "")
    # None of the positional names exist as jobs when the dict names one slot.
    for gone in ("advisor-open", "advisor-am1", "advisor-am2", "advisor-pm1", "advisor-pm2", "advisor-close"):
        assert gone not in by_id
    assert by_id["advisor-deep"].at_et == "17:00"


def test_named_checkpoints_sort_by_time_not_by_name():
    cfg = suite_cfg()
    cfg["advisor"] = {"enabled": True, "checkpoints": {"pm1": "13:30", "open": "09:45"}}
    from cherrypick.orchestrator.config import advisor_settings

    av = advisor_settings(cfg)
    assert av["checkpoints"] == ["09:45", "13:30"]
    assert av["checkpoint_slots"] == ["open", "pm1"]


def test_list_checkpoints_keep_positional_names():
    """The original form is untouched: a list is named positionally and checkpoint_slots is None."""
    from cherrypick.orchestrator.config import advisor_settings

    av = advisor_settings({"advisor": {"checkpoints": ["09:45", "10:30"]}})
    assert av["checkpoints"] == ["09:45", "10:30"]
    assert av["checkpoint_slots"] is None


def test_the_dolt_pull_lands_before_the_forward_scan_reads_it():
    """An ordering invariant, not a scheduling preference.

    The earnings module's 06:30 ET pre-market forward scan reads the Dolt earnings calendar to decide
    which names are even eligible that night, and bounds the entry universe from what it measures. A
    pull that lands at or after 06:30 means the scan walked YESTERDAY's calendar -- the same silent
    staleness this job exists to prevent, one day smaller instead of five weeks. Both sat at 06:30
    for a day, which also had them contending for Dolt while the clones were being rewritten.

    Pinned because nothing else can see it: both jobs succeed, the scan reports a clean pass, and the
    only symptom is a forward scan quietly measuring a stale universe.
    """
    jobs, errors = derive(suite_cfg())
    assert errors == {}
    pull = {j.id: j for j in jobs}["earnings-dolt-pull"]

    # The scan's own time, as the earnings module defaults it (symbol_watch.at, "06:30").
    scan_minutes = 6 * 60 + 30
    hour, minute = (int(x) for x in pull.at_et.split(":"))
    assert hour * 60 + minute < scan_minutes, (
        f"the pull runs at {pull.at_et} ET, at or after the 06:30 forward scan that reads it -- "
        "the scan would walk the previous day's calendar"
    )


# --------------------------------------------------------------------------- regime cuts (2026-09-19)
def _with_regime_cuts(cfg, module, at="16:40"):
    cfg["modules"][module]["paper"]["regime_cuts_at"] = at
    cfg["modules"][module]["paper"]["regime_cuts_argv"] = [
        "-m",
        f"cherrypick.{module}.regime_cuts",
        "--write",
    ]
    return cfg


def test_regime_cuts_job_is_derived_from_the_module_own_declaration():
    """Shown to fail with the declaration removed from the fixture: the job exists only because the
    module's paper block names a time and an argv, so a third module is covered the moment it
    declares them and one without a regime substrate never grows a job."""
    cfg = _with_regime_cuts(_with_regime_cuts(suite_cfg(), "flies"), "meic", at="16:45")
    jobs, errors = derive(cfg)
    assert errors == {}
    by_id = {j.id: j for j in jobs}
    flies = by_id["flies-regime-cuts"]
    assert flies.argv == ("pythonw", "-m", "cherrypick.flies.regime_cuts", "--write")
    assert flies.kind == jobspec.KIND_DAILY and flies.at_et == "16:40" and flies.trading_days_only
    assert flies.catchup_minutes == jobspec.CATCHUP_MINUTES["regime-cuts"]
    assert flies.cwd.endswith("flies")
    assert by_id["meic-regime-cuts"].at_et == "16:45"


def test_regime_cuts_job_is_absent_when_a_module_does_not_declare_it():
    jobs, _ = derive(suite_cfg())
    assert not [j.id for j in jobs if j.id.endswith("-regime-cuts")]
    # a time without an argv is not a declaration either
    cfg = suite_cfg()
    cfg["modules"]["flies"]["paper"]["regime_cuts_at"] = "16:40"
    jobs, _ = derive(cfg)
    assert "flies-regime-cuts" not in {j.id for j in jobs}


def _with_selector_fit(cfg, module="flies", at="16:45"):
    cfg["modules"][module]["paper"]["selector_fit_at"] = at
    cfg["modules"][module]["paper"]["selector_fit_argv"] = [
        "-m",
        "cherrypick.flies.cli",
        "selector-fit",
        "--write",
    ]
    return cfg


def test_selector_fit_job_is_derived_from_the_module_own_declaration():
    """The job exists only because flies' paper block names a time and an argv -- shown to fail
    with the declaration removed. Trading days only, after the 16:40 cuts, and it catches up no
    later than midnight, because past it the scheduler would read a new day's job."""
    jobs, errors = derive(_with_selector_fit(suite_cfg()))
    assert errors == {}
    job = {j.id: j for j in jobs}["flies-selector-fit"]
    assert job.argv == ("pythonw", "-m", "cherrypick.flies.cli", "selector-fit", "--write")
    assert job.kind == jobspec.KIND_DAILY and job.at_et == "16:45" and job.trading_days_only
    assert job.cwd.endswith("flies")
    h, m = (int(x) for x in job.at_et.split(":"))
    assert h * 60 + m + job.catchup_minutes <= 24 * 60, "a catch-up past midnight is a different day's job"


def test_selector_fit_job_is_absent_without_both_keys():
    assert "flies-selector-fit" not in {j.id for j in derive(suite_cfg())[0]}
    cfg = suite_cfg()
    cfg["modules"]["flies"]["paper"]["selector_fit_at"] = "16:45"
    assert "flies-selector-fit" not in {j.id for j in derive(cfg)[0]}


def _with_fee_reconcile(cfg, module="flies", at="09:15"):
    cfg["modules"][module]["paper"]["fee_reconcile_at"] = at
    cfg["modules"][module]["paper"]["fee_reconcile_argv"] = [
        "-m",
        f"cherrypick.{module}.fee_reconcile",
        "--symbol",
        "SPX",
    ]
    return cfg


def test_fee_reconcile_job_runs_whether_or_not_live_trading_is_armed():
    """The reason this job exists at all. Reconciliation used to ride the live tick, which only ran
    under `--live`, sat behind the dead-man's switch, and stopped entirely once the loop disarmed --
    so a session traded on Tuesday was never confirmed against broker cash if Wednesday was never
    armed. Shown to fail before the job existed. Note there is no `enabled`/arming condition here,
    unlike the `flies-live` job derived above; that absence IS the fix."""
    cfg = _with_fee_reconcile(suite_cfg())
    jobs, errors = derive(cfg)
    assert errors == {}
    job = {j.id: j for j in jobs}["flies-fee-reconcile"]
    assert job.argv == ("pythonw", "-m", "cherrypick.flies.fee_reconcile", "--symbol", "SPX")
    assert job.kind == jobspec.KIND_DAILY and job.at_et == "09:15" and job.trading_days_only
    assert job.catchup_minutes == jobspec.CATCHUP_MINUTES["fee-reconcile"]
    assert job.enabled is True, "never gated on arming -- that was the defect"
    assert job.cwd.endswith("flies")


def test_fee_reconcile_is_declared_per_module_not_hardcoded_for_flies():
    """Config-driven rather than a flies special case, which is how bwb -- which carried the
    identical defect -- gets the identical fix by declaring two keys and nothing else."""
    cfg = _with_fee_reconcile(_with_fee_reconcile(suite_cfg()), "meic", at="09:20")
    jobs, errors = derive(cfg)
    assert errors == {}
    by_id = {j.id: j for j in jobs}
    assert by_id["meic-fee-reconcile"].at_et == "09:20"
    assert by_id["meic-fee-reconcile"].argv[1:3] == ("-m", "cherrypick.meic.fee_reconcile")
    assert by_id["flies-fee-reconcile"].at_et == "09:15"
    assert all(by_id[k].enabled is True for k in ("meic-fee-reconcile", "flies-fee-reconcile"))


def test_every_module_with_a_live_ledger_declares_the_reconcile_job():
    """Driven off the shipped example rather than a hand-kept list. flies and bwb are the two
    modules that place live orders, and reconciliation must not depend on either being armed --
    a live path whose cash is never confirmed against the broker is the defect this guards."""
    import json
    from pathlib import Path

    example = json.loads(
        (Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8")
    )
    for module in ("flies", "bwb"):
        paper = example["modules"][module]["paper"]
        assert paper.get("fee_reconcile_at"), f"{module} declares no fee_reconcile_at"
        argv = paper.get("fee_reconcile_argv") or []
        assert argv[:2] == ["-m", f"cherrypick.{module}.fee_reconcile"], argv


def test_fee_reconcile_job_is_absent_when_a_module_does_not_declare_it():
    jobs, _ = derive(suite_cfg())
    assert not [j.id for j in jobs if j.id.endswith("-fee-reconcile")]
    cfg = suite_cfg()
    cfg["modules"]["flies"]["paper"]["fee_reconcile_at"] = "09:15"
    jobs, _ = derive(cfg)
    assert "flies-fee-reconcile" not in {j.id for j in jobs}


# --------------------------------------------------------------------------- vendor collector (2026-09-27)
def test_vendor_collector_jobs_are_off_until_a_login_is_stored():
    """Each job signs in to a third-party site; on by default they would fail every morning on a
    fresh box and notify about a login nobody has stored."""
    jobs, _ = derive(suite_cfg())
    by_id = {j.id: j for j in jobs}
    for job_id in ("report-edition", "report-edition-retry", "report-charts", "report-session-alert"):
        assert not by_id[job_id].enabled
        assert "market_report.collector" in by_id[job_id].enabled_reason


def test_vendor_collector_runs_after_publication_and_after_the_close():
    """The edition is published ~06:00-06:20 ET; a fetch before 06:30 finds yesterday's at the top
    and saves nothing. The chart data runs 20 minutes behind, so a capture before 16:20 files the
    previous session under today's panel."""
    cfg = suite_cfg()
    cfg["market_report"] = {
        "collector": True,
        "vendor_dashboard_url": "https://example.invalid/dashboard",
        "vendor_edition_title": "Example Report",
    }
    jobs, errors = derive(cfg)
    assert errors == {}
    by_id = {j.id: j for j in jobs}

    def minutes(job_id):
        h, m = (int(x) for x in by_id[job_id].at_et.split(":"))
        return h * 60 + m

    assert all(by_id[j].enabled and by_id[j].trading_days_only for j in ("report-edition", "report-charts"))
    assert minutes("report-edition") >= 6 * 60 + 30
    assert minutes("report-edition-retry") > minutes("report-edition")
    assert minutes("report-charts") >= 16 * 60 + 20
    assert by_id["report-edition"].argv[-1] == "edition"
    assert by_id["report-charts"].argv[-1] == "charts"
    alert = by_id["report-session-alert"]
    assert alert.enabled and alert.argv[-1] == "session-alert"
    assert not alert.trading_days_only  # an outage that starts Friday evening still reminds on Saturday
    assert alert.interval_seconds <= 15 * 60  # it bounds how late an hourly reminder can be


def test_the_collector_never_runs_without_its_generic_vendor_keys():
    """Nothing that names the vendor is in the code, so switched on without its address or its
    report's title the collector has nothing to run against: no job fires, and the reason says
    which key is missing."""
    for missing in ("vendor_dashboard_url", "vendor_edition_title"):
        cfg = suite_cfg()
        cfg["market_report"] = {
            "collector": True,
            "vendor_dashboard_url": "https://example.invalid/dashboard",
            "vendor_edition_title": "Example Report",
        }
        cfg["market_report"][missing] = "  "
        by_id = {j.id: j for j in derive(cfg)[0]}
        for job_id in ("report-edition", "report-edition-retry", "report-charts"):
            assert by_id[job_id].enabled is False, job_id
            assert missing in by_id[job_id].enabled_reason


def test_the_liquidity_verdict_runs_after_its_inputs_and_before_its_readers():
    """It judges from tonight's IV-rank fetch (weekly expiries), and the 19:00 screener greeks run
    skips by it; out of that order, every reader would act on yesterday's verdict."""
    cfg = suite_cfg()
    by_id = {j.id: j for j in derive(cfg)[0]}

    def minutes(at):
        h, m = (int(x) for x in at.split(":"))
        return h * 60 + m

    job = by_id["technicals-liquidity"]
    assert job.argv[-1] == "liquidity" and job.trading_days_only
    assert minutes(job.at_et) > minutes(by_id["technicals-iv-rank"].at_et)
    assert minutes(job.at_et) < minutes(by_id["report-screener-greeks"].at_et)


def test_screener_greeks_is_its_own_switch_and_runs_after_the_chart_capture():
    """It reads the shared broker credential, so it is off until switched on, even with the
    collector on; and it records the lists the chart capture saves, so it runs after it."""
    cfg = suite_cfg()
    cfg["market_report"] = {
        "collector": True,
        "vendor_dashboard_url": "https://example.invalid/dashboard",
        "vendor_edition_title": "Example Report",
    }
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert not by_id["report-screener-greeks"].enabled
    assert "market_report.screener_greeks" in by_id["report-screener-greeks"].enabled_reason
    cfg["market_report"]["screener_greeks"] = True
    by_id = {j.id: j for j in derive(cfg)[0]}
    job = by_id["report-screener-greeks"]
    assert job.enabled and job.trading_days_only
    assert job.argv[-1].endswith("fetch_screener_greeks.py")

    def minutes(at):
        h, m = (int(x) for x in at.split(":"))
        return h * 60 + m

    assert minutes(job.at_et) >= minutes(by_id["report-charts"].at_et) + 60
    # The spread readings: regular hours, inside the measuring window, never on top of the
    # universe builder's own readings.
    for i in (1, 2):
        m = by_id[f"report-screener-measure-{i}"]
        assert m.enabled and m.trading_days_only and m.argv[-1] == "measure"
        assert 10 * 60 <= minutes(m.at_et) <= 15 * 60 + 30
        assert m.at_et not in {by_id["universe-measure-1"].at_et, by_id["universe-measure-2"].at_et}


# --------------------------------------------------------------------------- stock universe (2026-09-27)
UNIVERSE_JOBS = ("universe-measure-1", "universe-measure-2", "universe-daily")


def test_universe_jobs_are_off_until_switched_on():
    jobs, _ = derive(suite_cfg())
    by_id = {j.id: j for j in jobs}
    for job_id in UNIVERSE_JOBS:
        assert not by_id[job_id].enabled
        assert "market_report.universe" in by_id[job_id].enabled_reason


def test_universe_measurements_sit_inside_regular_hours_and_the_harvest_after_the_charts():
    """A measurement outside 10:00-15:30 ET is refused by the script, so a slot out there is a job
    that runs every day and records nothing. The harvest reads the day's editions and feed, so it
    runs after the vendor's evening chart capture."""
    cfg = suite_cfg()
    cfg["market_report"] = {"universe": True}
    jobs, errors = derive(cfg)
    assert errors == {}
    by_id = {j.id: j for j in jobs}

    def minutes(job_id):
        h, m = (int(x) for x in by_id[job_id].at_et.split(":"))
        return h * 60 + m

    assert all(by_id[j].enabled and by_id[j].trading_days_only for j in UNIVERSE_JOBS)
    for job_id in ("universe-measure-1", "universe-measure-2"):
        assert (
            10 * 60 <= minutes(job_id) and minutes(job_id) + jobspec.CATCHUP_MINUTES[job_id] <= 15 * 60 + 30
        )
        assert by_id[job_id].argv[-1] == "measure"
    assert minutes("universe-daily") > minutes("report-charts")
    assert by_id["universe-daily"].argv[-1] == "daily"


def test_the_watchlist_sync_needs_its_own_switch():
    """The one scheduled write to the broker account: switching the universe on must not also
    switch this on."""
    cfg = suite_cfg()
    cfg["market_report"] = {"universe": True}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert not by_id["universe-watchlist"].enabled
    assert "market_report.universe_watchlist" in by_id["universe-watchlist"].enabled_reason

    cfg["market_report"] = {"universe_watchlist": True}
    assert not {j.id: j for j in derive(cfg)[0]}["universe-watchlist"].enabled


def test_the_watchlist_sync_runs_after_the_rebuild_and_applies():
    cfg = suite_cfg()
    cfg["market_report"] = {"universe": True, "universe_watchlist": True}
    by_id = {j.id: j for j in derive(cfg)[0]}
    job = by_id["universe-watchlist"]
    assert job.enabled and job.argv[-2:] == ("watchlist", "--apply")
    assert job.at_et > by_id["universe-daily"].at_et


# --------------------------------------------------------------------------- market files (2026-09-27)
def test_market_files_are_fetched_after_treasury_posts_and_retried_before_the_pack():
    """Treasury posts its curve by ~18:00 ET, so an evening fetch before that reads yesterday's; the
    retry exists to land a missed fetch before the 08:30 pack, so it must run before the pack and
    stop catching up once the pack has been built."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}

    def minutes(at):
        h, m = (int(x) for x in at.split(":"))
        return h * 60 + m

    evening, retry, pack = by_id["market-files"], by_id["market-files-retry"], by_id["morning-factpack"]
    assert evening.enabled and retry.enabled, "credential-free: on with the pack"
    assert minutes(evening.at_et) >= 18 * 60
    assert minutes(retry.at_et) + retry.catchup_minutes <= minutes(pack.at_et)
    assert evening.argv[-1] == "fetch" and evening.trading_days_only


def test_market_files_follow_the_morning_switch():
    cfg = suite_cfg()
    cfg["morning"] = {"enabled": False}
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert not by_id["market-files"].enabled


# --------------------------------------------------------------------------- technicals (2026-09-27)
def test_the_technicals_landing_follows_the_dolt_pull_and_needs_the_dolt_server():
    """It reads the local dolt sql-server, which only the earnings module keeps alive, and the
    clones it reads are pulled at 05:30 -- a landing before the pull lands yesterday's data."""
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    land, pull = by_id["technicals-land"], by_id["earnings-dolt-pull"]

    def minutes(at):
        h, m = (int(x) for x in at.split(":"))
        return h * 60 + m

    assert minutes(land.at_et) > minutes(pull.at_et)
    assert land.argv[1:] == ("-m", "cherrypick.technicals", "land")
    cfg = suite_cfg()
    cfg.setdefault("modules", {}).setdefault("earnings", {})["enabled"] = False
    off = {j.id: j for j in derive(cfg)[0]}["technicals-land"]
    assert not off.enabled and "dolt" in off.enabled_reason


def test_the_dividend_fetch_is_a_script_on_by_default():
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    job = by_id["technicals-dividends"]
    assert job.enabled and job.argv[-1].endswith("fetch_dividends.py")
    cfg = suite_cfg()
    cfg["technicals"] = {"dividends": False}
    assert not {j.id: j for j in derive(cfg)[0]}["technicals-dividends"].enabled


def test_the_index_bar_fetch_runs_before_the_landing_that_reads_it():
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    job = by_id["technicals-index-bars"]
    assert job.enabled and job.argv[-1].endswith("fetch_index_bars.py")
    assert job.at_et < by_id["technicals-land"].at_et, "the landing must find the previous session's bar"
    cfg = suite_cfg()
    cfg["technicals"] = {"index_bars": False}
    assert not {j.id: j for j in derive(cfg)[0]}["technicals-index-bars"].enabled


def test_earnings_moves_are_priced_after_the_close_and_before_the_pack():
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    job, pack = by_id["earnings-moves"], by_id["morning-factpack"]

    def minutes(at):
        h, m = (int(x) for x in at.split(":"))
        return h * 60 + m

    assert job.enabled and job.trading_days_only and minutes(job.at_et) >= 16 * 60 + 15
    assert job.argv[-1].endswith("fetch_earnings_moves.py")
    assert minutes(pack.at_et) < minutes(job.at_et), "an evening job the next morning's pack reads"


def test_the_technicals_report_follows_the_landing():
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    land, rep = by_id["technicals-land"], by_id["technicals-report"]
    assert rep.argv[1:] == ("-m", "cherrypick.technicals", "report")
    assert rep.at_et > land.at_et and rep.enabled == land.enabled


def test_headlines_are_fetched_before_the_narrative_reads_them():
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    job, note = by_id["fetch-headlines"], by_id["morning-narrative"]
    assert job.enabled and job.trading_days_only
    assert job.argv[-1].endswith("fetch_headlines.py")
    assert job.at_et < note.at_et, "the narrative must find this morning's file"


# --------------------------------------------------------------------------- QuikOptions (2026-10-03)
QUIKOPTIONS_JOBS = (
    "quikoptions-capture",
    "quikoptions-score",
    "quikoptions-post",
    "quikoptions-confirm",
    "quikoptions-morning",
    "quikoptions-weekly",
)


def _qo(**block):
    cfg = suite_cfg()
    cfg["quikoptions"] = block
    jobs, errors = derive(cfg)
    assert errors == {}
    return {j.id: j for j in jobs}


def test_quikoptions_jobs_are_off_until_the_capture_is_switched_on():
    by_id = _qo()
    for job_id in QUIKOPTIONS_JOBS:
        assert not by_id[job_id].enabled, job_id
        assert "quikoptions." in by_id[job_id].enabled_reason


def test_the_quikoptions_series_needs_its_own_switch_and_each_follow_up_its_own():
    by_id = _qo(enabled=True)
    assert all(by_id[j].enabled for j in ("quikoptions-capture", "quikoptions-score", "quikoptions-confirm"))
    for job_id in ("quikoptions-post", "quikoptions-morning", "quikoptions-weekly"):
        assert not by_id[job_id].enabled
        assert "quikoptions.post" in by_id[job_id].enabled_reason
    # `post` alone does nothing without the capture.
    assert not _qo(post=True)["quikoptions-post"].enabled

    by_id = _qo(enabled=True, post=True, post_weekly=False)
    assert by_id["quikoptions-post"].enabled and by_id["quikoptions-morning"].enabled
    assert not by_id["quikoptions-weekly"].enabled
    assert "post_weekly" in by_id["quikoptions-weekly"].enabled_reason


def test_quikoptions_runs_in_order_and_each_step_waits_for_the_one_before():
    """The report lands 30-60 minutes after the close; the scheduler fires each job once a day, so a
    step that cannot wait for its input would be done for the day after a wake fired them together."""
    by_id = _qo(enabled=True, post=True)

    def minutes(job_id):
        h, m = (int(x) for x in by_id[job_id].at_et.split(":"))
        return h * 60 + m

    order = ("quikoptions-capture", "quikoptions-score", "quikoptions-post")
    assert [minutes(j) for j in order] == sorted(minutes(j) for j in order)
    assert 16 * 60 + 30 <= minutes("quikoptions-capture") and minutes("quikoptions-post") <= 17 * 60
    assert minutes("quikoptions-confirm") < minutes("quikoptions-morning") < 9 * 60 + 30
    assert all(by_id[j].trading_days_only and by_id[j].catchup_minutes for j in QUIKOPTIONS_JOBS)
    for job_id in ("quikoptions-score", "quikoptions-post", "quikoptions-morning"):
        argv = by_id[job_id].argv
        assert argv[argv.index("--wait") + 1] == str(jobspec.QUIKOPTIONS_WAIT_MINUTES), job_id
    assert "--require-today" in by_id["quikoptions-score"].argv


def test_quikoptions_jobs_run_the_suite_scripts_with_the_configured_capture():
    by_id = _qo(enabled=True, post=True, headed=False, jitter_minutes=5, at="16:40")
    capture = by_id["quikoptions-capture"]
    assert capture.argv[1].replace("\\", "/").endswith("scripts/fetch_quikoptions.py")
    assert capture.argv[2:] == ("hot-options", "--jitter", "5", "--headless")
    assert capture.at_et == "16:40"
    # Headless unless asked (2026-10-08): the default passes --headless; headed=true is the opt-in.
    assert "--headless" in _qo(enabled=True)["quikoptions-capture"].argv
    assert "--headless" not in _qo(enabled=True, headed=True)["quikoptions-capture"].argv
    assert by_id["quikoptions-score"].argv[1].endswith("quikoptions_flow.py")
    assert by_id["quikoptions-confirm"].argv[2:] == ("confirm",)
    assert by_id["quikoptions-post"].argv[1].endswith("quikoptions_post.py")
    assert by_id["quikoptions-morning"].argv[2:4] == ("--kind", "morning")
    assert by_id["quikoptions-weekly"].argv[2:] == ("--kind", "weekly")


def test_flies_payoff_intraday_is_its_own_job_off_by_default():
    by_id = {j.id: j for j in derive(suite_cfg())[0]}
    job = by_id["flies-payoff-intraday"]
    assert not job.enabled
    assert job.enabled_reason == "disabled in config (flies_payoff_post.intraday)"
    assert "--intraday" in job.argv
    assert (job.window_start, job.window_end, job.interval_seconds) == ("10:01", "15:30", 3600)
    # The after-bell post never carries the flag.
    assert "--intraday" not in by_id["flies-payoff-post"].argv


def test_flies_payoff_intraday_runs_without_the_settled_post():
    cfg = suite_cfg(flies_payoff_post={"enabled": False, "modes": ["paper"], "intraday": {"enabled": True}})
    by_id = {j.id: j for j in derive(cfg)[0]}
    assert by_id["flies-payoff-intraday"].enabled
    assert not by_id["flies-payoff-post"].enabled
    assert by_id["flies-payoff-intraday"].argv[-2:] == ("--mode", "paper")
