"""The regime-cuts artifact writer (2026-09-19): flies' `analytics.regime_cuts` over the shared
`cherrypick.core.regimecuts` contract, and the `regime-cuts` CLI. Seeds rows with the same helper
`test_analytics.py` uses."""

import json

import pytest
from cherrypick.core import regimecuts as rc

from cherrypick.flies import analytics, cli
from cherrypick.flies import db as dbmod


def position(conn, position_id, *, day, arm, kind="fly", pnl=98.11, gross=105.0, regime=None):
    """A settled legged row, the shape test_analytics.position() writes (tests dir is not a package)."""
    dbmod.save_position(
        conn,
        {
            "position_id": position_id,
            "book_id": f"{day}:{arm}:SPX",
            "trade_date": day,
            "arm": arm,
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": kind,
            "side": "put",
            "center": 6000.0,
            "wing_width": 5.0,
            "quantity": 1,
            "net": 1.05,
            "credit": 2.55,
            "fees": 6.89,
            "gross_pnl": gross,
            "pnl": pnl,
            "status": "settled",
            "underlying_at_entry": 6000.0,
            "risk_free": 1,
            "entry_time": f"{day}T12:00:00",
            **{f"entry_{k}": v for k, v in (regime or {}).items()},
        },
    )


DAY = "2026-09-18"


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def _seed(conn):
    # control: three sessions, tagged gex + trend; one completed, one not
    for i, day in enumerate(("2026-09-14", "2026-09-15", "2026-09-16")):
        position(
            conn, f"c{i}", day=day, arm="control", regime={"gex_bucket": "diffuse", "trend_bucket": "flat"}
        )
    position(
        conn,
        "c-strand",
        day="2026-09-16",
        arm="control",
        kind="short_vertical",
        pnl=-300.0,
        gross=-293.0,
        regime={"gex_bucket": "diffuse", "trend_bucket": "up_from_open"},
    )
    # an advised twin, two sessions (thin)
    for i, day in enumerate(("2026-09-15", "2026-09-16")):
        position(
            conn,
            f"a{i}",
            day=day,
            arm="advised:x",
            regime={"gex_bucket": "clustered", "trend_bucket": "flat"},
        )
    # callwall with a pre-break row that must not count for it
    position(conn, "w-old", day="2026-08-25", arm="callwall", regime={"gex_bucket": "diffuse"})
    position(conn, "w-new", day="2026-09-15", arm="callwall", regime={"gex_bucket": "diffuse"})
    # an untagged control row
    position(conn, "c-untagged", day="2026-09-17", arm="control")
    # the flies-style fly rows above are "completed" only when completed_at is set
    conn.execute("UPDATE fly_positions SET completed_at = entry_time WHERE kind = 'fly'")
    conn.commit()
    dbmod.record_measurement_break(conn, break_date="2026-08-21", kind="advisor_era_cutover", reason="era")
    dbmod.record_measurement_break(
        conn, break_date="2026-08-31", scope="callwall", kind="arm_added", reason="added"
    )
    dbmod.record_measurement_break(conn, break_date="2026-12-18", kind="entry_rules", reason="future gate")


def test_by_regime_arm_filter_excludes_other_arms(conn):
    _seed(conn)
    rows = analytics.by_regime(conn, "gex", arm="advised:x")
    assert {r["bucket"] for r in rows} == {"clustered"} and rows[0]["trades"] == 2


def test_by_regime_reports_completed_and_completion_rate(conn):
    _seed(conn)
    rows = {r["bucket"]: r for r in analytics.by_regime(conn, "trend", arm="control")}
    assert rows["flat"]["completed"] == 3 and rows["flat"]["completion_rate"] == 1.0
    assert rows["up_from_open"]["completed"] == 0 and rows["up_from_open"]["completion_rate"] == 0.0


def test_regime_cuts_scopes_the_era_and_each_arm_from_its_own_break(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    assert doc["era"]["start"] == "2026-08-21"
    assert [b["break_date"] for b in doc["era"]["ignored_future"]] == ["2026-12-18"]
    books = {b["book"]: b for b in doc["books"]}
    assert books["callwall"]["era_start"] == "2026-08-31" and books["callwall"]["trades"] == 1
    assert books["control"]["era_start"] == "2026-08-21" and books["control"]["trades"] == 5
    assert "advised:x" in books  # twins are books


def test_regime_cuts_stamps_thin_and_carries_completion(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    books = {b["book"]: b for b in doc["books"]}
    advised_gex = books["advised:x"]["dimensions"]["gex"]["buckets"]
    assert advised_gex[0]["bucket"] == "clustered" and advised_gex[0]["thin"] is True
    control_gex = {b["bucket"]: b for b in books["control"]["dimensions"]["gex"]["buckets"]}
    assert control_gex["diffuse"]["thin"] is False and control_gex["diffuse"]["completed"] == 3
    assert control_gex["untagged"]["trades"] == 1
    assert books["control"]["completion_rate"] == 0.8  # 4 flies of 5 rows; the strand is the fifth


def test_cross_tab_cells_pair_the_two_buckets(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    ct = doc["cross_tabs"][0]
    assert ct["dims"] == ["gex", "trend"]
    control = next(b for b in ct["books"] if b["book"] == "control")
    cells = {tuple(c["buckets"]): c for c in control["cells"]}
    assert cells[("diffuse", "flat")]["trades"] == 3 and cells[("diffuse", "flat")]["thin"] is False
    assert (
        cells[("diffuse", "up_from_open")]["completed"] == 0
        and cells[("diffuse", "up_from_open")]["thin"] is True
    )
    assert cells[("untagged", "untagged")]["trades"] == 1


def test_regime_cuts_is_byte_stable(conn):
    _seed(conn)
    a = json.dumps(analytics.regime_cuts(conn, session=DAY, generated_at="t"))
    b = json.dumps(analytics.regime_cuts(conn, session=DAY, generated_at="t"))
    assert a == b


def test_cli_write_leaves_the_dated_file_and_latest_and_no_tmp(conn, tmp_path, monkeypatch, capsys):
    _seed(conn)
    conn.close()
    monkeypatch.setattr(cli, "regime_cuts_dir", lambda: str(tmp_path / "out"))
    assert (
        cli.main(["--db", str(tmp_path / "paper_trades.db"), "regime-cuts", "--write", "--session", DAY]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["session"] == DAY and out["written"]["latest"]
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == [f"regime_cuts-{DAY}.json", "regime_cuts.json"]


def test_cli_backfill_writes_one_artifact_per_settled_session(conn, tmp_path, monkeypatch, capsys):
    _seed(conn)
    conn.close()
    monkeypatch.setattr(cli, "regime_cuts_dir", lambda: str(tmp_path / "out"))
    assert (
        cli.main(
            [
                "--db",
                str(tmp_path / "paper_trades.db"),
                "regime-cuts",
                "--write",
                "--backfill",
                "--since",
                "2026-09-15",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["sessions"] == ["2026-09-15", "2026-09-16", "2026-09-17"]
    assert rc.latest_session(tmp_path / "out") == "2026-09-17"
    assert rc.dated_sessions(tmp_path / "out") == ["2026-09-15", "2026-09-16", "2026-09-17"]


@pytest.mark.parametrize("dimension", sorted(analytics.REGIME_DIMENSIONS))
def test_regime_parser_offers_every_declared_dimension(dimension, tmp_path):
    """Shown to fail before the fix on center_offset and trend: the choices list was hand-kept
    and had not been touched since the two dimensions were added on 2026-08-04."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(tmp_path / "x.db"), "regime", "--dimension", dimension, "--help"])
    assert exc.value.code == 0
