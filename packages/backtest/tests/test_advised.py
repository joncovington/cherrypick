"""The advisor's specs run here: each becomes a config-defined advised arm (base + overlay), all run
over the design window in one run, each counted as a trial, each result written back."""

from __future__ import annotations

import json

import pytest
from test_runner import REPLAYED_CONFIGS, _replay, _unlock, world  # noqa: F401 -- the fixture

from cherrypick.backtest import advised, results, runner

REQ = {
    "id": 7,
    "module": "flies",
    "name": "Wider wings!",
    "base_arm": "range-high",
    "params": {"wing_width_strikes": 4},
}


class FakeAdvisor:
    def __init__(self, requests):
        self.requests, self.calls, self.results = requests, [], {}

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "backtest-requests":
            return {"ok": True, "requests": self.requests}
        i = args.index("--result-file")
        with open(args[i + 1], encoding="utf-8") as fh:
            self.results[int(args[args.index("--id") + 1])] = (
                args[args.index("--status") + 1],
                json.load(fh),
            )
        return {"ok": True}


def _read(path):
    day = path.stem
    rows = [{"arm": "control", "session": day, "net_pnl": 1.0, "gross_pnl": 2.0, "cost": 1.0}]
    rows.append({"arm": advised.arm_name(REQ), "session": day, "net_pnl": 3.0, "gross_pnl": 4.0, "cost": 1.0})
    return rows


def test_a_spec_runs_as_an_overlay_arm_over_the_design_window_and_is_written_back(world):  # noqa: F811
    s, _ = world
    _unlock(s)
    fake = FakeAdvisor([REQ])
    REPLAYED_CONFIGS.clear()
    out = advised.run(s, advisor=fake, replay=_replay, read_rows=_read, workers=1, log=lambda _m: None)
    name = advised.arm_name(REQ)
    assert name == "advised:wider-wings-7"
    arm = REPLAYED_CONFIGS[0]["arms"][name]
    assert arm == {"gate": "x", "wing_width_strikes": 4, "enabled": True}, (
        "the base's params with the overlay"
    )
    status, result = fake.results[7]
    assert status == "done" and result["window"] == "design" and result["summary"]["sessions"] >= 1
    assert out["written"] == [{"id": 7, "arm": name, "status": "done"}]
    assert results.trial_count(results.connect(), "flies") == 2, "control and the spec: each a trial"


def test_advised_arms_never_run_outside_the_design_window(world):  # noqa: F811
    s, _ = world
    _unlock(s)
    with pytest.raises(runner.RunRefused, match="design window only"):
        runner.run(s, [], "holdout", spend_holdout=True, extra_arms={"advised:x": {}}, replay=_replay,
                   read_rows=_read, log=lambda _m: None)  # fmt: skip


def test_an_empty_queue_runs_nothing(world):  # noqa: F811
    s, _ = world
    assert advised.run(s, advisor=FakeAdvisor([]), replay=_replay, read_rows=_read) == {
        "ok": True,
        "requests": 0,
    }
