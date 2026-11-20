"""The Builder's pin of a backtest reference (`referenceedit`, configcli `automation_reference`): the
module's check, the backtest package's word that the run exists, then the `references` block written in
one config save, nothing else in the file changed."""

from __future__ import annotations

import json

import pytest
from cherrypick.core import home as core_home

from cherrypick.orchestrator import configcli, referenceedit

FILE = (
    "{\r\n"
    '  "_header": "automations: kept as written",\r\n'
    '  "groups": {"ensemble": {"enabled": false}},\r\n'
    '  "automations": {"iron-fly-1000": {"enabled": true}}\r\n'
    "}\r\n"
)
RUN = "builder-iron-condor-1345-2611201000"


@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = core_home.config_path("automations")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FILE.encode("utf-8"))
    return path


def module(problems=(), block=None, seen=None):
    def run(cfg, req):
        if seen is not None:
            seen.append(req)
        if problems:
            return {"ok": True, "problems": list(problems), "block": None}
        return {
            "ok": True,
            "problems": [],
            "block": {RUN.split("-2")[0][8:]: RUN} if block is None else block,
        }

    return run


def listed(*runs):
    return lambda name: {"ok": True, "name": name, "runs": [{"run": r} for r in runs]}


def doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


REQ = {"name": "iron-condor-1345", "run": RUN}


def test_a_pin_adds_the_block_and_leaves_the_rest_as_written(cfgfile):
    out = referenceedit.save({}, REQ, module(), listed(RUN))
    assert out["ok"] is True and out["run"] == RUN
    d = doc(cfgfile)
    assert d["references"] == {"iron-condor-1345": RUN}
    assert d["automations"] == {"iron-fly-1000": {"enabled": True}} and d["groups"] == {
        "ensemble": {"enabled": False}
    }
    assert '"_header": "automations: kept as written"' in cfgfile.read_text(encoding="utf-8")


def test_a_second_pin_replaces_the_block(cfgfile):
    referenceedit.save({}, REQ, module(), listed(RUN))
    other = "builder-iron-condor-1345-2611210900"
    referenceedit.save({}, {**REQ, "run": other}, module(block={"iron-condor-1345": other}), listed(other))
    assert doc(cfgfile)["references"] == {"iron-condor-1345": other}


def test_a_run_the_backtest_package_does_not_list_writes_nothing(cfgfile):
    out = referenceedit.save({}, REQ, module(), listed("builder-iron-condor-1345-2601010900"))
    assert out["ok"] is False and out["code"] == "not_found"
    assert "references" not in doc(cfgfile)


def test_the_backtest_package_unavailable_writes_nothing(cfgfile):
    out = referenceedit.save({}, REQ, module(), lambda name: None)
    assert out["ok"] is False and out["code"] == "unavailable"
    assert "references" not in doc(cfgfile)


def test_a_problem_the_module_names_writes_nothing(cfgfile):
    out = referenceedit.save({}, REQ, module(problems=["no automation named x"]), listed(RUN))
    assert out["ok"] is False and out["code"] == "invalid" and out["problems"] == ["no automation named x"]
    assert "references" not in doc(cfgfile)


def test_unpinning_needs_no_backtest_read(cfgfile):
    def refuse(name):
        raise AssertionError("unpinning read the backtest package")

    out = referenceedit.save({}, {"name": "iron-condor-1345", "run": None}, module(block={}), refuse)
    assert out["ok"] is True and doc(cfgfile)["references"] == {}


def test_the_request_is_passed_to_the_module_as_sent(cfgfile):
    seen = []
    referenceedit.save({}, {**REQ, "extra": 1}, module(seen=seen), listed(RUN))
    assert seen == [{"name": "iron-condor-1345", "run": RUN}]


@pytest.mark.parametrize("req", [{}, {"name": ""}, {"name": "x", "run": 3}])
def test_a_malformed_request_is_refused_before_the_module(cfgfile, req):
    def never(cfg, r):
        raise AssertionError("asked the module")

    assert referenceedit.save({}, req, never, listed(RUN))["ok"] is False


def test_a_stale_page_is_refused(cfgfile):
    out = referenceedit.save({}, {**REQ, "expected_mtime": 1}, module(), listed(RUN))
    assert out["ok"] is False and out["code"] == "conflict"


def test_configcli_routes_the_op(cfgfile, monkeypatch):
    monkeypatch.setattr(configcli.cfgmod, "load_config", lambda: {"modules": {}})
    monkeypatch.setattr(referenceedit, "run_module_check", module())
    monkeypatch.setattr(referenceedit, "run_backtest_runs", listed(RUN))
    assert configcli.handle({"op": "automation_reference", **REQ})["ok"] is True
    assert doc(cfgfile)["references"] == {"iron-condor-1345": RUN}
