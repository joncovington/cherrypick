"""A chain fetch's meter: answered of total through one request list, by status, with rate and ETA --
counts only, read from the store whoever ran the fetch."""

import sqlite3

from cherrypick.backtest import chainfetch


def test_progress_counts_only_the_list_and_derives_rate_and_eta(tmp_path, monkeypatch):
    db = tmp_path / "eod_chains.db"
    monkeypatch.setattr(chainfetch, "db_path", lambda settings: db)
    con = sqlite3.connect(db)
    con.executescript(chainfetch.SCHEMA)
    now = 1_800_000_000.0
    rows = [
        ("AAA", "2024-01-02", "2024-02-16", "complete", now - 60),
        ("AAA", "2024-01-03", "2024-02-16", "empty", now - 120),
        ("BBB", "2024-01-02", "2024-02-16", "complete", now - 7200),  # answered long ago
        ("ZZZ", "2024-01-02", "2024-02-16", "complete", now - 30),  # not on this list
    ]
    con.executemany(
        "INSERT INTO eod_chain_requests (symbol, session, expiration, status, fetched_at) VALUES (?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    wanted = [("AAA", "2024-01-02", "2024-02-16"), ("AAA", "2024-01-03", "2024-02-16"),
              ("BBB", "2024-01-02", "2024-02-16"), ("CCC", "2024-01-02", "2024-02-16")]  # fmt: skip
    out = chainfetch.progress({}, wanted, now=now)
    assert out["total"] == 4 and out["answered"] == 3
    assert out["by_status"] == {"complete": 2, "empty": 1}
    assert out["rate_per_min"] == 0.2  # two answers in the last ten minutes
    assert out["eta_minutes"] == 5  # one left at 0.2 a minute
    assert out["last_answer_age_s"] == 60


def test_progress_with_no_store_is_all_unanswered(tmp_path, monkeypatch):
    monkeypatch.setattr(chainfetch, "db_path", lambda settings: tmp_path / "none.db")
    out = chainfetch.progress({}, [("A", "2024-01-02", "2024-02-16")])
    assert out["answered"] == 0 and out["rate_per_min"] is None
