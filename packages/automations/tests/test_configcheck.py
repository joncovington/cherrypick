"""`cli check`: would the paper loop refuse this config, answered without running the loop.

The 2026-11-25 to 11-27 incident: a group name left stale in the config refused every tick of the loop and
nothing said so until 45 minutes into a session. The check is the loop's own load, run ahead of the open.
"""

import json
import pathlib

from cherrypick.automations import cli, configcheck
from cherrypick.automations import config as config_mod

EXAMPLE = pathlib.Path(config_mod._PKG_ROOT) / "config.example.json"


def _stale_group_text() -> str:
    """The shipped config with its `default` group misspelt: the arms still name `default`."""
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    doc["groups"] = {"defualt": {"enabled": False}}
    return json.dumps(doc)


def _write(tmp_path, text) -> str:
    path = tmp_path / "automations.json"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_shipped_config_is_accepted():
    out = configcheck.check()
    assert out["ok"] is True and out["path"].endswith("config.example.json")


def test_a_stale_group_name_is_refused_with_the_loops_own_words(tmp_path):
    path = _write(tmp_path, _stale_group_text())
    out = configcheck.check(path)
    assert out["ok"] is False
    assert out["error"].startswith("arms: groups.defualt")
    assert out["path"] == path


def test_candidate_text_is_checked_in_place_of_the_file(tmp_path):
    good = _write(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    assert configcheck.check(good, _stale_group_text())["ok"] is False
    broken = _write(tmp_path, _stale_group_text())
    assert configcheck.check(broken, EXAMPLE.read_text(encoding="utf-8"))["ok"] is True


def test_text_that_is_not_json_or_not_an_object_is_refused(tmp_path):
    assert configcheck.check(None, "{ nope")["ok"] is False
    out = configcheck.check(None, "[1]")
    assert out["ok"] is False and "JSON object" in out["error"]


def test_no_config_anywhere_is_a_refusal_not_an_exit(monkeypatch):
    monkeypatch.setattr(config_mod._home, "module_config_source", lambda *a, **k: None)
    monkeypatch.setattr(
        config_mod._home,
        "load_module_config",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit("no config found")),
    )
    out = configcheck.check()
    assert out["ok"] is False and "no config found" in out["error"]


def test_the_check_writes_nothing(tmp_path, managed_home, capsys):
    path = _write(tmp_path, _stale_group_text())
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert cli.main(["--config", path, "check"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert not managed_home.exists()  # no ledger, no stream request, no state


def test_the_stdin_flag_reads_the_candidate(tmp_path, monkeypatch, capsys):
    import io

    path = _write(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    monkeypatch.setattr("sys.stdin", io.StringIO(_stale_group_text()))
    assert cli.main(["--config", path, "check", "--stdin"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_the_check_agrees_with_the_loop(tmp_path, capsys):
    """Whatever the loop refuses, the check refuses with the same message."""
    from cherrypick.automations import paper_loop

    path = _write(tmp_path, _stale_group_text())
    assert paper_loop.main(["--config", path, "--db", str(tmp_path / "l.db"), "--status"]) == 2
    loop_error = json.loads(capsys.readouterr().out.splitlines()[0])["error"]
    assert configcheck.check(path)["error"] == loop_error
