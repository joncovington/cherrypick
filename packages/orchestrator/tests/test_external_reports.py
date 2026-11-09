"""External reports (orchestrator/external_reports.py): entries become jobs, a script must live under
the suite home, and a valid report is posted to Discord once per `as_of` -- never twice, never when
invalid. No network: the webhook sender and the keyring lookup are replaced."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cherrypick.cli import build_parser
from cherrypick.orchestrator import doctor, init, jobspec
from cherrypick.orchestrator import external_reports as ext


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "suite-home"
    (root / "scripts").mkdir(parents=True)
    (root / "data" / "external").mkdir(parents=True)
    (root / "state").mkdir()
    monkeypatch.setenv("CHERRYPICK_HOME", str(root))
    return root


def _derive(cfg):
    base = {"timezone": "America/New_York", "modules": {}}
    return jobspec.derive_jobs(
        {**base, **cfg},
        pythonw="pythonw",
        launcher="run.py",
        now=datetime(2026, 11, 9, 12, 0, tzinfo=ZoneInfo("America/New_York")),
        include_retired=True,
    )


def _entry(home, **over):
    script = home / "scripts" / "report.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    return {"id": "vix-history", "script": str(script), "at_et": "17:30", **over}


def _doc(as_of="2026-11-09T17:30:00-05:00", rows=3, **over):
    doc = {
        "schema": 1,
        "id": "vix-history",
        "title": "VIX history",
        "source": "my own script",
        "as_of": as_of,
        "stale_after_hours": 36,
        "summary": "Last sessions",
        "sections": [
            {
                "heading": "Closes",
                "table": {"columns": ["date", "close"], "rows": [[f"d{i}", i * 1.5] for i in range(rows)]},
            }
        ],
    }
    doc.update(over)
    return doc


def _write(home, doc):
    path = home / "data" / "external" / f"{doc['id']}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------- jobs
def test_an_entry_becomes_a_daily_job_run_by_the_launcher(home):
    jobs, errors = _derive({"external_reports": [_entry(home, post="reporting")]})
    assert errors == {}
    job = next(j for j in jobs if j.id == "external-vix-history")
    assert job.enabled and job.kind == jobspec.KIND_DAILY and job.at_et == "17:30"
    assert job.trading_days_only is True  # the default
    assert job.argv == ("pythonw", "run.py", "external-run", "vix-history")
    assert "network" in job.tags and job.catchup_minutes == ext.CATCHUP_MINUTES


def test_trading_days_only_can_be_turned_off(home):
    jobs, _ = _derive({"external_reports": [_entry(home, trading_days_only=False)]})
    assert next(j for j in jobs if j.id == "external-vix-history").trading_days_only is False


def test_a_script_outside_the_home_is_refused_with_a_reason(home, tmp_path):
    outside = tmp_path / "elsewhere.py"
    outside.write_text("print('x')\n", encoding="utf-8")
    jobs, errors = _derive({"external_reports": [_entry(home, script=str(outside))]})
    job = next(j for j in jobs if j.id == "external-vix-history")
    assert not job.enabled
    assert "outside the suite home" in job.enabled_reason
    resolved, why = ext.script_problem("scripts/../../escape.py")
    assert resolved is None and "outside the suite home" in why


def test_a_relative_script_is_read_from_the_home(home):
    resolved, why = ext.script_problem("scripts/report.py")
    assert why is None and resolved == (home / "scripts" / "report.py").resolve()


def test_a_disabled_entry_runs_nothing(home):
    jobs, _ = _derive({"external_reports": [_entry(home, enabled=False)]})
    assert not [j for j in jobs if j.id.startswith("external-") and j.enabled]


def test_no_entries_means_no_external_jobs(home):
    jobs, errors = _derive({})
    assert not [j for j in jobs if j.id.startswith("external-")] and errors == {}


@pytest.mark.parametrize(
    "bad, said",
    [
        ({"id": "Bad Id"}, "lowercase"),
        ({"at_et": "5pm"}, "HH:MM"),
        ({"post": "slack"}, "post must be"),
    ],
)
def test_a_malformed_entry_says_why_and_makes_no_running_job(home, bad, said):
    cfg = {"external_reports": [_entry(home, **bad)]}
    entry = ext.entries(cfg)[0]
    assert said in entry["problem"]
    jobs, errors = _derive(cfg)
    assert not [j for j in jobs if j.id.startswith("external-") and j.enabled]
    assert errors or any(not j.enabled for j in jobs if j.id.startswith("external-"))


def test_the_derived_argv_is_one_the_cli_accepts(home):
    jobs, _ = _derive({"external_reports": [_entry(home)]})
    job = next(j for j in jobs if j.id == "external-vix-history")
    args, extra = build_parser().parse_known_args(list(job.argv[2:]))
    assert (args.command, args.name, extra) == ("external-run", "vix-history", [])


def test_config_validation_reports_a_refused_entry(home, tmp_path):
    cfg = {"modules": {}, "external_reports": [_entry(home, script=str(tmp_path / "x.py"))]}
    errors = [m for lvl, m in init.validate_config(cfg) if lvl == "error"]
    assert any("outside the suite home" in m for m in errors)


# ------------------------------------------------------------------------- the removed quikoptions block
def test_doctor_warns_once_about_a_leftover_quikoptions_block():
    out = doctor.removed_config_checks({"quikoptions": {"enabled": True}})
    assert len(out) == 1 and out[0].status == doctor.WARN
    assert "removed in v0.12.0" in out[0].detail and "external reports" in out[0].detail


def test_doctor_is_silent_without_the_block():
    assert doctor.removed_config_checks({"modules": {}}) == []


def test_the_leftover_block_is_ignored_when_deriving_jobs(home):
    jobs, errors = _derive({"quikoptions": {"enabled": True, "post": True}})
    assert errors == {} and not [j for j in jobs if "quikoptions" in j.id]


# ----------------------------------------------------------------------------------------- posting
class _Sender:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, url, payload, **kw):
        self.calls.append((url, payload, kw))
        return {"ok": self.ok, "error": None if self.ok else "HTTP 500"}


def _hook(channel):
    return f"https://example.invalid/{channel}"


def _post(home, sender, post="reporting"):
    entry = {"id": "vix-history", "post": post}
    return ext.post_if_new(entry, sender=sender, webhook=_hook)


def test_a_new_as_of_is_posted_once_and_not_again(home):
    _write(home, _doc())
    sender = _Sender()
    first = _post(home, sender)
    again = _post(home, sender)
    assert first["posted"] is True
    assert again["posted"] is False and "already posted" in again["why"]
    assert len(sender.calls) == 1
    url, payload, kw = sender.calls[0]
    assert url.endswith("discord_reporting") and kw["channel"] == "discord_reporting"
    embed = payload["embeds"][0]
    assert embed["title"] == "VIX history"
    assert "my own script" in embed["description"] and "2026-11-09T17:30:00-05:00" in embed["description"]
    assert "Last sessions" in embed["description"]


def test_a_later_as_of_posts_again(home):
    sender = _Sender()
    _write(home, _doc())
    _post(home, sender)
    _write(home, _doc(as_of="2026-11-10T17:30:00-05:00"))
    assert _post(home, sender)["posted"] is True
    assert len(sender.calls) == 2


def test_notify_posts_to_the_suite_webhook(home):
    _write(home, _doc())
    sender = _Sender()
    _post(home, sender, post="notify")
    assert sender.calls[0][2]["channel"] == "discord"


def test_an_invalid_report_posts_nothing(home):
    _write(home, _doc(title=""))
    sender = _Sender()
    out = _post(home, sender)
    assert out["posted"] is False and "invalid report" in out["why"]
    assert sender.calls == []


def test_a_missing_report_posts_nothing(home):
    sender = _Sender()
    assert _post(home, sender)["posted"] is False and sender.calls == []


def test_a_failed_send_is_retried_next_time(home):
    _write(home, _doc())
    bad = _Sender(ok=False)
    assert _post(home, bad)["posted"] is False
    good = _Sender()
    assert _post(home, good)["posted"] is True


def test_no_stored_webhook_posts_nothing(home):
    _write(home, _doc())
    sender = _Sender()
    out = ext.post_if_new({"id": "vix-history", "post": "reporting"}, sender=sender, webhook=lambda c: None)
    assert out["posted"] is False and "webhook" in out["why"] and sender.calls == []


def test_the_first_table_is_cut_to_the_embed_limits_and_says_how_many_rows_are_left():
    doc = _doc(rows=400)
    embed = ext.card_payload(doc)["embeds"][0]
    assert len(embed["description"]) <= 4000 and len(embed["title"]) <= 256
    shown = embed["description"].count("\nd") - 1  # the header line starts with "date"
    assert 0 < shown <= ext.MAX_CARD_ROWS
    assert f"…and {400 - shown} more rows" in embed["description"]


def test_a_short_table_is_shown_whole_without_a_more_line():
    embed = ext.card_payload(_doc(rows=2))["embeds"][0]
    assert "more row" not in embed["description"] and "d1" in embed["description"]


def test_the_discord_card_ignores_the_bars_hint():
    plain = _doc()
    barred = _doc()
    barred["sections"][0]["table"]["bars"] = [1]
    assert ext.card_payload(barred) == ext.card_payload(plain)


def test_a_wide_cell_is_shortened():
    doc = _doc()
    doc["sections"][0]["table"]["rows"][0][0] = "x" * 150
    assert "x" * 150 not in ext.card_payload(doc)["embeds"][0]["description"]


# ------------------------------------------------- a script that does its own work (args, no report)
def test_args_timeout_and_writes_report_reach_the_run(home):
    cfg = _cfg(home, args=["score", "--wait", "45"], timeout_minutes=60, writes_report=False, post=None)
    seen = {}

    def runner(argv, **kw):
        seen["argv"], seen["timeout"] = argv, kw["timeout"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    out = ext.run(cfg, "vix-history", runner=runner)
    assert out["ok"] is True  # no report is written or judged
    assert seen["argv"][2:] == ["score", "--wait", "45"] and seen["timeout"] == 3600


def test_a_step_without_a_report_still_fails_on_a_bad_exit(home):
    cfg = _cfg(home, writes_report=False)
    bad = lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="x")  # noqa: E731
    assert ext.run(cfg, "vix-history", runner=bad)["ok"] is False


@pytest.mark.parametrize(
    "bad, said",
    [
        ({"args": "score"}, "args must be a list"),
        ({"timeout_minutes": 0}, "timeout_minutes"),
        ({"timeout_minutes": 500}, "timeout_minutes"),
        ({"post": "reporting", "writes_report": False}, "post needs a report"),
    ],
)
def test_bad_args_timeout_or_post_without_a_report_is_refused(home, bad, said):
    assert said in ext.entries(_cfg(home, **bad))[0]["problem"]


def test_several_entries_may_run_one_script_at_different_times(home):
    one = _entry(home, id="qo-score", at_et="16:50", args=["score"], writes_report=False)
    two = _entry(home, id="qo-post", at_et="17:00", args=["post"], writes_report=False)
    jobs, errors = _derive({"external_reports": [one, two]})
    ids = {j.id: j.at_et for j in jobs if j.id.startswith("external-")}
    assert errors == {} and ids == {"external-qo-score": "16:50", "external-qo-post": "17:00"}


def test_retries_and_their_delay_reach_the_job(home):
    jobs, _ = _derive({"external_reports": [_entry(home, retries=2, retry_delay_minutes=1)]})
    job = next(j for j in jobs if j.id == "external-vix-history")
    assert (job.retries, job.retry_delay_seconds) == (2, 60)
    plain, _ = _derive({"external_reports": [_entry(home)]})
    job = next(j for j in plain if j.id == "external-vix-history")
    assert (job.retries, job.retry_delay_seconds) == (0, 600)  # off by default; ten minutes when on


@pytest.mark.parametrize(
    "bad",
    [{"retries": 4}, {"retries": -1}, {"retries": 1.5}, {"retries": True}, {"retry_delay_minutes": 0},
     {"retry_delay_minutes": 500}, {"retry_delay_minutes": "10"}],
)  # fmt: skip
def test_bad_retries_or_delay_is_refused_with_a_reason(home, bad):
    problem = ext.entries(_cfg(home, **bad))[0]["problem"]
    assert problem and ("retries" in problem or "retry_delay_minutes" in problem)


def test_a_timeout_or_an_invalid_report_exits_non_zero_so_the_supervisor_retries(home):
    import subprocess

    def timed_out(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    assert ext.run(_cfg(home, retries=1), "vix-history", runner=timed_out)["ok"] is False
    _write(home, _doc(title=""))
    fine = lambda argv, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")  # noqa: E731
    assert ext.run(_cfg(home, retries=1), "vix-history", runner=fine)["ok"] is False


# ----------------------------------------------------------------------------- external-check
def test_external_check_prints_the_card_and_exits_zero_for_a_valid_file(home, capsys):
    from cherrypick import cli

    path = _write(home, _doc(as_of=datetime.now().astimezone().isoformat(timespec="seconds")))
    with pytest.raises(SystemExit) as stop:
        cli.cmd_external_check(str(path))
    out = capsys.readouterr().out
    assert stop.value.code == 0
    assert "title: VIX history" in out and "state: fresh" in out and "Closes (table, 3 rows)" in out


def test_external_check_lists_every_reason_and_exits_one_for_an_invalid_file(home, capsys):
    from cherrypick import cli

    path = _write(home, _doc(title="", stale_after_hours=0))
    with pytest.raises(SystemExit) as stop:
        cli.cmd_external_check(str(path))
    out = capsys.readouterr().out
    assert stop.value.code == 1
    assert out.count("invalid: ") >= 2


def test_external_check_without_a_file_exits_two(capsys):
    from cherrypick import cli

    with pytest.raises(SystemExit) as stop:
        cli.cmd_external_check(None)
    assert stop.value.code == 2


# ------------------------------------------------------------------------------------------ the job
def _cfg(home, **over):
    return {"external_reports": [_entry(home, **over)]}


def test_run_executes_the_script_judges_the_file_and_posts(home, monkeypatch):
    path = _write(home, _doc())
    seen = []

    def runner(argv, **kw):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    posted = []
    monkeypatch.setattr(
        ext, "post_if_new", lambda entry, **kw: posted.append(entry["id"]) or {"posted": True}
    )
    out = ext.run(_cfg(home, post="reporting"), "vix-history", runner=runner)
    assert out["ok"] is True and posted == ["vix-history"] and path.exists()
    assert seen[0][1].endswith("report.py")


def test_run_fails_when_the_script_fails_or_the_report_is_invalid(home, monkeypatch):
    def failing(argv, **kw):
        return SimpleNamespace(returncode=3, stdout="", stderr="boom")

    out = ext.run(_cfg(home), "vix-history", runner=failing)
    assert out["ok"] is False and "exited 3" in out["error"]

    _write(home, _doc(stale_after_hours=-1))
    fine = lambda argv, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")  # noqa: E731
    out = ext.run(_cfg(home), "vix-history", runner=fine)
    assert out["ok"] is False and "invalid report" in out["error"]


def test_run_refuses_an_unknown_id_and_an_outside_script(home, tmp_path):
    assert ext.run(_cfg(home), "nope")["ok"] is False
    out = ext.run(_cfg(home, script=str(tmp_path / "x.py")), "vix-history")
    assert out["ok"] is False and "outside the suite home" in out["error"]
