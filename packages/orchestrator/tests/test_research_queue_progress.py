"""The queue's fetch meter is the backtest package's own count, by subprocess, and never fails a list."""

from __future__ import annotations

import json
import subprocess

from cherrypick.orchestrator import researchqueue as rq


def test_only_a_chain_fetch_has_a_meter():
    assert rq.fetch_progress({"kind": "script", "args": "{}"}) is None
    assert rq.fetch_progress({"kind": "fetch-chains", "args": "{}"}) is None  # no request file


def test_the_meter_is_the_backtest_verb_and_a_failure_is_none(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"total": 2, "answered": 1}), stderr="")

    monkeypatch.setattr(rq.subprocess, "run", fake_run)
    row = {"kind": "fetch-chains", "args": json.dumps({"requests": "r.json"})}
    assert rq.fetch_progress(row) == {"total": 2, "answered": 1}
    assert seen["argv"][1:5] == ["-m", "cherrypick.backtest", "chain-progress", "--requests"]

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(rq.subprocess, "run", boom)
    assert rq.fetch_progress(row) is None
