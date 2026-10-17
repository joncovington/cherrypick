"""`flies replay-check` without --detail: the summary is computed from the matched entries before they
are dropped from the reply (dropping them first raised KeyError on every such run), and --history-db
reaches the replay."""

from __future__ import annotations

import json
import sqlite3

from cherrypick.flies import cli, replay_check


def _session(day):
    return {
        "ok": True,
        "date": day,
        "paper_entries": 2,
        "entries_matched": 2,
        "extra_entry_episodes": [],
        "paper_completions": 1,
        "completions_matched": 1,
        "early_completion_episodes": [],
        "matched_entries": [{"credit_diff": 0.05}, {"credit_diff": -0.10}],
        "completion_outcomes": [],
    }


def test_the_summary_survives_dropping_the_detail(tmp_path, monkeypatch, capsys):
    paper = tmp_path / "paper.db"
    sqlite3.connect(paper).close()
    seen = {}

    def fake_check(config, day, conn, *, arm, history_db=None):
        seen[day] = history_db
        return _session(day)

    monkeypatch.setattr(replay_check, "decision_check", fake_check)
    monkeypatch.setattr(replay_check, "_outcome_share", lambda _ok: None)
    code = cli.main(
        ["--db", str(paper), "replay-check", "--dates", "2026-10-13,2026-10-14", "--history-db", "v.db"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["summary"]["measures"]["credit_abs_median"] == 0.075
    assert all("matched_entries" not in s for s in out["sessions"])
    assert seen == {"2026-10-13": "v.db", "2026-10-14": "v.db"}
