"""The review CLI's session arguments name files, so they must be dates and nothing else."""

import argparse
import sys

import pytest

from cherrypick.review import __main__ as cli


def test_iso_date_passes_a_date_through():
    assert cli._iso_date("2026-10-26") == "2026-10-26"


@pytest.mark.parametrize("bad", ["../x", "..\\x", "2026-13-01", "today", "", "2026-10-26/../../y"])
def test_iso_date_refuses_anything_else(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        cli._iso_date(bad)


@pytest.mark.parametrize("command", ["build", "render"])
def test_main_refuses_a_path_as_a_session_before_building_anything(command, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["review", command, "--session", "../outside"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "not a YYYY-MM-DD date" in capsys.readouterr().err
