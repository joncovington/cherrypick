"""The regime-cuts artifact for MEIC (2026-09-19): `analytics.regime_cuts` over the shared
`cherrypick.core.regimecuts` contract, the `db.measurement_breaks` reader, and the writer entry
point `cherrypick.meic.regime_cuts` (its own module: `cli.py` is pinned never to write)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.core import regimecuts as rc  # noqa: E402

from cherrypick.meic import analytics, db, regime_cuts  # noqa: E402

DAY = "2026-09-18"


def _insert(conn, **overrides):
    row = {
        "trade_date": "2026-09-14",
        "symbol": "SPX",
        "status": "expired",
        "arm": "control",
        "era": analytics.CURRENT_ERA,
        "put_credit": 0.9,
        "call_credit": 0.9,
        "net_credit": 1.8,
        "wing_width": 10,
        "put_strike": 7450.0,
        "call_strike": 7550.0,
        "pnl": 100.0,
        "fees": 6.89,
        "quantity": 1,
        "ic_order_id": f"IC-{overrides.get('ic_order_id', 'X')}",
        "created_at": "x",
        "updated_at": "x",
    }
    row.update(overrides)
    conn.execute(
        f"INSERT INTO ic_trades ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = str(tmp_path / "paper_trades.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.cmd_init_db(None)
    conn = sqlite3.connect(path)
    for i, day in enumerate(("2026-09-14", "2026-09-15", "2026-09-16")):
        _insert(
            conn, ic_order_id=f"c{i}", trade_date=day, entry_gex_bucket="diffuse", entry_trend_bucket="flat"
        )
    _insert(
        conn,
        ic_order_id="c-up",
        trade_date="2026-09-16",
        pnl=-500.0,
        entry_gex_bucket="diffuse",
        entry_trend_bucket="up_from_open",
    )
    for i, day in enumerate(("2026-09-15", "2026-09-16")):
        _insert(
            conn,
            ic_order_id=f"a{i}",
            trade_date=day,
            arm="advised:x",
            entry_gex_bucket="clustered",
            entry_trend_bucket="flat",
        )
    _insert(conn, ic_order_id="old-era", trade_date="2026-08-10", era="sample", entry_gex_bucket="diffuse")
    _insert(conn, ic_order_id="untagged", trade_date="2026-09-17")
    conn.execute(
        "INSERT INTO measurement_breaks (break_date, scope, kind, reason, created_at) VALUES "
        "('2026-08-21', '*', 'advisor_era_cutover', 'era', 'x'), ('2026-12-18', '*', 'entry_rules', 'future', 'x')"
    )
    conn.commit()
    conn.close()
    return path


def test_measurement_breaks_reader_returns_rows_oldest_first(ledger):
    conn = sqlite3.connect(ledger)
    rows = db.measurement_breaks(conn)
    assert [r["break_date"] for r in rows] == ["2026-08-21", "2026-12-18"] and rows[0]["scope"] == "*"
    conn.close()


def test_regime_cuts_arms_come_from_the_ledger_and_completion_is_null(ledger):
    conn = sqlite3.connect(ledger)
    conn.row_factory = sqlite3.Row
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    assert doc["module"] == "meic" and doc["arm_column"] == "arm" and doc["entry_modes"] is None
    assert doc["era"]["start"] == "2026-08-21" and doc["era"]["key"] == analytics.CURRENT_ERA
    assert [b["break_date"] for b in doc["era"]["ignored_future"]] == ["2026-12-18"]
    books = {b["arm"]: b for b in doc["arms"]}
    assert set(books) == {"control", "advised:x"}  # the sample-era row is outside the era
    assert books["control"]["trades"] == 5 and books["control"]["completed"] is None
    assert books["control"]["completion_rate"] is None
    gex = {b["bucket"]: b for b in books["control"]["dimensions"]["gex"]["buckets"]}
    assert (
        gex["diffuse"]["trades"] == 4 and gex["diffuse"]["thin"] is False and gex["untagged"]["trades"] == 1
    )
    assert books["advised:x"]["dimensions"]["gex"]["buckets"][0]["thin"] is True
    conn.close()


def test_cross_tab_pairs_gex_and_trend(ledger):
    conn = sqlite3.connect(ledger)
    conn.row_factory = sqlite3.Row
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    control = next(b for b in doc["cross_tabs"][0]["arms"] if b["arm"] == "control")
    cells = {tuple(c["buckets"]): c for c in control["cells"]}
    assert cells[("diffuse", "flat")]["trades"] == 3 and cells[("diffuse", "up_from_open")]["thin"] is True
    assert cells[("diffuse", "up_from_open")]["net_pnl"] == -506.89
    conn.close()


def test_writer_entry_point_writes_dated_and_latest_and_reads_the_ledger_read_only(
    ledger, tmp_path, monkeypatch, capsys
):
    from cherrypick.meic import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "out")
    assert regime_cuts.main(["--db", ledger, "--write", "--session", DAY]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["written"]["latest"] and out["session"] == DAY
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        f"regime_cuts-{DAY}.json",
        "regime_cuts.json",
    ]
    assert regime_cuts.main(["--db", ledger, "--write", "--backfill", "--since", "2026-09-15"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sessions"] == ["2026-09-15", "2026-09-16", "2026-09-17"]
    assert rc.latest_session(tmp_path / "out") == DAY  # a backfill never overtakes the newer latest


def test_session_totals_are_the_cells_own_net_of_fees(ledger):
    """The robustness stamps are only as good as their input: each bucket's per-session totals must
    add up to the net the cell publishes. Shown to fail by pooling `pnl` alone -- MEIC's `pnl` is
    gross, and the 2026-09-28 read made exactly that mistake by hand."""
    conn = sqlite3.connect(ledger)
    conn.row_factory = sqlite3.Row
    for dim in analytics.REGIME_DIMENSIONS:
        for r in analytics.by_regime(conn, dim, arm="control", with_sessions=True):
            assert round(sum(v[1] for v in r["session_nets"].values()), 2) == r["net_pnl"], (dim, r["bucket"])
            assert sum(v[0] for v in r["session_nets"].values()) == r["trades"]
    assert "session_nets" not in analytics.by_regime(conn, "gex", arm="control")[0]
    conn.close()


def test_regime_cuts_stamps_robustness_and_paired_from_the_ledger(ledger):
    conn = sqlite3.connect(ledger)
    conn.row_factory = sqlite3.Row
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    control = next(b for b in doc["arms"] if b["arm"] == "control")
    diffuse = next(b for b in control["dimensions"]["gex"]["buckets"] if b["bucket"] == "diffuse")
    # three sessions, one of them carrying the -500 condor: fragile by concentration
    assert diffuse["fragile"] is True and diffuse["robustness"]["largest_session_net"] == -413.78
    assert control["dimensions"]["trend"]["paired"] == []  # flat and up share one session only
    assert doc["multiplicity"]["intervals"] >= 1
    assert "session_nets" not in json.dumps(doc)
    conn.close()
