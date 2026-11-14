"""#1117: a study reads only the chain paths it declared, and fingerprints what it read. A synthetic
store built from the fetch's own DDL; no vendor data."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.backtest import chainfetch
from cherrypick.backtest.study.inputs import Inputs, Undeclared


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    held = {"AAA": ("2024-02-01", "2024-02-02", "2024-03-01"), "BBB": ("2024-02-01",)}
    exp = "2024-03-15"
    for sym, sessions in held.items():
        for s in sessions:
            conn.executemany(
                "INSERT INTO eod_chain_quotes (symbol, session, expiration, strike, right, bid, ask) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(sym, s, exp, 100.0, "CALL", 2.0, 2.2), (sym, s, exp, 100.0, "PUT", 2.0, 2.2)],
            )
    conn.execute(
        "INSERT INTO eod_chain_paths VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0)",
        ("AAA", exp, "2024-02-01", "2024-02-29", "complete", 2, 4),
    )
    return conn


@pytest.fixture
def inputs(tmp_path):
    p = tmp_path / "path_requests.json"
    req = {"symbol": "aaa", "expiration": "2024-03-15", "start": "2024-02-01", "through": "2024-02-29"}
    p.write_text(json.dumps({"requests": [req]}), encoding="utf-8")
    return Inputs(p)


def test_only_the_declared_path_and_its_dates_are_read(store, inputs):
    # The store also holds 2024-03-01 for AAA (another study's fetch) and BBB: neither is read.
    assert inputs.sessions(store, "AAA", "2024-03-15") == ["2024-02-01", "2024-02-02"]
    assert inputs.chain(store, "AAA", "2024-02-01", "2024-03-15").forward == 100.0
    with pytest.raises(Undeclared):
        inputs.sessions(store, "BBB", "2024-03-15")
    with pytest.raises(Undeclared):
        inputs.chain(store, "AAA", "2024-03-01", "2024-03-15")


def test_a_declared_session_the_store_lacks_is_none(store, inputs):
    assert inputs.chain(store, "AAA", "2024-02-05", "2024-03-15") is None


def test_the_fingerprint_counts_what_was_declared_and_moves_when_it_does(store, inputs):
    fp = inputs.fingerprint(store)
    assert fp == {**fp, "paths": 1, "complete": 1, "sessions": 2, "rows": 4, "requests": "path_requests.json"}
    store.execute("UPDATE eod_chain_paths SET sessions = 3, rows = 6")
    assert inputs.fingerprint(store)["sha256"] != fp["sha256"]


def test_a_path_never_fetched_reads_missing(tmp_path, store):
    p = tmp_path / "r.json"
    req = {"symbol": "CCC", "expiration": "2024-03-15", "start": "2024-02-01", "through": "2024-02-29"}
    p.write_text(json.dumps({"requests": [req]}), encoding="utf-8")
    fp = Inputs(p).fingerprint(store)
    assert fp["complete"] == 0 and fp["sessions"] == 0
