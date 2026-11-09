"""The research scripts' correction logs, the stamp beside a result, and the stale reading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrypick.orchestrator import researchqueue as rq
from cherrypick.orchestrator import studyversion as sv

ONE = {"on": "2026-11-01", "what": "a", "why": "b", "decided": "user", "before_read": True}
TWO = {**ONE, "on": "2026-11-02", "what": "c"}


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "scripts" / "x_item99.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_the_log_is_read_by_parsing_and_its_length_is_the_version(tmp_path):
    body = f"import nothing_that_exists\nREPLAY_CORRECTIONS = ({ONE!r}, {TWO!r})\n"
    assert [c["what"] for c in sv.corrections(_script(tmp_path, body))] == ["a", "c"]
    assert sv.corrections(_script(tmp_path, "X = 1\n")) == [], "no log is version 0"
    assert sv.corrections(tmp_path / "missing.py") == []


@pytest.mark.parametrize(
    "log, match",
    [
        (({k: v for k, v in ONE.items() if k != "why"},), "needs exactly"),
        (({**ONE, "before_read": "yes"},), "true or false"),
        (({**ONE, "on": "1 Nov"},), "YYYY-MM-DD"),
        ((TWO, ONE), "dated before"),
    ],
)
def test_an_incomplete_or_out_of_order_entry_is_refused(tmp_path, log, match):
    with pytest.raises(sv.BadLog, match=match):
        sv.corrections(_script(tmp_path, f"REPLAY_CORRECTIONS = {log!r}\n"))


def test_a_log_built_by_code_is_refused(tmp_path):
    with pytest.raises(sv.BadLog, match="literal"):
        sv.corrections(_script(tmp_path, "REPLAY_CORRECTIONS = tuple(range(2))\n"))


def test_the_stamp_sits_beside_the_result_and_reads_back(tmp_path):
    script = _script(tmp_path, f"REPLAY_CORRECTIONS = ({ONE!r},)\n")
    out = tmp_path / "res" / "results.json"
    out.parent.mkdir()
    out.write_text("{}", encoding="utf-8")
    doc = sv.stamp(out, script, tmp_path, now="2026-11-01T22:00:00", sha="abc1234")
    assert sv.stamp_path(out) == tmp_path / "res" / "results.version.json"
    assert sv.read_stamp(out) == doc
    assert doc["script"] == "scripts/x_item99.py" and doc["version"] == 1 and doc["commit"] == "abc1234"


def test_a_result_is_stale_once_the_log_grows_past_its_stamp():
    assert sv.state({"version": 1}, [ONE, TWO]) | {} == {
        "version": 1, "current": 2, "commit": None, "script": None, "stale": True,
        "since": [{"on": "2026-11-02", "what": "c", "before_read": True}],
    }  # fmt: skip
    assert sv.state({"version": 2}, [ONE, TWO])["stale"] is False
    unstamped = sv.state(None, [ONE])
    assert unstamped["stale"] and unstamped["version"] is None and len(unstamped["since"]) == 1
    assert sv.state(None, [])["stale"] is False, "unstamped with no corrections is not stale"
    assert sv.state(None, None) is None


def test_every_research_script_s_log_parses():
    """A malformed log would hide a correction from the board, so each one in the repo must read."""
    scripts = sorted(p for d in rq.SCRIPT_DIRS for p in (rq.repo_root() / d).glob("*.py"))
    assert scripts
    logged = {p.name: sv.corrections(p) for p in scripts}
    assert len(logged["pmcc_item36.py"]) >= 4 and logged["earnings_item31.py"]


def test_the_queue_stamps_a_script_row_s_output(tmp_path, monkeypatch):
    out = tmp_path / "results.json"
    out.write_text("{}", encoding="utf-8")
    row = {"args": json.dumps({"path": "packages/backtest/scripts/pmcc_item36.py"}), "output": str(out)}
    assert rq.stamp_output(row) == len(
        sv.corrections(rq.repo_root() / "packages/backtest/scripts/pmcc_item36.py")
    )
    assert sv.read_stamp(out)["script"] == "packages/backtest/scripts/pmcc_item36.py"
    monkeypatch.setattr(sv, "stamp", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert rq.stamp_output(row) == "not stamped: disk full"
