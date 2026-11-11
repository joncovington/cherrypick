"""Producer instances (`streamer.instances`): one spec per process, each watched, started, stopped and
budgeted under its own label, each reading its own request folder."""

from __future__ import annotations

import pytest
from cherrypick.core import streamrequests as _sr

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import proc, watchdog

pytestmark = pytest.mark.unit

MAIN = {
    "enabled": True,
    "path": "../streamer",
    "status_argv": ["run.py", "--status"],
    "start_argv": ["run.py"],
    "stop_argv": ["run.py", "--stop"],
    "auto_restart": True,
}


def _cfg(instances=("margin",), logins=("margin",)):
    return {
        "streamer": {**MAIN, "instances": list(instances)},
        "broker": {"logins": [{"name": n} for n in logins]},
    }


def test_no_instances_is_main_alone_unchanged():
    specs = cfgmod.streamer_specs({"streamer": MAIN})
    assert list(specs) == ["streamer"] and specs["streamer"] is MAIN
    assert cfgmod.streamer_specs({"streamer": {**MAIN, "enabled": False}}) == {}


def test_an_instance_spec_is_main_s_with_its_name_on_every_argv():
    specs = cfgmod.streamer_specs(_cfg())
    assert list(specs) == ["streamer", "streamer.margin"]
    inst = specs["streamer.margin"]
    assert inst["status_argv"] == ["run.py", "--status", "--instance", "margin"]
    assert inst["start_argv"] == ["run.py", "--instance", "margin"]
    assert inst["stop_argv"] == ["run.py", "--stop", "--instance", "margin"]
    assert inst["config_name"] == "streamer.margin" and inst["instance"] == "margin"
    assert specs["streamer"]["start_argv"] == ["run.py"]  # main untouched


def test_an_instance_needs_a_listed_login():
    assert cfgmod.streamer_instances(_cfg(instances=["margin", "ghost", "margin"], logins=["margin"])) == [
        "margin"
    ]


def test_every_instance_is_a_named_daemon():
    names = proc.daemons(_cfg())
    assert names["streamer.margin"][2] is True and names["streamer"][2] is True


def test_the_watchdog_checks_each_instance_in_its_own_request_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(cfgmod, "module_root", lambda spec, name: tmp_path)
    seen = []

    def one(label, spec, in_session):
        seen.append((label, _sr.active_instance()))
        return []

    monkeypatch.setattr(watchdog, "_check_one_producer", one)
    watchdog._check_producer(_cfg(), True)
    assert seen == [("streamer", "main"), ("streamer.margin", "margin")]
    assert _sr.active_instance() == "main"


def test_each_instance_has_its_own_budget_finding(monkeypatch):
    seen = []

    def budget(cfg, key):
        seen.append((key, _sr.active_instance()))
        return []

    monkeypatch.setattr(watchdog, "_check_one_budget", budget)
    watchdog._check_subscription_budget(_cfg())
    assert seen == [("streamer.budget", "main"), ("streamer.margin.budget", "margin")]
