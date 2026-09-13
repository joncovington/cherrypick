"""The fenced script, exercised end to end against a fake `claude` on PATH.

No paid call is made anywhere in this file. A shim binary stands in for `claude`, and it can be
asked to return good JSON, garbage, nothing, or to be absent entirely — which is the whole point:
the failure paths are the ones that must be right, because they are the ones that decide whether an
AI outage costs a day of advice or corrupts an experiment's sample.

The script lives in `scripts/`, deliberately outside every package, so it is loaded here by path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import fakes
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "advisor_checkpoint.py"
# Clock-derived: the deep slot enacts, and a literal date expires out from under that (see
# fakes.anchor_session). NEXT_SESSION is where the artifact it issues lands.
SESSION = fakes.anchor_session()
NEXT_SESSION = fakes.next_session(SESSION)
# Pinned, and correctly so: this test asserts the calendar gate SKIPS a non-trading day, and
# "2026-08-15 is a Saturday" is a permanent fact with no expiry in it to rot.
SATURDAY = "2026-08-15"

GOOD_REPLY = {
    "observations": ["control took no stops all session"],
    "flags": [],
    "proposals": [
        {
            "kind": "bounded_adjustment",
            "module": "meic",
            "sessions": 15,
            "hypothesis": "a wider trigger survives midday chop",
            "params": [{"param": "stop_trigger_ratio", "value": 0.9, "rationale": "wider"}],
        },
    ],
}


@pytest.fixture
def home(tmp_home):
    fakes.seed_suite(tmp_home, SESSION)
    fakes.write_config(
        tmp_home, "meic", fakes.advice_block({"stop_trigger_ratio": {"min": 0.85, "max": 0.95}})
    )
    fakes.write_suite_config(tmp_home, {"enabled": True, "modules": {"meic": {"enabled": True}}})
    return tmp_home


def _shim(directory: Path, behavior: str) -> Path:
    """A fake `claude` that reads stdin and does what `behavior` says.

    Written as a .py plus a .bat/.sh launcher because `shutil.which` has to find it under the name
    `claude`, with no extension of its own on POSIX and a PATHEXT-visible one on Windows.
    """
    directory.mkdir(parents=True, exist_ok=True)
    # Every behaviour records the argv it was invoked with beside itself, so a test can assert
    # the fence the script claims to put around the model (2026-09-12).
    record = (
        "import sys, json, pathlib; "
        "pathlib.Path(sys.argv[0]).with_name('argv.json').write_text(json.dumps(sys.argv[1:])); "
    )
    envelope = json.dumps(
        {"type": "result", "result": json.dumps(GOOD_REPLY), "modelUsage": {"claude-test-1": {}}}
    )
    body = {
        "good": f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(GOOD_REPLY))})",
        "json": f"import sys; sys.stdin.read(); print({json.dumps(envelope)})",
        "prose": (
            "import sys; sys.stdin.read(); "
            f"print('Here is what I found:'); print({json.dumps(json.dumps(GOOD_REPLY))})"
        ),
        "garbage": "import sys; sys.stdin.read(); print('I was unable to analyse this today.')",
        "silent": "import sys; sys.stdin.read()",
        "angry": "import sys; sys.stdin.read(); sys.stderr.write('rate limited'); sys.exit(1)",
    }[behavior]
    payload = directory / "claude_impl.py"
    payload.write_text(record + body, encoding="utf-8")

    if sys.platform == "win32":
        launcher = directory / "claude.bat"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{payload}" %*\r\n', encoding="utf-8")
    else:
        launcher = directory / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{payload}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def _run(*argv: str, shim: str | None = "good", tmp_path: Path | None = None) -> dict:
    env = dict(os.environ)
    if shim:
        shim_dir = (tmp_path or Path.cwd()) / "bin"
        _shim(shim_dir, shim)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    else:
        # No `claude` anywhere: PATH is emptied of everything but the interpreter's own directory.
        env["PATH"] = str(Path(sys.executable).parent)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, f"the script must always exit 0: {proc.stderr}"
    # --dry-run prints the prompt and pack rather than an envelope.
    if "--dry-run" in argv:
        return {"stdout": proc.stdout}
    return json.loads(proc.stdout)


def test_a_clean_reply_runs_the_whole_pipeline(home, tmp_path):
    from cherrypick.advisor import paths, store

    result = _run("--slot", "deep", "--session", SESSION, "--model", "opus", tmp_path=tmp_path)

    assert result["ok"] is True
    assert result["admitted"] == 1 and result["rejected"] == 0
    assert result["target_session"] == NEXT_SESSION
    assert paths.pack_path(SESSION, "deep").exists()
    assert paths.raw_path(SESSION, "deep").exists()
    assert paths.advice_path("meic", NEXT_SESSION).exists()

    conn = store.connect()
    assert store.experiments(conn, status="active")[0]["module"] == "meic"
    conn.close()


def test_json_wrapped_in_prose_still_admits(home, tmp_path):
    result = _run("--slot", "open", "--session", SESSION, shim="prose", tmp_path=tmp_path)
    assert result["ok"] is True and result["admitted"] == 1


def test_a_non_trading_day_never_reaches_the_model(home, tmp_path):
    """The calendar gate is first, before anything is built and before a paid call is possible."""
    from cherrypick.advisor import paths

    result = _run("--slot", "open", "--session", SATURDAY, tmp_path=tmp_path)
    assert result["skipped"] == "not a trading day"
    assert not paths.pack_path(SATURDAY, "open").exists()


def test_a_recorded_slot_is_frozen_until_forced(home, tmp_path):
    first = _run("--slot", "open", "--session", SESSION, tmp_path=tmp_path)
    assert first["ok"] is True

    again = _run("--slot", "open", "--session", SESSION, tmp_path=tmp_path)
    assert "frozen" in again["skipped"]

    forced = _run("--slot", "open", "--session", SESSION, "--force", tmp_path=tmp_path)
    assert forced["ok"] is True


@pytest.mark.parametrize(
    "shim,expected",
    [
        ("garbage", "no JSON object found"),
        ("silent", "claude returned nothing"),
        ("angry", "rate limited"),
        (None, "claude not on PATH"),
    ],
)
def test_every_ai_failure_is_an_envelope_not_a_crash(home, tmp_path, shim, expected):
    result = _run("--slot", "open", "--session", SESSION, shim=shim, tmp_path=tmp_path)
    assert result["ok"] is False
    assert expected in result["error"]


@pytest.mark.parametrize("shim", ["garbage", "silent", "angry", None])
def test_the_deep_slot_enacts_even_when_the_ai_failed(home, tmp_path, shim):
    """An outage must never truncate an active A/B sample — the experiment is the measurement, and
    a hole in the middle of one is worse than a day with no new advice."""
    from cherrypick.advisor import experiments, paths, store

    conn = store.connect()
    experiments.admit_spec(conn, session=SESSION, module="meic", params={"stop_trigger_ratio": 0.9})
    conn.close()

    result = _run("--slot", "deep", "--session", SESSION, shim=shim, tmp_path=tmp_path)
    assert result["ok"] is False, "the failure is still reported"
    assert paths.advice_path("meic", NEXT_SESSION).exists(), "but tomorrow's advice was issued"
    assert result["enacted"][0]["module"] == "meic"


def test_dry_run_prints_the_prompt_and_writes_nothing_but_the_pack(home, tmp_path):
    """The one thing --dry-run is for: reading the real pack before spending a real call."""
    from cherrypick.advisor import paths

    out = _run("--slot", "deep", "--session", SESSION, "--dry-run", tmp_path=tmp_path)["stdout"]
    assert "--- prompt (deep) ---" in out
    assert "read-only context" in out  # the live-facts note reaches the model
    assert '"pack_version": 1' in out
    assert not paths.raw_path(SESSION, "deep").exists()
    assert not paths.checkpoint_path(SESSION, "deep").exists()
    assert not paths.advice_path("meic", NEXT_SESSION).exists()


def test_the_light_and_deep_prompts_differ_in_what_they_ask_for(home, tmp_path):
    light = _run("--slot", "open", "--session", SESSION, "--dry-run", tmp_path=tmp_path)["stdout"]
    deep = _run("--slot", "deep", "--session", SESSION, "--dry-run", tmp_path=tmp_path)["stdout"]
    assert "intraday observer" in light and "post-close analyst" in deep
    assert "PROVISIONAL at this hour" in deep
    # Both carry the data-literacy preamble and the propose-only fence.
    for prompt in (light, deep):
        assert "`null` means NOT RECORDED" in prompt
        assert "Nothing you propose can reach a live account" in prompt


# --------------------------------------------------------------------------- 2026-09-12 review fixes


def _argv_seen(tmp_path: Path) -> list[str]:
    return json.loads((tmp_path / "bin" / "argv.json").read_text(encoding="utf-8"))


def _script_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("advisor_checkpoint", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_model_gets_no_tools_no_mcp_servers_and_no_skills(tmp_path):
    """The prompt says the fact pack is everything the model has. Until 2026-09-12 the invocation
    was a deny-list of seven tools, which left Read/Glob/Grep and every configured MCP server
    reachable -- every module's ledger included. The fence has to be on the argv, and asserted.

    Asserted on the argv the script BUILDS rather than on what a shim received: a multi-line
    prompt does not survive a Windows .bat launcher's `%*` intact, so the shim's record is
    truncated at the prompt's first newline on that platform."""
    argv = _script_module()._claude_argv("claude", "prompt", "opus")
    assert argv[argv.index("--tools") + 1] == "", "every built-in tool must be disabled"
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers": {}}'
    assert "--disable-slash-commands" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--disallowed-tools" not in argv
    assert argv[-2:] == ["--tools", ""], "the empty argument stays last so nothing can be swallowed"


def test_the_resolved_model_id_is_recorded_beside_the_alias(home, tmp_path):
    from cherrypick.advisor import store

    result = _run("--slot", "open", "--session", SESSION, "--model", "opus", shim="json", tmp_path=tmp_path)
    assert result["ok"] is True and result["admitted"] == 1
    assert result["model_id"] == "claude-test-1"
    conn = store.connect()
    row = conn.execute(
        "SELECT model, model_id FROM checkpoints WHERE session = ? AND slot = 'open'", (SESSION,)
    ).fetchone()
    conn.close()
    assert (row["model"], row["model_id"]) == ("opus", "claude-test-1")


@pytest.mark.parametrize(
    "shim,expected",
    [("silent", "returned nothing"), ("angry", "rate limited"), (None, "not on PATH")],
)
def test_a_claude_level_failure_leaves_a_failed_checkpoint_row_and_no_freeze(home, tmp_path, shim, expected):
    """Only a reply that failed to PARSE used to leave a row; the more common failure -- no reply
    at all -- read as a slot that never ran. It is a failed checkpoint, and the slot stays
    re-runnable because no summary file is written."""
    from cherrypick.advisor import paths, store

    result = _run("--slot", "open", "--session", SESSION, shim=shim, tmp_path=tmp_path)
    assert result["ok"] is False
    conn = store.connect()
    row = conn.execute(
        "SELECT ok, error FROM checkpoints WHERE session = ? AND slot = 'open'", (SESSION,)
    ).fetchone()
    conn.close()
    assert row is not None and row["ok"] == 0 and expected in row["error"]
    assert not paths.checkpoint_path(SESSION, "open").exists()
    again = _run("--slot", "open", "--session", SESSION, tmp_path=tmp_path)
    assert again["ok"] is True, "a failed slot must not be frozen"


def test_a_forced_rerun_does_not_duplicate_the_experiment(home, tmp_path):
    from cherrypick.advisor import store

    first = _run("--slot", "deep", "--session", SESSION, tmp_path=tmp_path)
    forced = _run("--slot", "deep", "--session", SESSION, "--force", tmp_path=tmp_path)
    assert first["ok"] and forced["ok"]
    conn = store.connect()
    rows = store.experiments(conn, module="meic")
    conn.close()
    assert len(rows) == 1, "the same reply admitted twice is one experiment"


def test_the_cli_admit_verb_is_frozen_too(home, tmp_path):
    """The console reaches `admit` directly; the freeze cannot live only in the script."""
    from cherrypick.advisor import paths

    first = _run("--slot", "open", "--session", SESSION, tmp_path=tmp_path)
    assert first["ok"] is True
    proc = subprocess.run(
        [
            sys.executable, "-m", "cherrypick.advisor", "admit",
            "--slot", "open", "--session", SESSION, "--raw", str(paths.raw_path(SESSION, "open")),
        ],
        capture_output=True, text=True, encoding="utf-8", env=os.environ,
    )
    out = json.loads(proc.stdout)
    assert out["ok"] is False and "frozen" in out["skipped"]
