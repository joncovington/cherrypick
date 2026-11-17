"""`read automation-runs`: the Builder's run card reads back only that automation's Builder results,
from both result folders, each marked licensed by where it lives, without its trades."""

from __future__ import annotations

import json

from cherrypick.backtest import paths, readout


def _write(d, name, **over):
    d.mkdir(parents=True, exist_ok=True)
    doc = {"name": name, "kind": "explore", "symbol": "SPX", "source": "recorded-0dte", "licensed": False,
           "summary": {"mine": {"sessions": 3, "trades": 2, "net": 10.0, "mean_net": 5.0}},
           "trades": [{"net": 1}], "generated_at": name[-10:], **over}  # fmt: skip
    (d / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_only_this_automations_builder_runs_newest_first_without_trades(tmp_path, monkeypatch):
    own, lic = tmp_path / "own", tmp_path / "lic"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: lic if licensed else own)
    _write(own, "builder-mine-2611161730")
    _write(lic, "builder-mine-2611171730", source="vendor-0dte-spx-60s", licensed=True)
    _write(own, "builder-mine-two-2611161730")  # another automation whose name starts the same
    _write(own, "my-hand-run")  # a run not queued by the Builder
    (own / "builder-mine-2611181730.json").write_text("{", encoding="utf-8")
    out = readout.automation_runs("mine")
    assert out["ok"] is True
    assert [r["run"] for r in out["runs"]] == ["builder-mine-2611171730", "builder-mine-2611161730"]
    assert [r["licensed"] for r in out["runs"]] == [True, False]
    assert "trades" not in out["runs"][0] and out["runs"][1]["summary"]["mine"]["trades"] == 2
    assert out["unreadable"] == ["builder-mine-2611181730.json"]


def test_a_file_in_the_licensed_folder_is_licensed_whatever_it_says(tmp_path, monkeypatch):
    lic = tmp_path / "lic"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: lic if licensed else tmp_path / "own")
    _write(lic, "builder-mine-2611161730", licensed=False)
    assert readout.automation_runs("mine")["runs"][0]["licensed"] is True


def test_a_name_that_is_not_an_automations_is_refused():
    assert readout.automation_runs("../x")["ok"] is False
    assert readout.automation_runs("")["ok"] is False
